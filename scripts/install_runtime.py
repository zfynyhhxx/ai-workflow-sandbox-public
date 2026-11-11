#!/usr/bin/env python3
"""只安装用户级入口和缺失的起步输入，不覆盖全局规则、已有工具或凭据。"""
from pathlib import Path
import os
import shutil

root=Path(__file__).resolve().parents[1]
folder=Path.home()/'.local/bin'; folder.mkdir(parents=True,exist_ok=True)
target=folder/'wf'
content='#!/bin/sh\nexec /usr/bin/python3 '+repr(str(root/'scripts/wf.py'))+' "$@"\n'
if target.exists() or target.is_symlink():
    if target.is_symlink() or target.read_text()!=content:
        raise SystemExit('已有 wf 入口，拒绝覆盖')
else:
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700)
    with os.fdopen(fd,'w') as stream: stream.write(content)
starter=Path.home()/'.local/share/ai-workflow/AI-Workflow-Starter-2026-11-11'
if not starter.exists(): shutil.copytree(root/'vendor/starter',starter)
print('用户级 wf 入口已就位；原全局规则、工具、登录与已有起步包保持不变。')
