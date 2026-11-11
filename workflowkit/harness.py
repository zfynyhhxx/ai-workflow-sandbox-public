"""原生 CLI 适配。API Worker 无外网、无真实 Key；写入由控制面校验后执行。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import tomllib
import time
from .store import GateError

ROOT=Path(__file__).resolve().parents[1]
NPM=Path.home()/'.npm-global'


def clean_env():
    names=('PATH','LANG','LC_ALL','TERM','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
           'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
    return {k:os.environ[k] for k in names if k in os.environ} | {'HOME':str(Path.home()),'PYTHONDONTWRITEBYTECODE':'1'}


def execute(command, *, cwd, prompt, env=None, timeout=180, log=None):
    started=time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process=subprocess.Popen(command,cwd=cwd,env=env or clean_env(),stdin=subprocess.PIPE,
                                 stdout=stdout,stderr=stderr,start_new_session=True)
        if log:
            start=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
            Path(log).with_name('process.json').write_text(json.dumps({'pid':process.pid,'start':start}))
        try:
            process.communicate(prompt.encode(),timeout=timeout)
        except (subprocess.TimeoutExpired,KeyboardInterrupt):
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL); process.wait()
            raise GateError('HARNESS_TIMEOUT_OR_CANCELLED')
        stdout.seek(0); output=stdout.read(2000000).decode(errors='replace')
        stderr.seek(0); errors=stderr.read(100000).decode(errors='replace')
    if log:
        Path(log).write_text(json.dumps({'returncode':process.returncode,'seconds':round(time.monotonic()-started,3),
                                        'stdout':output,'stderr':errors},ensure_ascii=False))
        os.chmod(log,0o600)
    if process.returncode:
        # 完整错误仅留私有运行记录，避免 SDK 将凭据/内部路径拼进公开异常。
        raise GateError(f'HARNESS_EXIT_{process.returncode}: {Path(log).name if log else "private log unavailable"}')
    return output


def api_worker(harness,prompt,gateway,folder,workspace=None,timeout=180,model='deepseek-flash',session_title=None):
    if harness not in ('claude','opencode'):
        raise GateError('未知 API Harness')
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    home=folder/'home'; home.mkdir(exist_ok=True,mode=0o700)
    workspace=Path(workspace or folder/'workspace'); workspace.mkdir(parents=True,exist_ok=True)
    settings={'permissions':{'allow':[], 'deny':['Agent','Task','Bash','Edit','Write','WebSearch','WebFetch']}}
    (folder/'claude-settings.json').write_text(json.dumps(settings))
    opencode={
        '$schema':'https://opencode.ai/config.json','update':'disable','share':'disabled',
        'model':'wf-deepseek/deepseek-flash','default_agent':'wf-stage',
        'permissions':[{'action':'*','resource':'*','effect':'deny'}],
        'agents':{'wf-stage':{'mode':'primary','description':'只从已批准上下文输出结构化产物',
                             'steps':2,'system':'不调用工具，只处理提供的上下文。'}},
        'providers':{'wf-deepseek':{'name':'Budgeted DeepSeek validation',
           'env':['DEEPSEEK_API_KEY'],'package':'@opencode/ai/providers/openai-compatible',
           'settings':{'baseURL':'','apiKey':''},
           'models':{'deepseek-flash':{'name':'DeepSeek Flash','limit':{'context':1000000,'output':4096}}}}}}
    if harness=='claude':
        command=['/opt/npm/bin/claude','--bare','-p','--model',model,'--tools','',
                 '--settings','/runtime/claude-settings.json','--output-format','json',
                 '--no-session-persistence']
    else:
        command=['/opt/npm/bin/opencode','run','--standalone','--agent','wf-stage',
                 '--model','wf-deepseek/deepseek-flash','--format','json',prompt]
        if session_title: command[-1:-1]=['--title',session_title]
    config={'harness':harness,'command':command,'token':gateway.token,'opencode':opencode,
            'env':{'ANTHROPIC_MODEL':model,'ANTHROPIC_DEFAULT_OPUS_MODEL':model,
                   'ANTHROPIC_DEFAULT_SONNET_MODEL':model,'ANTHROPIC_DEFAULT_HAIKU_MODEL':model,
                   'CLAUDE_CODE_SUBAGENT_MODEL':model,'CLAUDE_CODE_EFFORT_LEVEL':'low',
                   'DISABLE_NONESSENTIAL_TRAFFIC':'1','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1'}}
    launch=folder/'launch.json'; launch.write_text(json.dumps(config)); os.chmod(launch,0o600)
    args=['bwrap','--unshare-all','--die-with-parent','--new-session',
          '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
          '--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
          '--ro-bind','/etc/ssl','/etc/ssl','--ro-bind',str(NPM),'/opt/npm',
          '--bind',str(home),'/home/worker','--ro-bind',str(workspace),'/workspace',
          '--dir','/broker','--ro-bind',str(gateway.path),'/broker/api.sock',
          '--dir','/runtime','--ro-bind',str(launch),'/runtime/launch.json',
          '--ro-bind',str(folder/'claude-settings.json'),'/runtime/claude-settings.json',
          '--ro-bind',str(ROOT/'workflowkit/bridge.py'),'/runtime/bridge.py',
          '--clearenv','--setenv','HOME','/home/worker','--setenv','PATH','/opt/npm/bin:/usr/bin:/bin',
          '--setenv','LANG','C.UTF-8','--setenv','XDG_CONFIG_HOME','/home/worker/.config',
          '--setenv','XDG_DATA_HOME','/home/worker/.local/share','--chdir','/workspace',
          '/usr/bin/python3','/runtime/bridge.py']
    output=execute(args,cwd=ROOT,prompt=prompt if harness=='claude' else '',
                   timeout=timeout,log=folder/'events.json')
    metadata={'harness':harness,'requested_model':model,'auth_channel':'metered API gateway','actual_model':None}
    if harness=='claude':
        parsed=json.loads(output)
        if parsed.get('is_error'):
            raise GateError('CLAUDE_PROVIDER_ERROR; see private events.json')
        text=parsed.get('result')
        metadata['reported_models']=list(parsed.get('modelUsage',{}))
    else:
        texts=[]; session_ids=set()
        for line in output.splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            if isinstance(event.get('sessionID'),str): session_ids.add(event['sessionID'])
            if event.get('type')=='error':
                raise GateError('OPENCODE_PROVIDER_ERROR; see private events.json')
            part=event.get('part',{})
            if event.get('type')=='text' and isinstance(part.get('text'),str):
                texts.append(part['text'])
        text='\n'.join(texts)
        if len(session_ids)!=1: raise GateError('OPENCODE_SESSION_ID_AMBIGUOUS')
        metadata.update(session_id=session_ids.pop(),title=session_title,updated=int(time.time()*1000))
    if not isinstance(text,str) or not text.strip():
        raise GateError('EMPTY_HARNESS_RESPONSE; inspect native event protocol')
    (folder/'answer.txt').write_text(text); os.chmod(folder/'answer.txt',0o600)
    (folder/'metadata.json').write_text(json.dumps(metadata)); os.chmod(folder/'metadata.json',0o600)
    return text


def codex_worker(prompt,folder,workspace=None,timeout=180,model=None):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    final=folder/'answer.txt'
    # 只读模型产物模式：不让独立 Codex 会话执行 shell、访问 MCP/Apps 或派生 Agent。
    args=[str(NPM/'bin/codex'),'--ask-for-approval','never','exec','--sandbox','read-only',
          '--disable','shell_tool','--disable','apps','--disable','multi_agent',
          '-c','mcp_servers.codex_apps.enabled=false','-c','mcp_servers.node_repl.enabled=false',
          '-c','web_search="disabled"','--cd',str(workspace or ROOT),
          '--json','--output-last-message',str(final),'-']
    config_path=Path.home()/'.codex/config.toml'
    config=tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    for name in config.get('mcp_servers',{}):
        if name not in ('codex_apps','node_repl'):
            args[-1:-1]=['-c','mcp_servers.'+json.dumps(name)+'.enabled=false']
    if model: args[-1:-1]=['--model',model]
    output=execute(args,cwd=ROOT,prompt=prompt,timeout=timeout,log=folder/'events.json')
    metadata={'harness':'codex','provider':'openai','configured_model':model or config.get('model'),'actual_model':None,'auth':'existing ChatGPT login'}
    for line in output.splitlines():
        try: event=json.loads(line)
        except ValueError: continue
        if event.get('type')=='thread.started': metadata['thread_id']=event.get('thread_id')
        if event.get('type')=='turn.completed': metadata['usage']=event.get('usage')
    identifier=metadata.get('thread_id')
    if identifier:
        for path in (Path.home()/'.codex/sessions').rglob('*'+identifier+'*.jsonl'):
            for line in path.read_text().splitlines():
                try: event=json.loads(line)
                except ValueError: continue
                if event.get('type')=='turn_context' and event.get('payload',{}).get('model'):
                    metadata['actual_model']=event['payload']['model']; break
            break
    (folder/'metadata.json').write_text(json.dumps(metadata)); os.chmod(folder/'metadata.json',0o600)
    text=final.read_text(); os.chmod(final,0o600)
    if not text.strip(): raise GateError('EMPTY_CODEX_RESPONSE')
    return text
