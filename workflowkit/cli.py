"""面向操作员的统一入口；批准与知识落库必须在人类控制面显式调用。"""
import argparse
from decimal import Decimal
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
from .engine import Engine,ROOT,STATE,command
from .policy import STARTER
from .store import GateError,digest


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--mode',choices=['validation','production'],default='production',
                        help='默认正式配置；测试时必须显式选择 validation')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('status')
    ao=sub.add_parser('ao'); ao.add_argument('operation',choices=['start','status','stop'])
    budget=sub.add_parser('budget'); budget.add_argument('--cap-usd',required=True); budget.add_argument('--reason',required=True)
    for name in ('approve','run','recover','finish','cancel'):
        p=sub.add_parser(name); p.add_argument('issue',type=int)
        if name=='approve':
            p.add_argument('--ttl',type=int,default=3600)
            p.add_argument('--plan',action='store_true')
    watch=sub.add_parser('watch'); watch.add_argument('--once',action='store_true'); watch.add_argument('--interval',type=int,default=30)
    draft=sub.add_parser('draft'); draft.add_argument('run'); draft.add_argument('--title',required=True); draft.add_argument('--slug',required=True)
    args=parser.parse_args()
    try:
        engine=Engine(args.repo,mode=args.mode)
        if args.action=='ao':
            from .ao import install_adapters,call,environment,owned_processes,terminate_owned
            install_adapters()
            if args.operation=='start':
                running=Path.home()/'.agent-orchestrator/running.json'
                if running.is_file():
                    record=json.loads(running.read_text())
                    try: os.kill(record['pid'],0)
                    except ProcessLookupError: pass
                    else:
                        raise GateError('AO_ALREADY_RUNNING: 仅查看 status；禁止重复 start 意外启动原生 orchestrator')
                # 原生 start 以服务前台运行；不能拿请求型超时终止它并遗留子进程。
                raise SystemExit(subprocess.call(['ao','start','workflow-sandbox','--no-orchestrator','--no-restore'],cwd=ROOT,env=environment()))
            operations={'start':['start','--no-orchestrator','--no-restore'],
                        'status':['status'],'stop':['stop','workflow-sandbox','--yes']}
            running=Path.home()/'.agent-orchestrator/running.json'
            before=json.loads(running.read_text()) if args.operation=='stop' and running.is_file() else None
            owned=owned_processes(before)
            try: print(call(operations[args.operation],timeout=180))
            finally:
                # native stop 可能先移除运行记录；使用调用前捕获的 PID/starttime，
                # 覆盖其 detached dashboard 子树，且不触碰共享实例和其他项目。
                terminate_owned(owned)
            return
        if args.action=='status':
            result={'budget':engine.store.balance(),'runs':engine.store.runs()}
        elif args.action=='budget':
            if not sys.stdin.isatty(): raise GateError('预算变更必须由人类在交互终端确认')
            raw=Decimal(args.cap_usd)*1000000
            if not raw.is_finite() or raw!=raw.to_integral_value(): raise GateError('预算最多六位小数')
            amount=int(raw)
            print(json.dumps({'current':engine.store.balance(),'new_cap_microusd':amount,'reason':args.reason},ensure_ascii=False))
            if input('保留全部历史费用，确认本次授权上限，输入 BUDGET: ')!='BUDGET': raise GateError('未授权')
            engine.store.set_cap(amount,engine.project['budget_cap_microusd'])
            result=engine.store.balance()
        elif args.action=='approve':
            if not sys.stdin.isatty(): raise GateError('批准必须由人类在交互终端执行')
            issue,task=engine.issue(args.issue)
            preview=engine.subject(task)
            if args.plan:
                _,data=engine.latest(args.issue)
                preview=preview|{'plan_digest':digest(data['plan'])}
                print(json.dumps({'plan':data['plan']},ensure_ascii=False,indent=2))
            print(json.dumps({'issue':issue['url'],'task':task,'mode':args.mode,'subject':preview},ensure_ascii=False,indent=2))
            if input('确认上述任务/路径/预算/模式，输入 APPROVE: ')!='APPROVE': raise GateError('未批准')
            result={'approved_digest':engine.approve(args.issue,args.ttl,plan=args.plan,expected_subject=preview)}
        elif args.action=='run': result=engine.run(args.issue)
        elif args.action=='watch': engine.watch(args.once,args.interval); return
        elif args.action=='recover': result=engine.recover(args.issue)
        elif args.action=='finish': result=engine.finish(args.issue)
        elif args.action=='cancel': result=engine.cancel(args.issue)
        elif args.action=='draft':
            if not sys.stdin.isatty(): raise GateError('知识落库必须由人类在交互终端确认正文与查重')
            run=next((r for r in engine.store.runs() if r['id']==args.run),None)
            if not run: raise GateError('未知运行')
            if json.loads(run['payload']).get('knowledge',{}).get('create') is not True:
                raise GateError('本轮没有确认的知识复用价值，不创建笔记')
            body=engine.store.root/'runs'/args.run/'knowledge-body.md'
            print(body.read_text())
            if input('已审阅来源/隐私/查重，确认新建 pending，输入 DRAFT: ')!='DRAFT': raise GateError('未确认')
            spec=importlib.util.spec_from_file_location('draft_writer',STARTER/'draft_writer.py')
            writer=importlib.util.module_from_spec(spec); spec.loader.exec_module(writer)
            data=json.loads(run['payload'])
            source=engine.project['repository']+'@'+data['head']+':'+data.get('pr',{}).get('url','local')
            path=writer.write_draft(Path.home()/'Knowledge/Engineering',body.read_text(),args.title,engine.project['repository'],source,args.slug)
            result={'draft':str(path),'status':'pending'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (GateError,OSError,ValueError,KeyError) as exc:
        print('STOPPED: '+str(exc),file=sys.stderr); raise SystemExit(2)


if __name__=='__main__': main()
