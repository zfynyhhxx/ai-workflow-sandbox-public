"""以原生 AO 插件/tmux 启动原生 CLI 适配阶段；不向模型进程提供宿主凭据。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import signal
from .store import Store,GateError,digest
from .harness import api_worker,codex_worker,clean_env

ROOT=Path(__file__).resolve().parents[1]
STATE=Path.home()/'.local/state/ai-workflow/runtime'
BIN=Path.home()/'.local/share/ai-workflow/overrides/ao-stage-bin'


def environment():
    env=clean_env()
    env['PATH']=str(BIN)+os.pathsep+env.get('PATH','/usr/bin:/bin')
    package=Path(shutil.which('ao',path=env['PATH'])).resolve().parent.parent
    config=json.loads((ROOT/'config/ao-compat.json').read_text())
    if json.loads((package/'package.json').read_text()).get('version')!=config['ao_version']:
        raise GateError('AO_VERSION_CHANGED: 重新核对官方包及兼容合同，禁止静默升级')
    env['WF_AO_SUPERVISOR']=str(package/'node_modules/@aoagents/ao-cli/dist/lib/project-supervisor.js')
    env['WF_AO_OPENCODE_PLUGIN']=str(package/'node_modules/@aoagents/ao-plugin-agent-opencode/dist/index.js')
    env['NODE_OPTIONS']=' '.join('--require='+json.dumps(str(ROOT/'scripts'/name)) for name in ('ao-loopback.cjs','ao-compat.cjs'))
    return env


def install_adapters():
    BIN.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in ('codex','claude','opencode'):
        path=BIN/name
        content='#!/bin/sh\nexec /usr/bin/python3 '+repr(str(ROOT/'scripts/ao_agent.py'))+' '+name+' "$@"\n'
        if path.exists() and path.read_text()!=content:
            raise GateError('已有其他 AO 启动适配器，拒绝覆盖：'+name)
        if not path.exists():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700)
            with os.fdopen(fd,'w') as stream: stream.write(content)
        # AO 只会在这些受审的前缀之前加入本身的 git/gh 包装目录。
        effective=str(Path.home()/'.ao/bin')+':/usr/local/bin:'+environment()['PATH']
        if shutil.which(name,path=effective)!=str(path): raise GateError('AO PATH 不会使用受控启动器：'+name)


def call(args,timeout=120):
    result=subprocess.run(['ao',*args],cwd=ROOT,env=environment(),capture_output=True,text=True,timeout=timeout)
    if result.returncode: raise GateError('AO_COMMAND_FAILED: '+result.stderr[-1500:])
    return result.stdout


def sessions():
    data=json.loads(call(['session','ls','--project','workflow-sandbox','--include-terminated','--json']))
    if isinstance(data,dict): data=data.get('data',data.get('sessions'))
    if not isinstance(data,list) or any(not isinstance(x,dict) or 'id' not in x for x in data):
        raise GateError('AO_SESSION_PROTOCOL_CHANGED')
    return data


def owned_processes(record):
    if not record or Path(record['configPath']).resolve()!=ROOT/'agent-orchestrator.yaml' or not set(record['projects'])<={'workflow-sandbox'}:
        return []
    pid=record['pid']; proc=Path(f'/proc/{pid}')
    if not proc.exists(): return []
    if (proc/'cwd').resolve()!=ROOT or b'ao\x00start\x00' not in (proc/'cmdline').read_bytes():
        raise GateError('AO_PID_IDENTITY_CHANGED: 未发送信号')
    records={}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            data=path.read_text().rsplit(')',1)[1].split()
            records[int(path.parent.name)]={'pid':int(path.parent.name),'parent':int(data[1]),'start':data[19]}
        except (FileNotFoundError,ProcessLookupError,PermissionError): pass
    selected={pid}
    while True:
        more={p for p,r in records.items() if r['parent'] in selected}
        if more<=selected: break
        selected|=more
    return [records[p] for p in selected if p in records]


def terminate_owned(records):
    for record in records:
        try:
            actual=Path(f'/proc/{record["pid"]}/stat').read_text().rsplit(')',1)[1].split()
            if actual[19]==record['start'] and actual[0]!='Z': os.kill(record['pid'],signal.SIGTERM)
        except (FileNotFoundError,ProcessLookupError): pass


def run_stage(harness,prompt,gateway,folder,model='deepseek-flash'):
    install_adapters()
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(STATE)
    job={'harness':harness,'prompt':prompt,'model':model,'folder':str(folder),
         'socket':str(gateway.path),'token':gateway.token}
    job['binding']=digest(job)
    job_path=folder/'ao-job.json'; job_path.write_text(json.dumps(job)); os.chmod(job_path,0o600)
    pointer=STATE/'ao-current.json'
    result=folder/'ao-result.json'; error=folder/'ao-error.json'
    with store.lock('ao-launch',folder.parent.name,ttl=600):
        pointer.write_text(json.dumps({'path':str(job_path),'binding':job['binding']})); os.chmod(pointer,0o600)
        plugin={'codex':'codex','claude':'claude-code','opencode':'opencode'}[harness]
        try:
            before={x['id'] for x in sessions()}
            output=call(['spawn','--agent',plugin,'--prompt','受控工作流阶段 '+job['binding'][:12]+'。仅执行控制面已冻结的产物协议，不自行修改或操作远端。'])
            (folder/'ao-launch.txt').write_text(output)
            deadline=time.monotonic()+240
            while not result.exists() and not error.exists() and time.monotonic()<deadline:
                time.sleep(1)
            current=sessions(); created=[x for x in current if x['id'] not in before]
            if len(created)!=1: raise GateError('AO_SESSION_IDENTITY_AMBIGUOUS')
            (folder/'ao-session.json').write_text(json.dumps(created[0],ensure_ascii=False))
            if error.exists(): raise GateError(json.loads(error.read_text())['error'])
            if not result.exists(): raise GateError('AO_STAGE_TIMEOUT; 先检查自己的会话，不重复付费派发')
            record=json.loads(result.read_text())
            if record['binding']!=job['binding']: raise GateError('AO 产物绑定不匹配')
            call(['report','completed','--session',created[0]['id'],'--note','仅完成隔离 CLI 产物阶段；不代表业务验收或人工合并完成'])
            return record['answer']
        finally:
            pointer.unlink(missing_ok=True)


def agent_main(harness,args):
    if harness=='opencode' and args==['version']: args=['--version']
    if args in (['--version'],['-V'],['-v'],['--help'],['-h']):
        return subprocess.call([str(Path.home()/'.npm-global/bin'/harness),*args],env=clean_env())
    if harness=='opencode' and args==['session','list','--format','json']:
        entries=[]
        for reference in (STATE/'ao-sessions').glob('*'):
            ref=json.loads(reference.read_text()); job=json.loads(Path(ref['path']).read_text())
            binding=job.pop('binding')
            if binding!=ref['binding'] or binding!=digest(job) or job['harness']!='opencode': continue
            metadata=Path(job['folder'])/'native/metadata.json'
            if metadata.is_file():
                native=json.loads(metadata.read_text())
                entries.append({'id':native['session_id'],'title':native['title'],'updated':native['updated'],
                                'directory':str(Path(job['folder'])/'native/workspace')})
        print(json.dumps(entries)); return 0
    pointer=STATE/'ao-current.json'
    session=os.environ.get('AO_SESSION_ID','')
    if not session: raise GateError('NO_AO_SESSION; 元数据探测不得启动模型')
    if session and (not session.replace('-','').replace('_','').isalnum()): raise GateError('无效 AO 会话标识')
    mapping=STATE/'ao-sessions'/session if session else None
    current=json.loads(pointer.read_text()) if pointer.is_file() else None
    restoring=not current or (mapping and mapping.is_file() and json.loads(mapping.read_text())!=current)
    if restoring:
        if not mapping or not mapping.is_file(): raise GateError('NO_APPROVED_AO_JOB; 不退回不受控 CLI')
        reference=json.loads(mapping.read_text())
    else:
        reference=current
    job=json.loads(Path(reference['path']).read_text())
    binding=job.pop('binding')
    if binding!=reference['binding'] or binding!=digest(job) or job['harness']!=harness:
        raise GateError('AO_JOB_BINDING_REJECTED')
    folder=Path(job['folder']); result=folder/'ao-result.json'
    if result.exists():
        cached=json.loads(result.read_text())
        if cached['binding']!=binding: raise GateError('AO 缓存输入变化')
        print(cached['answer'],flush=True); return 0
    if restoring: raise GateError('未完成的阶段须由 recover 重新检查批准，禁止恢复时重复付费')
    if not any(binding[:12] in arg for arg in args):
        raise GateError('AO_PROMPT_BINDING_REJECTED')
    if mapping:
        mapping.parent.mkdir(exist_ok=True,mode=0o700)
        mapping.write_text(json.dumps(reference)); os.chmod(mapping,0o600)
    with Store(STATE).lock('ao-job-'+binding,session or str(os.getpid()),ttl=400):
        try:
            if harness=='codex': answer=codex_worker(job['prompt'],folder/'native',workspace=Path.cwd(),model=job.get('model'))
            else:
                from types import SimpleNamespace
                gateway=SimpleNamespace(path=Path(job['socket']),token=job['token'])
                answer=api_worker(harness,job['prompt'],gateway,folder/'native',model=job['model'],session_title='AO:'+session)
            result.write_text(json.dumps({'binding':binding,'answer':answer},ensure_ascii=False)); os.chmod(result,0o600)
            print(answer,flush=True); return 0
        except (GateError,OSError,ValueError) as exc:
            (folder/'ao-error.json').write_text(json.dumps({'error':str(exc)}))
            raise
