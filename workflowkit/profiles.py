"""正式配置直接引用原起步包；测试覆盖只存在于显式 validation 模式。"""
import copy
from datetime import date,datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from .policy import STARTER
from .store import GateError


def profiles(mode):
    value=copy.deepcopy(json.loads((STARTER/'workflow.json').read_text())['profiles'])
    if mode=='validation':
        overlay=json.loads((Path(__file__).resolve().parents[1]/'config/validation.json').read_text())
        for name,changes in overlay['overrides'].items(): value[name].update(changes)
    elif mode!='production': raise GateError('未知运行模式')
    return value


def anthropic_account():
    path=Path.home()/'.config/ai-workflow/anthropic.json'
    if not path.is_file() or path.is_symlink():
        raise GateError('正式模式需要操作员配置独立 Anthropic 账户/完整模型 ID/官方定价；不回退 DeepSeek')
    value=json.loads(path.read_text())
    required={'provider','model','context_tokens','input_usd_per_million','output_usd_per_million','pricing_source','checked'}
    if set(value)!=required or value['provider']!='anthropic' or not value['model'].startswith('claude-'):
        raise GateError('正式 Anthropic 账户配置无效')
    if not value['pricing_source'].startswith('https://platform.claude.com/'):
        raise GateError('正式定价必须来自 Anthropic 官方来源')
    age=(datetime.now(ZoneInfo('Asia/Shanghai')).date()-date.fromisoformat(value['checked'])).days
    if not 0<=age<=7: raise GateError('正式定价超过七天，请操作员核实后更新日期与费率')
    return value
