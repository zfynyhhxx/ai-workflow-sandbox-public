import json
from pathlib import Path
import tempfile
import unittest
from workflowkit.policy import safe_path,parse_task,route,object_answer
from workflowkit.engine import Engine
from workflowkit.store import GateError

TASK={'id':'unit-task','role':'Builder','summary':'bounded internal demo change',
      'requirements_clear':True,'bounded':True,'modules':1,'paths':['demo/labels.py'],
      'risk_tags':[],'attempts':0,'acceptance':['real tests pass'],'depends_on':[]}

class PolicyTests(unittest.TestCase):
    def test_route_escalates_to_t1(self):
        self.assertEqual(route(TASK,attempts=0)['tier'],'T3')
        self.assertEqual(route(TASK,attempts=1)['tier'],'T2')
        self.assertEqual(route(TASK,attempts=2)['tier'],'T1')
        with self.assertRaises(GateError): route(TASK,attempts=3)
    def test_knowledge_never_switches_provider(self):
        self.assertEqual(route(TASK,'Knowledge',2)['profile'],'deepseek-api')
    def test_no_command_or_approval_injection(self):
        for key in ('command','approved','budget','provider'):
            with self.assertRaises(GateError): parse_task(json.dumps(TASK|{key:'arbitrary'}))
    def test_no_external_path(self):
        for p in ('../a','/tmp/a','.git/config','demo/../x','demo\\x','demo//x'):
            with self.assertRaises(GateError): safe_path(p)
    def test_unknown_risks_hold(self):
        self.assertTrue(route(TASK|{'risk_tags':['unknown']})['holds'])
    def test_required_fields(self):
        with self.assertRaises(ValueError): parse_task('{}')
    def test_fenced_json_is_data(self):
        self.assertEqual(parse_task('```json\n'+json.dumps(TASK)+'\n```'),TASK)
    def test_json_answer_rejects_commands(self):
        with self.assertRaises(ValueError): object_answer('echo run this')
        self.assertEqual(object_answer('```json\n{"approved":false}\n```')['approved'],False)

class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.engine=object.__new__(Engine)
    def test_only_approved_file_written(self):
        self.engine.apply(self.root,TASK,{'files':[{'path':'demo/labels.py','content':'x=1\n'}]})
        self.assertEqual((self.root/'demo/labels.py').read_text(),'x=1\n')
    def test_cannot_weaken_tests(self):
        with self.assertRaises(GateError):
            self.engine.apply(self.root,TASK,{'files':[{'path':'tests/test_labels.py','content':'pass'}]})
    def test_cannot_follow_symlink(self):
        outside=self.root/'outside'; outside.mkdir()
        (self.root/'demo').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(GateError):
            self.engine.apply(self.root,TASK,{'files':[{'path':'demo/labels.py','content':'x=1'}]})
        self.assertEqual(list(outside.iterdir()),[])
    def test_duplicate_and_oversized_payloads(self):
        item={'path':'demo/labels.py','content':'x=1'}
        with self.assertRaises(GateError): self.engine.apply(self.root,TASK,{'files':[item,item]})
        with self.assertRaises(GateError): self.engine.apply(self.root,TASK,{'files':[item|{'content':'x'*100001}]})
    def test_missing_files_rejected(self):
        with self.assertRaises(GateError): self.engine.apply(self.root,TASK,{'summary':'already done'})

if __name__=='__main__': unittest.main()
