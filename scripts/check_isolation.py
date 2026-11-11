#!/usr/bin/env python3
"""真实执行 Worker 的相同 bwrap 挂载合同；无真实凭据、无模型消费。"""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflowkit.gateway import Gateway
from workflowkit.harness import api_worker,execute
from workflowkit.store import Store

PROBE='''
import json,os,socket
from pathlib import Path
assert not Path('/home/worker/.codex/auth.json').exists()
assert not Path('/home/worker/.config/gh/hosts.yml').exists()
assert not Path('/home/worker/.config/ai-workflow/secrets').exists()
for k in ('ANTHROPIC_API_KEY','DEEPSEEK_API_KEY','GITHUB_TOKEN','GH_TOKEN'):
    assert k not in os.environ
try:
    Path('/workspace/denied-write').write_text('must fail')
except OSError: pass
else: raise AssertionError('workspace unexpectedly writable')
s=socket.socket();s.settimeout(1)
assert s.connect_ex(('1.1.1.1',443))!=0
s.close()
assert Path('/broker/api.sock').exists()
print(json.dumps({'result':'ISOLATION_OK'}))
'''

def isolated(args,**kwargs):
    assert args[-2:]==['/usr/bin/python3','/runtime/bridge.py']
    return execute(args[:-2]+['/usr/bin/python3','-c',PROBE],**kwargs)

with tempfile.TemporaryDirectory(prefix='wf-isolation-') as tmp:
    root=Path(tmp); key=root/'dummy.key'; key.write_text('local-probe-not-a-provider-key'); key.chmod(0o600)
    store=Store(root/'state')
    with Gateway(store,root/'gateway',key,'isolation') as gateway:
        with patch('workflowkit.harness.execute',side_effect=isolated):
            assert api_worker('claude','no API request',gateway,root/'worker')=='ISOLATION_OK'
    assert store.balance()['accounted_microusd']==0
print(json.dumps({'isolation':'ISOLATION_OK','network':'denied','workspace':'read-only','host_credentials':'not mounted','api_requests':0}))
