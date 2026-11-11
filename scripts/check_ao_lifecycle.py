#!/usr/bin/env python3
"""零模型费用的真实 AO 冷启动/空闲/重启回归。必须先停止现有实例。"""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflowkit.ao import ROOT,STATE,call,sessions
from workflowkit.store import Store,GateError

def free(port):
    with socket.socket() as sock: return sock.connect_ex(('127.0.0.1',port))!=0

def wait_until(predicate,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.5)
    raise GateError('AO lifecycle regression timed out; inspect private evidence')

def active():
    return json.loads(call(['session','ls','--project','workflow-sandbox','--all','--json']))['data']

store=Store(STATE); before=store.balance()
if before['active_requests'] or (STATE/'ao-current.json').exists() or not free(3000) or not free(14801):
    raise SystemExit('请先停止现有实例与模型任务；本回归不终止或抢占其他工作')
folder=STATE/'runs'/('ao-lifecycle-'+str(time.time_ns())); folder.mkdir(mode=0o700)
running=Path.home()/'.agent-orchestrator/running.json'
results=[]
with store.lock('dispatcher',folder.name,ttl=600),store.lock('ao-launch',folder.name,ttl=600):
    for cycle in range(2):
        with (folder/f'start-{cycle}.log').open('w') as log:
            process=subprocess.Popen(['wf','ao','start'],cwd=ROOT,stdout=log,stderr=log)
            try:
                wait_until(lambda: running.is_file() and 'workflow-sandbox' in json.loads(running.read_text())['projects'] and not free(3000))
                if active(): raise GateError('发现非本测试活动会话，停止验收，不继续派发')
                if cycle==0:
                    # 覆盖 60 秒 supervisor reconcile，证明空闲后不会再剥离 worker。
                    time.sleep(65)
                    assert 'workflow-sandbox' in json.loads(running.read_text())['projects']
                    assert active()==[]
                prior={x['id'] for x in sessions()}
                output=call(['spawn','--agent','codex','--prompt','NO_MODEL_GUARD_PROBE: 无批准执行包，只验证拒绝启动模型。'])
                (folder/f'spawn-{cycle}.log').write_text(output)
                created=[x for x in sessions() if x['id'] not in prior]
                if len(created)!=1: raise GateError('本次探针会话身份不唯一')
                session=created[0]['id']
                def refused():
                    pane=subprocess.check_output(['tmux','capture-pane','-t',session,'-p','-S','-100'],text=True)
                    return 'NO_APPROVED_AO_JOB' in pane
                wait_until(refused)
                assert store.balance()==before
                results.append({'cycle':cycle,'zero_active_start':True,'idle_reconcile':cycle==0,
                                'native_spawn':session,'model_start':'guard refused','api_charge':0})
            finally:
                stop=subprocess.run(['wf','ao','stop'],cwd=ROOT,capture_output=True,text=True,timeout=120)
                (folder/f'stop-{cycle}.log').write_text(stop.stdout+stop.stderr)
                if stop.returncode: raise GateError('AO stop failed; inspect private evidence')
                process.wait(timeout=30)
                wait_until(lambda:free(3000) and free(14801))
record={'results':results,'before':before,'after':store.balance(),'evidence':str(folder)}
(folder/'evidence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
print(json.dumps(record,ensure_ascii=False,indent=2))
