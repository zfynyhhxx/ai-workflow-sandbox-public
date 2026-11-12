"""纯替身验证协议回归；真实 bwrap 验收另行执行。"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from workflowkit.engine import Engine
from workflowkit.policy import rule_hash,role_prompt
from workflowkit.store import Store,GateError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('verification_core',ROOT/'scripts/verify_core.py')
core=importlib.util.module_from_spec(spec); spec.loader.exec_module(core)


class CoreTests(unittest.TestCase):
    def run_core(self,root,results):
        with patch.object(core.subprocess,'run',side_effect=results) as run,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return core.verify(root),run.call_args_list
    def passed(self,count=1):
        return subprocess.CompletedProcess([],0,'',f'Ran {count} tests in 0.001s\nOK\n')
    def test_both_suites_and_original_syntax_checks_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result,calls=self.run_core(root,[self.passed(65),self.passed(31)])
            self.assertTrue(result['passed'])
            self.assertEqual([x['tests'] for x in result['steps'][:2]],[65,31])
            self.assertEqual(calls[0].args[0][-3:],['-s','tests','-q'])
            self.assertEqual(calls[1].args[0][-3:],['-s','vendor/starter/tests','-q'])
            for name,contents in (('demo/bad.py','def broken('),('config/bad.json','{')):
                p=root/name; p.parent.mkdir(exist_ok=True); p.write_text(contents)
                failed,_=self.run_core(root,[self.passed(),self.passed()])
                self.assertFalse(failed['passed']); self.assertEqual(failed['steps'][-1]['returncode'],1)
                p.unlink()
    def test_either_test_suite_failure_or_zero_tests_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); failed=subprocess.CompletedProcess([],1,'','Ran 1 test\nFAILED\n')
            first,calls=self.run_core(root,[failed]); self.assertFalse(first['passed']); self.assertEqual(len(calls),1)
            second,calls=self.run_core(root,[self.passed(),failed]); self.assertFalse(second['passed']); self.assertEqual(len(calls),2)
            empty,_=self.run_core(root,[self.passed(0)]); self.assertFalse(empty['passed'])
    def test_final_manifest_has_its_own_line_after_unterminated_test_output(self):
        def verify(_):
            print('unit output without newline',end='')
            return {'schema':'wf-core-checks-v1','passed':True,'steps':[]}
        output=io.StringIO()
        with patch.object(core,'verify',side_effect=verify),contextlib.redirect_stdout(output):
            self.assertEqual(core.main(),0)
        self.assertTrue(json.loads(output.getvalue().splitlines()[-1])['passed'])


class GateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.control=self.root/'control'; self.candidate=self.root/'candidate'
        for repo in (self.control,self.candidate):
            (repo/'scripts').mkdir(parents=True)
            (repo/'scripts/verify_core.py').write_text('# trusted\n' if repo==self.control else 'raise RuntimeError("candidate entry must not execute")\n')
        self.folder=self.root/'checks'; self.folder.mkdir()
        self.engine=object.__new__(Engine); self.engine.repo=self.control
        self.core={'schema':'wf-core-checks-v1','passed':True,'steps':[
            {'name':'project-tests','returncode':0,'tests':1,'passed':True},
            {'name':'starter-tests','returncode':0,'tests':1,'passed':True},
            {'name':'syntax-and-config','returncode':0,'passed':True}]}
    def execute(self,*args,**kwargs):
        Path(kwargs['log']).write_text(json.dumps({'returncode':0,'stdout':json.dumps(self.core),'stderr':'unit fixture'}))
        return json.dumps(self.core)
    def test_trusted_core_is_bound_readonly_without_host_git_or_credentials(self):
        with patch('workflowkit.engine.execute',side_effect=self.execute) as execute,patch('workflowkit.engine.command',return_value='unit-tree'):
            proof=self.engine.gate(self.candidate,self.folder)
        args=execute.call_args.args[0]
        i=args.index(str(self.control/'scripts/verify_core.py'))
        self.assertEqual(args[i-1:i+2],['--ro-bind',str(self.control/'scripts/verify_core.py'),'/runtime/verify_core.py'])
        self.assertEqual(args[-2:],['/usr/bin/python3','/runtime/verify_core.py'])
        self.assertIn('--unshare-all',args); self.assertIn('--clearenv',args)
        self.assertNotIn(str(self.candidate/'scripts/verify_core.py'),args)
        self.assertFalse(any('.git' in x or 'secrets' in x for x in args))
        self.assertEqual(proof['tree'],'unit-tree'); self.assertEqual(len(proof['static_checks']),3)
        self.assertEqual(json.loads((self.folder/'verification.json').read_text()),proof)
    def test_partial_success_cannot_create_verification_evidence(self):
        self.core['steps'].pop(1)
        with patch('workflowkit.engine.execute',side_effect=self.execute),patch('workflowkit.engine.command') as command:
            with self.assertRaisesRegex(GateError,'INCOMPLETE'): self.engine.gate(self.candidate,self.folder)
        command.assert_not_called(); self.assertFalse((self.folder/'verification.json').exists())
    def test_authority_document_changes_invalidate_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('AGENTS.md','.workflow/project.json','.specify/memory/constitution.md','docs/spec.md','docs/adr/0001-unit.md'):
                p=root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('unit authority')
            before=rule_hash(root); store=Store(root/'private-unit-state'); subject={'rules':before}; store.approve(subject,'run')
            for name in ('docs/spec.md','docs/adr/0001-unit.md'):
                (root/name).write_text('changed authority')
                after=rule_hash(root); self.assertNotEqual(before,after)
                self.assertFalse(store.authorized({'rules':after},'run'))
    def test_reviewer_information_is_separate_not_filtered_after_output(self):
        prompt=role_prompt('Reviewer',{'id':'unit'}, {'unit_fixture':True})
        self.assertIn('report',prompt); self.assertIn('未解决',prompt)
        self.assertIn('缺少必要证据仍应拒绝',prompt)
