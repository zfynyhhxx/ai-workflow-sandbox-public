import unittest
from pathlib import Path
import tempfile
from workflowkit.policy import read_source,safe_path
from workflowkit.policy import route,validate_artifact
from workflowkit.store import GateError

TASK={'id':'artifact','role':'Builder','summary':'unit','requirements_clear':True,'bounded':True,'modules':1,
      'paths':['workflowkit/gateway.py'],'risk_tags':[],'attempts':0,'acceptance':['unit contract']}
class ArtifactContractTests(unittest.TestCase):
    def test_control_plane_risk_floor(self):
        result=route(TASK)
        self.assertEqual(result['tier'],'T1'); self.assertTrue(result['holds'])
    def test_arbitrary_json_not_role_success(self):
        for role in ('Director','Planner','Builder','Reviewer','Knowledge'):
            with self.assertRaises(GateError): validate_artifact(role,{'message':'I am done'})
    def test_typed_reviewer_boolean(self):
        with self.assertRaises(GateError): validate_artifact('Reviewer',{'approved':'true','findings':[]})
        self.assertTrue(validate_artifact('Reviewer',{'approved':True,'findings':[]})['approved'])
    def test_planner_requires_steps(self):
        with self.assertRaises(GateError): validate_artifact('Planner',{'summary':'unit','steps':[],'risks':[]})
        validate_artifact('Planner',{'summary':'unit','steps':['do bounded work'],'risks':[]})
    def test_context_cannot_follow_symlink_or_include_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'outside').write_text('private unit marker')
            (root/'linked.py').symlink_to(root/'outside')
            with self.assertRaisesRegex(GateError,'符号链接'): read_source(root,['linked.py'])
            self.assertEqual(read_source(root,['new.py']),{'new.py':None})
        for path in ('.env','config/.env.local','secrets/api.key','auth.json'):
            with self.assertRaisesRegex(GateError,'凭据'): safe_path(path)
