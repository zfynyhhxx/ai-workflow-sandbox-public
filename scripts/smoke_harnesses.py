#!/usr/bin/env python3
"""真实原生 CLI 连通验证；所有付费请求复用同一份 1 USD 事务账本。"""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflowkit.store import Store,GateError
from workflowkit.gateway import Gateway
from workflowkit.harness import api_worker,codex_worker

root=Path.home()/'.local/state/ai-workflow/runtime'
store=Store(root)
run='smoke-'+str(time.time_ns())
folder=root/'runs'/run
folder.mkdir(parents=True,mode=0o700)
key=Path.home()/'.config/ai-workflow/secrets/deepseek.key'
try:
    with Gateway(store,folder/'gateway',key,run) as gateway:
        for harness in ('claude','opencode'):
            text=api_worker(harness,'只回复 WORKFLOW_OK，不使用任何工具，不添加解释。',gateway,folder/harness,timeout=180)
            if text.strip()!='WORKFLOW_OK':
                raise GateError(harness+' unexpected_response; inspect private answer.txt')
            print(json.dumps({'harness':harness,'provider':'deepseek','model':'deepseek-flash','result':'WORKFLOW_OK',
                              'budget':store.balance()},ensure_ascii=False),flush=True)
    text=codex_worker('只回复 WORKFLOW_OK，不使用任何工具，不读取文件。',folder/'codex',timeout=180)
    if text.strip()!='WORKFLOW_OK': raise GateError('Codex unexpected_response')
    print(json.dumps({'harness':'codex','auth':'existing ChatGPT login','result':'WORKFLOW_OK'},ensure_ascii=False),flush=True)
except (GateError,OSError,ValueError) as exc:
    print(json.dumps({'status':'blocked','reason':str(exc),'run':str(folder),'budget':store.balance()},ensure_ascii=False),flush=True)
    raise SystemExit(2)
