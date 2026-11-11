"""连接既有工具的受控流水线；批准、模型建议、CI 和人类合并不是同一状态。"""
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
from .store import Store,GateError,digest
from .policy import parse_task,route,rule_hash,role_prompt,object_answer,safe_path,STARTER,validate_artifact,read_source
from .gateway import Gateway
from .harness import api_worker,codex_worker,execute,clean_env
from .profiles import profiles,anthropic_account
from .github import Portfolio

ROOT=Path(__file__).resolve().parents[1]
STATE=Path.home()/'.local/state/ai-workflow/runtime'
TERMINAL={'WAITING_HUMAN','DONE','CANCELLED'}


def command(args,cwd=ROOT,timeout=120):
    try:
        r=subprocess.run(args,cwd=cwd,env=clean_env(),capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise GateError('COMMAND_TIMEOUT: 外部写入可能已发生，恢复前先查状态') from exc
    if r.returncode:
        raise GateError(f'COMMAND_FAILED: {args[0]} {args[1] if len(args)>1 else ""}: {r.stderr[:1000]}')
    return r.stdout.strip()


def git(repo,*args): return command(['git',*args],repo)


def process_alive(record):
    try:
        actual=Path(f'/proc/{record["pid"]}/stat').read_text().rsplit(')',1)[1].split()[19]
        return actual==record['start']
    except (FileNotFoundError,ProcessLookupError):
        return False


def checks_successful(checks):
    if not checks:
        return False
    for check in checks:
        if 'conclusion' in check:
            if check.get('status')!='COMPLETED' or check['conclusion']!='SUCCESS': return False
        elif check.get('state')!='SUCCESS':
            return False
    return True


class Engine:
    def __init__(self,repo=ROOT,state=STATE,mode='production'):
        self.repo=Path(repo).resolve(); self.store=Store(state)
        self.project=json.loads((self.repo/'.workflow/project.json').read_text())
        if mode not in ('validation','production'): raise GateError('无效 Profile 模式')
        self.mode=mode
        self.portfolio=None; self.item=None

    def issue(self,number):
        issue=json.loads(command(['gh','issue','view',str(number),'-R',self.project['repository'],
                                  '--json','number,title,body,state,url']))
        if issue['state']!='OPEN': raise GateError('Issue 已关闭，不能重新执行')
        return issue,parse_task(issue['body'])

    def subject(self,task):
        result={'task':task,'rules':rule_hash(self.repo),
                'base':git(self.repo,'rev-parse',self.project['base_branch']),
                'mode':self.mode,'project':self.project['repository'],'profiles':profiles(self.mode),'budget_cap_microusd':self.store.balance()['cap_microusd']}
        if self.mode=='production': result['anthropic_account']=anthropic_account()
        return result

    def latest(self,number):
        for row in self.store.runs():
            data=json.loads(row['payload'])
            if row['issue']==number and data.get('repository')==self.project['repository']:
                return row,data
        raise GateError('没有对应的本仓库运行记录')

    def approve(self,number,ttl=3600,plan=False,expected_subject=None):
        _,task=self.issue(number); subject=self.subject(task)
        if plan:
            _,data=self.latest(number)
            if data['subject']!=subject: raise GateError('计划的输入或规则已变化')
            subject=subject|{'plan_digest':digest(data['plan'])}
        if expected_subject is not None and expected_subject!=subject:
            raise GateError('APPROVAL_INPUT_CHANGED: 审阅期间任务/规则/基准/计划发生变化，必须重新展示并批准')
        return self.store.approve(subject,'plan' if plan else 'run',ttl)

    def project_update(self,**values):
        if self.portfolio and self.item: self.portfolio.update(self.item,**values)

    def stage(self,role,task,context,gateway,folder,attempts=0):
        decision=route(task,role,attempts)
        profile=profiles(self.mode)[decision['profile']]
        harness=profile['harness']; provider=profile['provider']
        model=profile.get('model')
        if provider=='deepseek':
            model=model.rsplit('/',1)[-1] if isinstance(model,str) else None
            if model!='deepseek-flash': raise GateError('UNPRICED_MODEL: 不擅自替换或使用未定价模型')
        context={'effective_project_rules':(self.repo/'AGENTS.md').read_text(),
                 'artifact_protocol':context,'notice':'approved 仅是独立 Reviewer 的审查建议，不代替人工授权或合并'}
        prompt=role_prompt(role,task,context)
        folder=Path(folder); folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        binding=digest([prompt,self.mode,profiles(self.mode)])
        cached=folder/'result.json'
        if cached.exists():
            record=json.loads(cached.read_text())
            if record['binding']!=binding: raise GateError('阶段输入变化，拒绝复用旧产物')
            return validate_artifact(role,record['result'])
        counter=folder/'invocations.json'
        count=json.loads(counter.read_text()) if counter.exists() else 0
        if count>=3: raise GateError('STAGE_RETRY_LIMIT_REACHED')
        counter.write_text(json.dumps(count+1))
        invocation=folder/f'invocation-{count}'
        identity={'role':role,'tier':decision['tier'],'profile':decision['profile'],'harness':harness,
                  'provider':provider,'mode':self.mode,'requested_model':profile.get('model')}
        self.store.event(folder.parent.name,'dispatch',identity)
        self.project_update(Role=role,Tier=decision['tier'],Profile=f'{harness}/{provider} ({self.mode})')
        gateway.run=folder.parent.name+'/'+role+'/'+str(attempts)
        use_ao=self.project.get('execution_backend')=='ao'
        if use_ao:
            from .ao import run_stage
        if harness=='codex':
            answer=run_stage(harness,prompt,gateway,invocation,model=model) if use_ao else codex_worker(prompt,invocation,workspace=self.repo,model=model)
        elif provider=='deepseek':
            answer=run_stage(harness,prompt,gateway,invocation,model=model) if use_ao else api_worker(harness,prompt,gateway,invocation,model=model)
        elif harness=='claude' and provider=='anthropic':
            account=anthropic_account()
            secret=Path.home()/'.config/ai-workflow/secrets/anthropic.key'
            with Gateway(self.store,invocation/'anthropic-gateway',secret,folder.parent.name+'/'+role,profile=account) as formal:
                answer=run_stage('claude',prompt,formal,invocation,model=account['model']) if use_ao else api_worker('claude',prompt,formal,invocation,model=account['model'])
        else:
            raise GateError('UNSUPPORTED_APPROVED_PROFILE')
        result=validate_artifact(role,object_answer(answer))
        cached.write_text(json.dumps({'binding':binding,'result':result},ensure_ascii=False)); os.chmod(cached,0o600)
        return result

    def apply(self,worktree,task,proposal):
        worktree=Path(worktree)
        files=proposal.get('files')
        if not isinstance(files,list) or not files or len(files)>20:
            raise GateError('Builder 必须输出 1–20 项结构化文件变更')
        names=[]; prepared=[]
        # 先完整验证再写入，拒绝部分合法变更夹带越界文件。
        for item in files:
            if not isinstance(item,dict) or set(item)!={'path','content'}:
                raise GateError('文件变更 Schema 不匹配')
            path=item['path']; safe_path(path)
            if path not in task['paths'] or path in names:
                raise GateError('Builder 修改了未批准路径或重复路径')
            content=item['content']
            if not isinstance(content,str) or len(content.encode())>100000:
                raise GateError('文件正文无效或超过 100KB')
            target=worktree/path
            if any(p.is_symlink() for p in [target,*target.parents] if p.is_relative_to(worktree)):
                raise GateError('拒绝符号链接写入')
            prepared.append((target,content)); names.append(path)
        for target,content in prepared:
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(content)
        return names

    def gate(self,worktree,folder):
        args=['bwrap','--unshare-all','--die-with-parent','--new-session',
              '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
              '--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
              '--dir','/home/worker','--ro-bind',str(worktree),'/workspace','--chdir','/workspace',
              '--ro-bind',str(STARTER),'/home/worker/.local/share/ai-workflow/AI-Workflow-Starter-2026-11-11',
              '--clearenv','--setenv','HOME','/home/worker','--setenv','PATH','/usr/bin:/bin',
              '--setenv','PYTHONDONTWRITEBYTECODE','1','/usr/bin/python3','-m','unittest','discover','-s','tests','-q']
        execute(args,cwd=self.repo,prompt='',timeout=180,log=folder/'checks.json')
        # 两项都是控制面自己的固定程序，不执行任务提供的 shell 字符串。
        command(['python3',str(self.repo/'scripts/secret_scan.py')],worktree)
        git(worktree,'diff','--cached','--check')

    def acceptance(self,worktree,task):
        name=task.get('acceptance_template')
        if not name: return []
        template=self.project['acceptance_templates'].get(name)
        if not template: raise GateError('未知验收模板')
        source=self.repo/template['source']; destination=Path(worktree)/template['destination']
        safe_path(template['destination'])
        if destination.is_symlink(): raise GateError('验收目标不能是符号链接')
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_bytes(source.read_bytes())
        return [template['destination']]

    def _build(self,number,task,subject,payload,folder,gateway,state):
        branch='workflow/'+folder.name
        worktree=self.store.root/'worktrees'/folder.name
        if payload.get('head'):
            self._frozen(payload); return
        worktree.parent.mkdir(parents=True,exist_ok=True)
        if not worktree.exists(): git(self.repo,'worktree','add','-b',branch,str(worktree),subject['base'])
        elif git(worktree,'branch','--show-current')!=branch: raise GateError('恢复 worktree 归属不匹配')
        payload.update(worktree=str(worktree),branch=branch); state('RUNNING')
        checkpoint=folder/'verified-tree.json'
        current_head=git(worktree,'rev-parse','HEAD')
        if current_head!=subject['base']:
            if not checkpoint.is_file(): raise GateError('候选含未知提交，保留现场并停止')
            saved=json.loads(checkpoint.read_text())
            if (git(worktree,'status','--porcelain') or git(worktree,'rev-parse','HEAD^')!=subject['base']
                or git(worktree,'rev-parse','HEAD^{tree}')!=saved['tree']):
                raise GateError('恢复时提交不匹配已通过验收的冻结 tree')
            payload.update(head=current_head,allowed=saved['allowed']); state('REVIEW'); return
        if task.get('acceptance_template') and not payload.get('baseline_red'):
            controlled=self.acceptance(worktree,task)
            git(worktree,'add','--',*controlled)
            red=folder/'baseline-red'; red.mkdir(exist_ok=True)
            try:
                self.gate(worktree,red)
            except GateError:
                record=red/'checks.json'
                expected=self.project['acceptance_templates'][task['acceptance_template']]['expected_red']
                if not record.exists() or expected not in json.loads(record.read_text()).get('stderr',''):
                    raise GateError('BASELINE_RED_INFRASTRUCTURE_FAILURE')
                payload['baseline_red']=True; state('RUNNING')
            else:
                raise GateError('ALREADY_SATISFIED: 验收未出现预期失败，不生成无意义变更')
        errors=payload.setdefault('failures',[])
        for attempt in range(payload.get('next_attempt',0),3):
            payload['attempts']=attempt+1
            attempt_folder=folder/f'Builder-{attempt}'; attempt_folder.mkdir(exist_ok=True,mode=0o700)
            source_snapshot=attempt_folder/'approved-source.json'
            def source(): return read_source(worktree,task['paths'])
            if source_snapshot.exists(): context=json.loads(source_snapshot.read_text())
            else:
                context=source(); source_snapshot.write_text(json.dumps(context)); os.chmod(source_snapshot,0o600)
            proposal=self.stage('Builder',task,{'plan':payload['plan'],
                'output_contract':{'files':[{'path':'approved relative path','content':'complete source text'}],'summary':'string'},
                'previous_failures':errors,'approved_source':context,'acceptance':task['acceptance']},
                gateway,attempt_folder,attempt)
            proposed={x['path']:x['content'] for x in proposal.get('files',[]) if isinstance(x,dict) and isinstance(x.get('path'),str) and isinstance(x.get('content'),str)}
            for path,value in source().items():
                if value!=context[path] and (path not in proposed or value!=proposed[path]):
                    raise GateError('恢复或等待模型期间出现未知源码变更；不覆盖用户现场')
            self.apply(worktree,task,proposal)
            controlled=self.acceptance(worktree,task); allowed=set(task['paths'])|set(controlled)
            changed=set(git(worktree,'diff','--name-only').splitlines())|set(git(worktree,'diff','--cached','--name-only').splitlines())
            changed|=set(git(worktree,'ls-files','--others','--exclude-standard').splitlines())
            if not changed<=allowed: raise GateError('候选包含未批准变更')
            git(worktree,'add','--',*sorted(allowed))
            check_folder=folder/f'check-{attempt}'; check_folder.mkdir(exist_ok=True)
            try:
                state('VERIFYING'); self.gate(worktree,check_folder)
            except GateError as exc:
                log=check_folder/'checks.json'
                detail=json.loads(log.read_text()).get('stderr','')[-8000:] if log.exists() else str(exc)
                errors.append(detail); payload['next_attempt']=attempt+1; state('RUNNING'); continue
            payload['allowed']=sorted(allowed)
            checkpoint.write_text(json.dumps({'tree':git(worktree,'write-tree'),'allowed':payload['allowed']}))
            os.chmod(checkpoint,0o600)
            git(worktree,'commit','-m',f'feat: 完成练习任务 #{number}')
            payload['head']=git(worktree,'rev-parse','HEAD'); state('REVIEW'); return
        raise GateError('RETRY_LIMIT_REACHED: 三次候选未通过，不继续消费')

    def _frozen(self,payload):
        if git(payload['worktree'],'rev-parse','HEAD')!=payload['head'] or git(payload['worktree'],'status','--porcelain'):
            raise GateError('候选变化使审查或验收失效')

    def _publish(self,number,task,payload,folder,state):
        self._frozen(payload)
        if not payload.get('pr'):
            git(payload['worktree'],'push','-u','origin',payload['branch'])
            prs=json.loads(command(['gh','pr','list','-R',self.project['repository'],'--head',payload['branch'],'--state','all','--json','url,number']))
            if prs: payload['pr']=prs[0]
            else:
                body=folder/'pr.md'
                body.write_text(f'Closes #{number}\n\nBase: `{payload["base"]}`\nHead: `{payload["head"]}`\n\n隔离本地检查通过；独立 Reviewer 对冻结差异审查通过。\n模式：{self.mode}；validation 模式的 Claude Code 使用 DeepSeek，不代表 Claude API 验收。\n\n最终合并仅由人类执行。没有上传 Key、私有上下文或模型原始日志。')
                url=command(['gh','pr','create','-R',self.project['repository'],'--base',self.project['base_branch'],
                    '--head',payload['branch'],'--draft','--title',task['summary'],'--body-file',str(body)])
                payload['pr']={'url':url,'number':int(url.rsplit('/',1)[1])}
        state('WAITING_CI')
        remote=json.loads(command(['gh','pr','view',str(payload['pr']['number']),'-R',self.project['repository'],'--json','headRefOid,statusCheckRollup']))
        if remote['headRefOid']!=payload['head']: raise GateError('PR HEAD 与已审查候选不一致')
        for _ in range(12):
            if remote.get('statusCheckRollup'): break
            time.sleep(5)
            remote=json.loads(command(['gh','pr','view',str(payload['pr']['number']),'-R',self.project['repository'],'--json','headRefOid,statusCheckRollup']))
            if remote['headRefOid']!=payload['head']: raise GateError('等待 CI 时 PR HEAD 发生变化')
        if not remote.get('statusCheckRollup'): raise GateError('NO_CI_EVIDENCE')
        ci=command(['gh','pr','checks',str(payload['pr']['number']),'-R',self.project['repository'],'--watch','--interval','10'],timeout=900)
        if not ci: raise GateError('NO_CI_EVIDENCE')
        payload['ci']=ci[-4000:]

    def run(self,number,publish=True,resume=False):
        if git(self.repo,'status','--porcelain'): raise GateError('控制面仓库有未提交改动，请先冻结规则与源码')
        issue,task=self.issue(number); subject=self.subject(task)
        if not self.store.authorized(subject,'run'): raise GateError('WAITING_HUMAN_APPROVAL')
        for dep in task.get('depends_on',[]):
            status=json.loads(command(['gh','issue','view',str(dep),'-R',self.project['repository'],'--json','state']))
            if status['state']!='CLOSED': raise GateError('DEPENDENCY_NOT_READY')
        previous=[r for r in self.store.runs() if r['issue']==number and r['task_digest']==digest(subject)]
        if previous:
            row=previous[0]; run=row['id']; payload=json.loads(row['payload'])
            if row['state'] in TERMINAL:
                return {'run':run,'state':row['state'],'notice':'幂等返回，未重复启动任务','pr':payload.get('pr')}
            if not resume: raise GateError('RUN_EXISTS: 检查检查点后使用 recover，不重置预算或重试次数')
            folder=self.store.root/'runs'/run
        else:
            run=f'issue-{number}-{secrets.token_hex(4)}'; folder=self.store.root/'runs'/run
            folder.mkdir(parents=True,mode=0o700)
            payload={'repository':self.project['repository'],'issue':issue['url'],'mode':self.mode,'base':subject['base'],
                     'subject':subject,'attempts':0,'next_attempt':0}
        portfolio=self.project.get('portfolio')
        if publish and portfolio:
            self.portfolio=Portfolio(**portfolio)
            self.item=payload.get('portfolio_item') or self.portfolio.add(issue['url'])
            payload['portfolio_item']=self.item
        def state(value):
            with self.store.connect() as connection:
                current=connection.execute('SELECT state FROM runs WHERE id=?',(run,)).fetchone()
            if current and current['state']=='CANCELLED' and value!='CANCELLED': raise GateError('CANCELLED')
            self.store.save_run(run,number,subject,value,payload)
            status={'REVIEW':'Review','WAITING_HUMAN':'Review','DONE':'Done','WAITING_PLAN_APPROVAL':'Blocked',
                    'BLOCKED':'Blocked','FAILED':'Blocked','BUDGET_EXHAUSTED':'Blocked','CANCELLED':'Blocked'}.get(value,'Running')
            self.project_update(Status=status,Budget=f'cap=1 USD; accounted upper bound={self.store.balance()["accounted_microusd"]/1000000:.6f} USD; actual=null')
        with self.store.lock('dispatcher',run,ttl=1800):
            try:
                state('RUNNING')
                key=Path.home()/'.config/ai-workflow/secrets/deepseek.key'
                gateway_folder=folder/('gateway-'+secrets.token_hex(4))
                with Gateway(self.store,gateway_folder,key,run) as gateway:
                    payload['director']=self.stage('Director',task,{'output_contract':{'phase':'string','next_step':'string'}},gateway,folder/'Director')
                    payload['plan']=self.stage('Planner',task,{'output_contract':{'summary':'string','steps':['string'],'risks':['string']},
                                               'acceptance':task['acceptance'],'base':subject['base']},gateway,folder/'Planner')
                    state('PLANNED'); decision=route(task)
                    if decision['holds']:
                        if decision['holds']!=['高风险任务需要人工批准计划']:
                            raise GateError('NEEDS_CLARIFICATION: '+';'.join(decision['holds']))
                        plan_subject=subject|{'plan_digest':digest(payload['plan'])}
                        if not self.store.authorized(plan_subject,'plan'):
                            state('WAITING_PLAN_APPROVAL')
                            return {'run':run,'state':'WAITING_PLAN_APPROVAL','plan':payload['plan']}
                    self._build(number,task,subject,payload,folder,gateway,state)
                    self._frozen(payload)
                    diff=git(payload['worktree'],'diff',subject['base'],payload['head'],'--',*payload['allowed'])
                    state('REVIEW')
                    review=self.stage('Reviewer',task,{'base':subject['base'],'head':payload['head'],'diff':diff,
                        'checks':'隔离确定性检查通过','output_contract':{'approved':'boolean','findings':['string']}},gateway,folder/'Reviewer')
                    payload['review']=review
                    if review.get('approved') is not True or review.get('findings'):
                        raise GateError('REVIEW_CHANGES_REQUESTED')
                    self._frozen(payload)
                    payload['review_binding']=digest([subject['base'],payload['head'],subject['rules']])
                    if publish: self._publish(number,task,payload,folder,state)
                    state('KNOWLEDGE')
                    evidence={'base':subject['base'],'head':payload['head'],'checks':'隔离单元测试通过','review_binding':payload['review_binding'],
                              'limits':['测试 Provider 覆盖不等于生产 Claude API 验收','最终合并仍需人类'],
                              'pr':payload.get('pr'),'ci':payload.get('ci'),'architecture':'无凭据模型产物，由控制面验证路径后写入独立 worktree；审批/预算与 Worker 隔离'}
                    knowledge=self.stage('Knowledge',task,{'approved_evidence':evidence,
                        'output_contract':{'create':'boolean: 只有确有复用价值才为 true','title':'string',
                        'body':'有价值时输出含七个模板章节的 Markdown 正文，不含 frontmatter；否则为空字符串'}},gateway,folder/'Knowledge')
                    if knowledge.get('create') is True:
                        import importlib.util
                        spec=importlib.util.spec_from_file_location('knowledge_writer',STARTER/'draft_writer.py')
                        writer=importlib.util.module_from_spec(spec); spec.loader.exec_module(writer)
                        writer.validate_body(knowledge.get('body',''))
                    payload['knowledge']=knowledge
                    body_path=folder/'knowledge-body.md'; body_path.write_text(knowledge.get('body','')); os.chmod(body_path,0o600)
                    state('WAITING_HUMAN')
                    self.project_update(**{'等待人工动作':'审阅 draft PR 并人工合并；有价值知识须另行审阅/查重后新建 pending',
                                           '验证证据链接':payload.get('pr',{}).get('url','local-only')})
                    return {'run':run,'state':'WAITING_HUMAN','pr':payload.get('pr'),'knowledge_body':str(body_path),'budget':self.store.balance()}
            except (GateError,OSError,ValueError,KeyboardInterrupt) as exc:
                payload['error']=str(exc) or 'INTERRUPTED'
                state('BUDGET_EXHAUSTED' if 'BUDGET' in str(exc) else 'FAILED')
                raise

    def recover(self,number):
        row,data=self.latest(number); folder=self.store.root/'runs'/row['id']
        for path in folder.rglob('process.json'):
            if process_alive(json.loads(path.read_text())): raise GateError('原生进程仍在运行，拒绝重复派发')
        with self.store.transaction() as c:
            # 未知请求按完整上限记账，不能把中断当作未消费或退款。
            c.execute("UPDATE requests SET charged=reserved,state='unknown' WHERE state='reserved' AND (run=? OR run LIKE ?)",
                      (row['id'],row['id']+'/%'))
            c.execute('DELETE FROM locks WHERE owner=?',(row['id'],))
        return self.run(number,resume=True)

    def cancel(self,number):
        row,data=self.latest(number); folder=self.store.root/'runs'/row['id']
        if row['state']=='DONE': raise GateError('已完成运行不能取消；请发起独立回退任务')
        for path in folder.rglob('process.json'):
            record=json.loads(path.read_text())
            if process_alive(record): os.killpg(record['pid'],signal.SIGTERM)
        self.store.save_run(row['id'],number,data['subject'],'CANCELLED',data)
        return {'state':'CANCELLED','notice':'保留 worktree、费用和证据；已发出请求可能继续计费'}

    def finish(self,number):
        row,data=self.latest(number)
        if not data.get('pr'): raise GateError('没有 PR，不能宣称完成')
        pr=json.loads(command(['gh','pr','view',str(data['pr']['number']),'-R',self.project['repository'],
                               '--json','state,headRefOid,mergedAt,statusCheckRollup']))
        if not pr['mergedAt'] or pr['headRefOid']!=data['head']:
            raise GateError('WAITING_HUMAN: PR 尚未由人类合并或 SHA 不匹配')
        checks=pr.get('statusCheckRollup') or []
        if not checks_successful(checks):
            raise GateError('CI 状态不是完整成功')
        self.store.save_run(row['id'],number,data['subject'],'DONE',data)
        if self.project.get('portfolio') and data.get('portfolio_item'):
            Portfolio(**self.project['portfolio']).update(data['portfolio_item'],Status='Done',**{'等待人工动作':'无；知识草稿是否采纳仍由人类决定'})
        return {'run':row['id'],'state':'DONE','head':data['head'],'pr':data['pr']}

    def watch(self,once=False,interval=30):
        while True:
            issues=json.loads(command(['gh','issue','list','-R',self.project['repository'],'--state','open',
                                      '--label','workflow:ready','--json','number']))
            for issue in issues:
                try: print(json.dumps(self.run(issue['number']),ensure_ascii=False),flush=True)
                except GateError as exc:
                    print(json.dumps({'issue':issue['number'],'gate':str(exc)},ensure_ascii=False),flush=True)
            if once: break
            time.sleep(max(interval,15))
