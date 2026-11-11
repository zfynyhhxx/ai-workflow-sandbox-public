#!/usr/bin/env python3
"""拒绝常见真实凭据模式和禁止产物；不输出命中的正文。"""
from pathlib import Path
import re
import subprocess

paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
failed=[]
for name in paths:
    if not name: continue
    path=Path(name)
    if path.is_symlink():
        failed.append(name+': symlink'); continue
    if not path.is_file(): continue
    if any(x in path.parts for x in ('__pycache__','.venv','secrets')) or path.name in ('auth.json','.env'):
        failed.append(name+': forbidden artifact'); continue
    text=path.read_text(errors='replace')
    patterns=(r'\bsk-ant-[A-Za-z0-9_-]{20,}',r'\bsk-[A-Za-z0-9]{24,}',r'\bghp_[A-Za-z0-9]{20,}',r'\bgithub_pat_[A-Za-z0-9_]{30,}',r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')
    if any(re.search(p,text) for p in patterns): failed.append(name+': credential pattern')
if failed: raise SystemExit('\n'.join(failed))
print('secret_scan: no matching credentials or forbidden artifacts')
