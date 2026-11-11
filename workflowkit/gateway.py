"""只监听私有 Unix socket 的 DeepSeek 计量网关；不把真实 Key 传给 Worker。"""
import json
import os
from pathlib import Path
import secrets
import socketserver
import threading
import urllib.error
import urllib.request
from decimal import Decimal,ROUND_CEILING
from http.server import BaseHTTPRequestHandler
from .store import GateError

MODEL = 'deepseek-flash'
CONTEXT_TOKENS = 1048576
MAX_OUTPUT = 4096
# 2026-11-12 官方峰时上限：输入 cache miss $0.30/M、输出 $1.20/M。
# 每次先按整个上下文上限预留，避免用不精确的本地 tokenizer 承诺硬预算。
RESERVE = (CONTEXT_TOKENS*3 + MAX_OUTPUT*12 + 9)//10


def usage_of(raw, content_type):
    parts=[]
    if 'text/event-stream' in content_type:
        for line in raw.splitlines():
            if line.startswith(b'data: '):
                try:
                    parts.append(json.loads(line[6:]))
                except (ValueError,UnicodeError):
                    pass
    else:
        try:
            parts=[json.loads(raw)]
        except (ValueError,UnicodeError):
            pass
    incoming=outgoing=None
    models=set()
    for part in parts:
        reported=part.get('model') or part.get('message',{}).get('model')
        if isinstance(reported,str): models.add(reported)
        usage=part.get('usage') or part.get('message',{}).get('usage') or {}
        for key in ('input_tokens','prompt_tokens'):
            if key in usage:
                value=usage[key]
                if type(value) is not int or value < 0:
                    raise GateError('无效 Provider 输入用量')
                # Anthropic 输入缓存字段额外累加，全部按 cache miss 上限计费。
                for cache in ('cache_read_input_tokens','cache_creation_input_tokens'):
                    cached=usage.get(cache,0)
                    if type(cached) is not int or cached<0:
                        raise GateError('无效 Provider 缓存用量')
                    value+=cached
                incoming=max(incoming or 0,value)
        for key in ('output_tokens','completion_tokens'):
            if key in usage:
                value=usage[key]
                if type(value) is not int or value < 0:
                    raise GateError('无效 Provider 输出用量')
                outgoing=max(outgoing or 0,value)
    if incoming is None or outgoing is None:
        return None,None
    charge=(incoming*3+outgoing*12+9)//10
    return charge,{'input_tokens':incoming,'output_tokens':outgoing,'reported_models':sorted(models),
                   'accounting':'peak_cache_miss_upper_bound','actual_cost':None}


class Gateway:
    def __init__(self, store, folder, key_file, run, upstream=None, profile=None):
        self.store=store
        self.folder=Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.folder,0o700)
        self.path=self.folder/'api.sock'
        if self.path.exists():
            raise GateError('拒绝覆盖已有网关 socket')
        self.token=secrets.token_hex(32)
        self.upstream_lock=threading.Lock()
        self.run=run
        self.model=MODEL
        self.provider='deepseek'
        self.input_rate=Decimal('0.30'); self.output_rate=Decimal('1.20')
        self.context_tokens=CONTEXT_TOKENS
        self.paths={'/v1/chat/completions':'/chat/completions','/anthropic/v1/messages':'/anthropic/v1/messages'}
        default_upstream='https://api.deepseek.com'
        if profile:
            if profile.get('provider')!='anthropic' or not profile.get('model','').startswith('claude-'):
                raise GateError('无效的正式 Anthropic Profile')
            self.model=profile['model']
            self.provider='anthropic'
            self.input_rate=Decimal(profile['input_usd_per_million'])
            self.output_rate=Decimal(profile['output_usd_per_million'])
            self.context_tokens=profile['context_tokens']
            if not self.input_rate.is_finite() or not self.output_rate.is_finite() or min(self.input_rate,self.output_rate)<=0:
                raise GateError('无效定价')
            if type(self.context_tokens) is not int or self.context_tokens<1:
                raise GateError('无效上下文上限')
            self.paths={'/anthropic/v1/messages':'/v1/messages'}
            default_upstream='https://api.anthropic.com'
        self.upstream=upstream or default_upstream
        self.reservation=int((Decimal(self.context_tokens)*self.input_rate+MAX_OUTPUT*self.output_rate).to_integral_value(rounding=ROUND_CEILING))
        p=Path(key_file)
        if p.is_symlink() or p.stat().st_mode & 0o077:
            raise GateError('API 凭据必须是权限 600 的普通私有文件')
        self.key=p.read_text().strip()
        if not self.key:
            raise GateError('凭据为空')
        gateway=self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):
                pass

            def reply(self,status,data,kind='application/json'):
                self.send_response(status)
                self.send_header('Content-Type',kind)
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                # 先耗尽有界请求体，再发送拒绝；否则客户端尚在 sendall 时关闭
                # Unix socket 会竞态触发 BrokenPipe 而不是收到明确的拒绝状态。
                try: length=int(self.headers.get('Content-Length','0'))
                except ValueError:
                    self.reply(400,b'{"error":"invalid_content_length"}'); return
                if not 0 < length <= 512000:
                    self.reply(413,b'{"error":"body_size_limit"}'); return
                raw_request=self.rfile.read(length)
                received=self.headers.get('Authorization','').removeprefix('Bearer ') or self.headers.get('x-api-key','')
                if not secrets.compare_digest(received,gateway.token):
                    self.reply(403,b'{"error":"invalid_worker_capability"}'); return
                path=self.path.split('?',1)[0]
                if path not in gateway.paths:
                    self.reply(403,b'{"error":"endpoint_not_allowed"}'); return
                request_id=None
                acquired=False
                try:
                    body=json.loads(raw_request)
                    if body.get('model') not in (gateway.model,gateway.model+'[1m]'):
                        raise GateError('仅允许当前 Profile 中已定价的模型')
                    body['model']=gateway.model
                    requested=body.get('max_tokens') or body.get('max_completion_tokens') or MAX_OUTPUT
                    if type(requested) is not int or requested <= 0:
                        raise GateError('无效输出上限')
                    body.pop('max_completion_tokens',None)
                    body['max_tokens']=min(requested,MAX_OUTPUT)
                    for tool in body.get('tools',[]):
                        if tool.get('type','').startswith(('web_search','web_fetch','computer')):
                            raise GateError('禁止额外计费的托管工具')
                    if path.startswith('/v1/') and body.get('stream'):
                        body['stream_options']={'include_usage':True}
                    # 原生 SDK 有标题等后台请求；在网关排队而不是制造 429 重试。
                    # SQLite 仍独立限制跨网关/跨进程的实际付费并发。
                    if not gateway.upstream_lock.acquire(timeout=120): raise GateError('PAID_QUEUE_TIMEOUT')
                    acquired=True
                    request_id=gateway.store.reserve(gateway.run,gateway.reservation)
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+gateway.key,
                             'x-api-key':gateway.key,'anthropic-version':'2023-06-01'}
                    if gateway.provider=='anthropic': headers.pop('Authorization')
                    req=urllib.request.Request(gateway.upstream+gateway.paths[path],data=json.dumps(body).encode(),headers=headers)
                    # 禁止 redirect，凭据不能被上游重定向带到其他目的地。
                    class NoRedirect(urllib.request.HTTPRedirectHandler):
                        def redirect_request(self,*_): return None
                    opener=urllib.request.build_opener(NoRedirect)
                    with opener.open(req,timeout=120) as response:
                        kind=response.headers.get('Content-Type','application/json')
                        raw=response.read(16*1024*1024+1)
                        if len(raw)>16*1024*1024:
                            raise GateError('响应过大，保留预算预留')
                    charge,usage=usage_of(raw,kind)
                    if usage is not None:
                        charge=int((usage['input_tokens']*gateway.input_rate+usage['output_tokens']*gateway.output_rate).to_integral_value(rounding=ROUND_CEILING))
                        usage['accounting']='configured_rate_upper_bound'
                        usage.update(provider=gateway.provider,requested_model=gateway.model,auth_channel='private API key at metered gateway')
                    gateway.store.settle(request_id,charge,usage)
                    request_id=None
                    self.reply(200,raw,kind)
                except urllib.error.HTTPError as exc:
                    exc.close()
                    if request_id:
                        gateway.store.settle(request_id)
                    self.reply(exc.code,json.dumps({'error':'provider_http_error','status':exc.code}).encode())
                except (GateError,ValueError,OSError,TimeoutError) as exc:
                    if request_id:
                        try: gateway.store.settle(request_id)
                        except GateError: pass
                    # 不输出请求头、Key、Provider 正文或客户端请求内容。
                    message=str(exc) if isinstance(exc,GateError) else type(exc).__name__
                    gateway.store.event(gateway.run,'gateway_rejected',{'reason':message})
                    self.reply(429 if 'BUDGET' in message or 'CONCURRENCY' in message else 502,
                               json.dumps({'error':message}).encode())
                finally:
                    if acquired: gateway.upstream_lock.release()

        self.server=socketserver.ThreadingUnixStreamServer(str(self.path),Handler)
        self.server.daemon_threads=True
        os.chmod(self.path,0o600)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self,*_):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.path.unlink(missing_ok=True)
        self.key=''
