#!/usr/bin/env python3
"""正式模式的操作员输入入口；本轮验证不执行它，不替用户猜模型 ID 或定价。"""
from datetime import datetime
from decimal import Decimal
import getpass
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

if not os.isatty(0): raise SystemExit('仅允许操作员在交互终端录入')
folder=Path.home()/'.config/ai-workflow'; secrets=folder/'secrets'
secrets.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(secrets,0o700)
keyfile=secrets/'anthropic.key'; account=folder/'anthropic.json'
if any(p.exists() or p.is_symlink() for p in (keyfile,account)):
    raise SystemExit('已有正式配置，拒绝覆盖；先由操作员核对差异')
model=input('账户实际可用的完整 Claude 模型 ID（claude-...）: ').strip()
context=int(input('官方上下文 token 上限: ').strip())
in_rate=str(Decimal(input('官方每百万输入 tokens 的 USD 上限（cache miss）: ').strip()))
out_rate=str(Decimal(input('官方每百万输出 tokens 的 USD 上限: ').strip()))
if not model.startswith('claude-') or context<1 or min(Decimal(in_rate),Decimal(out_rate))<=0:
    raise SystemExit('无效账户/定价输入，未保存')
key=getpass.getpass('正式 Anthropic API Key（不回显）: ').strip()
if not key or '\n' in key: raise SystemExit('未收到有效 Key')
value={'provider':'anthropic','model':model,'context_tokens':context,
       'input_usd_per_million':in_rate,'output_usd_per_million':out_rate,
       'pricing_source':'https://platform.claude.com/docs/en/about-claude/pricing',
       'checked':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()}
for target,content in ((keyfile,key),(account,json.dumps(value))):
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f: f.write(content)
print('正式账户配置已保存在本机私有目录；没有发起 API 请求或修改全局设置。')
