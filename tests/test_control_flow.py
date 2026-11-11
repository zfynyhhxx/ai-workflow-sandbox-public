"""控制流回归使用隔离临时仓库与替身；真实 CLI/CI 的证据单独记录。"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from workflowkit.engine import Engine,git
from workflowkit.store import GateError
from workflowkit.profiles import profiles

TASK={'id':'control-unit','role':'Builder','summary':'internal demo',
      'requirements_clear':True,'bounded':True,'modules':1,'paths':['demo/labels.py'],
      'risk_tags':[],'attempts':0,'acceptance':['real gate passes'],'depends_on':[]}

class FakeGateway:
    def __init__(self,*a,**kw): self.run='unit'
    def __enter__(self): return self
    def __exit__(self,*a): pass

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        git(self.repo,'init','-b','main'); git(self.repo,'config','user.name','Unit Test')
        git(self.repo,'config','user.email','unit@users.noreply.github.com')
        (self.repo/'.workflow').mkdir()
        (self.repo/'.workflow/project.json').write_text(json.dumps({'repository':'unit/repo','base_branch':'main','acceptance_templates':{}}))
        (self.repo/'README.md').write_text('unit\n')
        git(self.repo,'add','.'); git(self.repo,'commit','-m','unit baseline')
        self.engine=Engine(self.repo,self.root/'state',mode='validation')
        self.task=copy.deepcopy(TASK)
        self.issue={'url':'https://github.com/unit/repo/issues/1'}
        self.calls=[]
        self.subject={'task':self.task,'rules':'unit-rules','base':git(self.repo,'rev-parse','HEAD'),
                      'mode':'validation','project':'unit/repo'}
        self.patches=[patch.object(self.engine,'issue',side_effect=lambda n:(self.issue,self.task)),
                      patch.object(self.engine,'subject',side_effect=lambda t:self.subject),
                      patch('workflowkit.engine.Gateway',FakeGateway),
                      patch.object(self.engine,'stage',side_effect=self.stage),
                      patch.object(self.engine,'gate')]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.engine.store.approve(self.subject,'run')
    def stage(self,role,*args,**kw):
        self.calls.append(role)
        return {'Director':{'phase':'planned','next_step':'plan'},
                'Planner':{'summary':'unit plan','steps':['write approved file'],'risks':[]},
                'Builder':{'files':[{'path':'demo/labels.py','content':'value = 1\n'}],'summary':'unit'},
                'Reviewer':{'approved':True,'findings':[]},
                'Knowledge':{'create':False,'title':'','body':''}}[role]
    def test_complete_local_flow_then_idempotent(self):
        result=self.engine.run(1,publish=False)
        self.assertEqual(result['state'],'WAITING_HUMAN')
        count=len(self.calls)
        second=self.engine.run(1,publish=False)
        self.assertEqual(second['run'],result['run']); self.assertEqual(len(self.calls),count)
        self.assertEqual(git(self.repo,'status','--porcelain'),'')
        self.assertEqual(self.engine.store.balance()['accounted_microusd'],0)
    def test_stale_approval_blocks_before_model(self):
        self.subject=self.subject|{'rules':'changed'}
        with self.assertRaisesRegex(GateError,'APPROVAL'): self.engine.run(1,publish=False)
        self.assertEqual(self.calls,[])
    def test_approval_cannot_sign_unseen_input_changed_during_human_review(self):
        displayed=copy.deepcopy(self.subject)
        self.subject=self.subject|{'rules':'changed-during-review'}
        with self.assertRaisesRegex(GateError,'APPROVAL_INPUT_CHANGED'):
            self.engine.approve(1,expected_subject=displayed)
        self.assertFalse(self.engine.store.authorized(self.subject,'run'))
    def test_high_risk_plan_waits_then_resumes(self):
        self.task['risk_tags']=['security']
        self.engine.store.approve(self.subject,'run')
        result=self.engine.run(1,publish=False)
        self.assertEqual(result['state'],'WAITING_PLAN_APPROVAL'); self.assertNotIn('Builder',self.calls)
        self.engine.approve(1,plan=True)
        result=self.engine.run(1,publish=False,resume=True)
        self.assertEqual(result['state'],'WAITING_HUMAN')
    def test_no_unknown_risk_waiver(self):
        self.task['risk_tags']=['unrecognized']
        self.engine.store.approve(self.subject,'run')
        with self.assertRaisesRegex(GateError,'CLARIFICATION'): self.engine.run(1,publish=False)
        self.assertNotIn('Builder',self.calls)
    def test_review_failure_never_publishes(self):
        original=self.stage
        def stage(role,*a,**kw):
            if role=='Reviewer': return {'approved':False,'findings':['unit issue']}
            return original(role,*a,**kw)
        self.engine.stage.side_effect=stage
        with self.assertRaisesRegex(GateError,'REVIEW'): self.engine.run(1,publish=False)
        self.assertEqual(self.engine.latest(1)[0]['state'],'FAILED')
    def test_candidate_mutation_invalidates_review(self):
        result=self.engine.run(1,publish=False)
        _,data=self.engine.latest(1)
        (Path(data['worktree'])/'demo/labels.py').write_text('changed=2\n')
        with self.assertRaisesRegex(GateError,'失效'): self.engine._frozen(data)
    def test_crash_after_commit_adopts_only_preverified_tree(self):
        def crash(repo,*args):
            result=git(repo,*args)
            if args and args[0]=='commit': raise GateError('unit crash after commit before checkpoint state')
            return result
        with patch('workflowkit.engine.git',side_effect=crash):
            with self.assertRaisesRegex(GateError,'unit crash'): self.engine.run(1,publish=False)
        result=self.engine.run(1,publish=False,resume=True)
        self.assertEqual(result['state'],'WAITING_HUMAN')
        self.assertEqual(self.calls.count('Builder'),1)
    def test_resume_never_overwrites_unrecognized_user_edit(self):
        original=self.engine.apply
        def crash(*args):
            original(*args); raise GateError('unit crash after apply')
        with patch.object(self.engine,'apply',side_effect=crash):
            with self.assertRaisesRegex(GateError,'unit crash'): self.engine.run(1,publish=False)
        _,data=self.engine.latest(1); path=Path(data['worktree'])/'demo/labels.py'
        path.write_text('user_edit=42\n')
        with self.assertRaisesRegex(GateError,'不覆盖'): self.engine.run(1,publish=False,resume=True)
        self.assertEqual(path.read_text(),'user_edit=42\n')
    def test_three_failures_stop_and_preserve_worktree(self):
        self.engine.gate.side_effect=GateError('unit failing gate')
        with self.assertRaisesRegex(GateError,'RETRY_LIMIT'): self.engine.run(1,publish=False)
        row,data=self.engine.latest(1)
        self.assertEqual(data['next_attempt'],3); self.assertTrue(Path(data['worktree']).exists())
        with self.assertRaisesRegex(GateError,'RETRY_LIMIT'): self.engine.run(1,publish=False,resume=True)
        self.assertEqual(self.calls.count('Builder'),3)
    def test_finish_never_merges(self):
        self.engine.run(1,publish=False)
        with self.assertRaisesRegex(GateError,'没有 PR'): self.engine.finish(1)
    def test_formal_profile_unchanged_by_validation(self):
        formal=profiles('production'); validation=profiles('validation')
        self.assertEqual(formal['claude-api']['provider'],'anthropic')
        self.assertEqual(validation['claude-api']['provider'],'deepseek')
        self.assertEqual(profiles('production'),formal)
        self.assertEqual(validation['deepseek-api'],formal['deepseek-api'])

if __name__=='__main__': unittest.main()
