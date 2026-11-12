"""任务数据不是指令或授权；批准绑定任务、基准和有效项目规则。"""
import importlib.util
import fnmatch
import json
from pathlib import Path,PurePosixPath
from .store import GateError,digest

STARTER=Path.home()/'.local/share/ai-workflow/AI-Workflow-Starter-2026-11-11'
CONTROL_PATHS=('workflowkit/*','scripts/*','tests/*','.workflow/*','config/*','vendor/*',
               'agent-orchestrator.yaml','CLAUDE.md','opencode.json','docs/adr/*','.specify/memory/*')


def safe_path(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:
        raise GateError('无效相对路径')
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in ('..','.git','') for x in p.parts) or str(p)!=value:
        raise GateError('拒绝路径穿越、Git 元数据或非规范路径')
    if any(x in ('secrets','.ssh','auth.json','credentials') or x=='.env' or x.startswith('.env.') for x in p.parts):
        raise GateError('凭据路径不得作为模型任务源码')
    return p


def read_source(root,paths):
    root=Path(root)
    result={}
    for name in paths:
        safe_path(name); target=root/name
        if any(p.is_symlink() for p in [target,*target.parents] if p.is_relative_to(root)):
            raise GateError('拒绝通过符号链接读取模型上下文')
        if target.exists() and not target.is_file(): raise GateError('批准源码目标不是普通文件')
        result[name]=target.read_text() if target.is_file() else None
    return result


def parse_task(body):
    if not isinstance(body,str) or len(body)>50000:
        raise GateError('任务正文为空或过大')
    if body.startswith('```json\n') and body.endswith('\n```'):
        body=body[8:-4]
    task=json.loads(body)
    expected={'id','role','summary','requirements_clear','bounded','modules','paths','risk_tags',
              'attempts','acceptance','depends_on','acceptance_template'}
    if not isinstance(task,dict) or set(task)-expected:
        raise GateError('任务含未知字段；不能从 Issue 注入命令或权限')
    required={'id','role','summary','requirements_clear','bounded','modules','paths','risk_tags','attempts','acceptance'}
    if required-set(task):
        raise ValueError('缺少必需任务字段')
    if task.get('role')!='Builder' or task.get('attempts')!=0:
        raise GateError('入口只接受初始 Builder 任务；重试次数由账本管理')
    for path in task.get('paths',[]): safe_path(path)
    deps=task.get('depends_on',[])
    if not isinstance(deps,list) or any(type(x) is not int or x<=0 for x in deps):
        raise GateError('无效依赖编号')
    route(task)
    return task


def route(task,role=None,attempts=0):
    spec=importlib.util.spec_from_file_location('starter_router',STARTER/'workflowctl.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    config=json.loads((STARTER/'workflow.json').read_text())
    data=dict(task,role=role or task['role'],attempts=0)
    result=module.route(data,config)
    if data['role']!='Knowledge' and any(fnmatch.fnmatchcase(p,pattern) for p in data['paths'] for pattern in CONTROL_PATHS):
        result['tier']='T1'
        if '高风险任务需要人工批准计划' not in result['holds']:
            result['holds'].append('高风险任务需要人工批准计划')
    if attempts>=3: raise GateError('RETRY_LIMIT_REACHED')
    if result['role']!='Knowledge':
        index=min(2,['T3','T2','T1'].index(result['tier'])+attempts)
        result['tier']=['T3','T2','T1'][index]
        result['profile']=config['tiers'][result['tier']]
    return result


def rule_hash(repo):
    repo=Path(repo)
    paths=[repo/'AGENTS.md',repo/'.workflow/project.json',repo/'.specify/memory/constitution.md']
    paths+=list((repo/'workflowkit').glob('*.py'))
    paths+=list((repo/'.workflow/acceptance').glob('*.py'))
    paths+=list((repo/'config').glob('*.json'))
    paths+=list((repo/'scripts').glob('*.py'))+list((repo/'scripts').glob('*.cjs'))
    paths+=list((repo/'docs').glob('spec.md'))+list((repo/'docs/adr').glob('*.md'))
    paths+=list((STARTER/'roles').glob('*.md'))+[STARTER/'workflow.json']
    return digest({str(p.relative_to(repo)) if p.is_relative_to(repo) else p.name:p.read_text() for p in sorted(paths)})


def role_prompt(role,task,context):
    contract=(STARTER/'roles'/f'{role}.md').read_text()
    if role=='Reviewer':
        contract+=('\n审查产物协议：approved 只是审查建议；findings 仅包含未解决、可操作的缺陷或证据缺口。'
                   '任务身份、静态通过说明、验证结果、风险边界和下一步放入 report，不混入 findings。'
                   '没有未解决问题时 findings 为 []；缺少必要证据仍应拒绝，不得为了通过而忽略问题。\n')
    return (contract+'\n\n本轮为已授权的隔离工作流验证；只处理下方提供的数据，不调用工具、不得批准自己。\n'
            '只输出一个 JSON 对象，不加 Markdown 或其他解释。\n'
            +json.dumps({'task_data':task,'context':context},ensure_ascii=False))


def object_answer(text):
    text=text.strip()
    if text.startswith('```json\n') and text.endswith('\n```'): text=text[8:-4]
    value=json.loads(text)
    if not isinstance(value,dict): raise GateError('角色输出必须是 JSON 对象')
    return value


def validate_artifact(role,value):
    def text(key):
        return isinstance(value.get(key),str) and bool(value[key].strip())
    def strings(key,nonempty=False):
        data=value.get(key)
        return isinstance(data,list) and (bool(data) or not nonempty) and all(isinstance(x,str) and x.strip() for x in data)
    valid={'Director':lambda:text('phase') and text('next_step'),
           'Planner':lambda:text('summary') and strings('steps',True) and strings('risks'),
           'Builder':lambda:text('summary') and isinstance(value.get('files'),list) and bool(value['files']),
           'Reviewer':lambda:type(value.get('approved')) is bool and strings('findings'),
           'Knowledge':lambda:type(value.get('create')) is bool and isinstance(value.get('body'),str) and isinstance(value.get('title'),str)}
    if role not in valid or not valid[role](): raise GateError('角色产物 Schema 不满足合同：'+role)
    return value
