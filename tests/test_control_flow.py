"""控制流回归使用隔离临时仓库与替身；真实 CLI/CI 的证据单独记录。"""
import copy
import hashlib
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
        (self.repo/'scripts').mkdir(); (self.repo/'scripts/verify_core.py').write_text('# unit verification fixture\n')
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
                      patch.object(self.engine,'gate',side_effect=self.fake_gate)]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.engine.store.approve(self.subject,'run')
    def stage(self,role,*args,**kw):
        self.calls.append(role)
        if role=='Reviewer': self.review_context=args[1]
        return {'Director':{'phase':'planned','next_step':'plan'},
                'Planner':{'summary':'unit plan','steps':['write approved file'],'risks':[]},
                'Builder':{'files':[{'path':'demo/labels.py','content':'value = 1\n'}],'summary':'unit'},
                'Reviewer':{'approved':True,'findings':[]},
                'Knowledge':{'create':False,'title':'','body':''}}[role]
    def fake_gate(self,worktree,folder):
        # 明确的替身证据；不得在真实实现中允许缺失记录时继续。
        if folder.name=='baseline-red':
            (folder/'checks.json').write_text(json.dumps({'returncode':1,'stdout':'','stderr':'unit missing module'}))
            raise GateError('unit expected red')
        core={'schema':'wf-core-checks-v1','passed':True,'steps':[
            {'name':'project-tests','command':['python3','-m','unittest'],'returncode':0,'tests':1,'passed':True},
            {'name':'starter-tests','command':['python3','-m','unittest'],'returncode':0,'tests':1,'passed':True},
            {'name':'syntax-and-config','returncode':0,'passed':True,'parsed':[]}]}
        record=folder/'checks.json'; record.write_text(json.dumps({'returncode':0,'stdout':json.dumps(core),'stderr':'unit fixture'}))
        proof={'schema':'wf-verification-v1','tree':git(worktree,'write-tree'),'core':core,
               'core_sha256':hashlib.sha256((self.repo/'scripts/verify_core.py').read_bytes()).hexdigest(),
               'record_sha256':hashlib.sha256(record.read_bytes()).hexdigest(),
               'isolation':{'unit_fixture':True},'static_checks':[{'command':['unit fixture'],'returncode':0}]}
        (folder/'verification.json').write_text(json.dumps(proof))
        return proof
    def acceptance_fixture(self):
        self.task['acceptance_template']='unit-frozen'
        template={'source':'.workflow/acceptance/frozen.py','destination':'tests/test_frozen.py','expected_red':'unit missing module'}
        self.engine.project['acceptance_templates']['unit-frozen']=template
        source=self.repo/template['source']; source.parent.mkdir(); source.write_text('# frozen unit acceptance\n')
        git(self.repo,'add','.'); git(self.repo,'commit','-m','freeze unit acceptance')
        self.subject['base']=git(self.repo,'rev-parse','HEAD'); self.engine.store.approve(self.subject,'run')
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
    def test_reviewer_receives_bound_validation_and_controller_provenance(self):
        self.acceptance_fixture()
        self.engine.run(1,publish=False)
        context=self.review_context
        self.assertTrue(context['approval']['valid_for_exact_subject'])
        self.assertTrue(context['baseline_red']['observed'])
        self.assertEqual(context['baseline_red']['returncode'],1)
        self.assertEqual(context['builder_allowed_paths'],['demo/labels.py'])
        provenance=context['controller_acceptance'][0]
        self.assertEqual(provenance['destination'],'tests/test_frozen.py')
        self.assertEqual(provenance['frozen_blob'],provenance['candidate_blob'])
        self.assertTrue(context['verification']['core']['passed'])
        self.assertEqual(context['verification']['tree'],git(self.engine.latest(1)[1]['worktree'],'rev-parse','HEAD^{tree}'))
        self.assertEqual(context['builder_dispatch']['tier'],'T3')
        self.assertIsNone(context['builder_dispatch']['actual_model'])
        self.assertNotIn(str(self.root),json.dumps(context))
        self.assertNotIn('token',context['approval']); self.assertNotIn('signature',context['approval'])
    def test_verification_record_change_stops_before_reviewer(self):
        original=self.engine._build
        def altered(*args):
            original(*args)
            path=args[4]/'check-0/checks.json'; path.write_text(path.read_text()+'\n')
        with patch.object(self.engine,'_build',side_effect=altered):
            with self.assertRaisesRegex(GateError,'BINDING'): self.engine.run(1,publish=False)
        self.assertNotIn('Reviewer',self.calls)
    def test_missing_verification_stops_before_reviewer(self):
        original=self.engine._build
        def altered(*args): original(*args); args[3].pop('verification')
        with patch.object(self.engine,'_build',side_effect=altered):
            with self.assertRaisesRegex(GateError,'MISSING_VERIFICATION'): self.engine.run(1,publish=False)
        self.assertNotIn('Reviewer',self.calls)
    def test_corrupt_verification_file_stops_before_reviewer(self):
        original=self.engine._build
        def altered(*args): original(*args); (args[4]/'check-0/checks.json').write_text('{}')
        with patch.object(self.engine,'_build',side_effect=altered):
            with self.assertRaisesRegex(GateError,'INVALID_VERIFICATION'): self.engine.run(1,publish=False)
        self.assertNotIn('Reviewer',self.calls)
    def test_changed_frozen_template_stops_before_reviewer(self):
        self.acceptance_fixture(); original=self.engine._build
        def altered(*args):
            original(*args); (self.repo/'.workflow/acceptance/frozen.py').write_text('# changed unit acceptance\n')
        with patch.object(self.engine,'_build',side_effect=altered):
            with self.assertRaisesRegex(GateError,'FROZEN_ACCEPTANCE'): self.engine.run(1,publish=False)
        self.assertNotIn('Reviewer',self.calls)
    def test_approved_with_any_findings_still_blocks(self):
        original=self.stage
        def stage(role,*args,**kw):
            if role=='Reviewer': return {'approved':True,'findings':['unresolved evidence gap'],'report':'unit info'}
            return original(role,*args,**kw)
        self.engine.stage.side_effect=stage
        with self.assertRaisesRegex(GateError,'REVIEW'): self.engine.run(1,publish=False)
        self.assertNotIn('Knowledge',self.calls)
    def test_rules_changed_during_reviewer_never_publish(self):
        original=self.stage
        def stage(role,*args,**kw):
            value=original(role,*args,**kw)
            if role=='Reviewer': self.subject=self.subject|{'rules':'unit changed during reviewer'}
            return value
        self.engine.stage.side_effect=stage
        with patch.object(self.engine,'_publish') as publish:
            with self.assertRaisesRegex(GateError,'REVIEW_APPROVAL_CHANGED'): self.engine.run(1)
        publish.assert_not_called(); self.assertNotIn('Knowledge',self.calls)
    def test_expired_approval_during_reviewer_never_publish(self):
        original=self.stage
        def stage(role,*args,**kw):
            value=original(role,*args,**kw)
            if role=='Reviewer':
                # 只使本测试临时账本中的模拟批准过期，绝不访问真实批准。
                with self.engine.store.connect() as c: c.execute('UPDATE approvals SET expiry=0')
            return value
        self.engine.stage.side_effect=stage
        with patch.object(self.engine,'_publish') as publish:
            with self.assertRaisesRegex(GateError,'REVIEW_APPROVAL_CHANGED'): self.engine.run(1)
        publish.assert_not_called(); self.assertNotIn('Knowledge',self.calls)
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
