import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workflowkit.ao import agent_main,sessions
from workflowkit.store import digest,GateError

class AOTests(unittest.TestCase):
    def test_catalog_queries_never_start_a_model_even_with_active_pointer(self):
        with tempfile.TemporaryDirectory() as tmp,patch('workflowkit.ao.STATE',Path(tmp)),patch('workflowkit.ao.api_worker') as worker,patch('builtins.print') as output:
            (Path(tmp)/'ao-current.json').write_text('{"path":"must-not-open-for-metadata"}')
            self.assertEqual(agent_main('opencode',['session','list','--format','json']),0)
            worker.assert_not_called(); output.assert_called_once_with('[]')
        with tempfile.TemporaryDirectory() as tmp,patch('workflowkit.ao.STATE',Path(tmp)),patch.dict(os.environ,{'AO_SESSION_ID':''}):
            with self.assertRaisesRegex(GateError,'NO_AO_SESSION'): agent_main('opencode',['run','--command','true'])
    def test_legacy_warmup_cannot_bind_or_launch_an_approved_packet(self):
        with tempfile.TemporaryDirectory() as tmp,patch('workflowkit.ao.STATE',Path(tmp)),patch.dict(os.environ,{'AO_SESSION_ID':'wfsp-unit'}),patch('workflowkit.ao.api_worker') as worker:
            root=Path(tmp); job={'harness':'opencode','folder':tmp,'prompt':'unit','model':'unit','socket':'unused','token':'unit'}
            binding=digest(job); path=root/'job.json'; path.write_text(json.dumps(job|{'binding':binding}))
            (root/'ao-current.json').write_text(json.dumps({'path':str(path),'binding':binding}))
            with self.assertRaisesRegex(GateError,'PROMPT_BINDING'): agent_main('opencode',['run','--command','true'])
            worker.assert_not_called(); self.assertFalse((root/'ao-sessions/wfsp-unit').exists())
    def test_native_session_envelope_is_not_silently_empty(self):
        with patch('workflowkit.ao.call',return_value=json.dumps({'data':[{'id':'wfsp-unit'}],'meta':{}})):
            self.assertEqual(sessions(),[{'id':'wfsp-unit'}])
        with patch('workflowkit.ao.call',return_value=json.dumps({'unknown':[]})):
            with self.assertRaisesRegex(GateError,'PROTOCOL'): sessions()
    def test_unknown_session_cannot_start_unapproved_model(self):
        with tempfile.TemporaryDirectory() as tmp,patch('workflowkit.ao.STATE',Path(tmp)),patch.dict(os.environ,{'AO_SESSION_ID':'wfsp-unit'}):
            with self.assertRaisesRegex(GateError,'NO_APPROVED'): agent_main('claude',[])
    def test_restore_only_replays_frozen_result(self):
        with tempfile.TemporaryDirectory() as tmp,patch('workflowkit.ao.STATE',Path(tmp)),patch.dict(os.environ,{'AO_SESSION_ID':'wfsp-unit'}):
            root=Path(tmp); folder=root/'job'; folder.mkdir()
            job={'harness':'claude','folder':str(folder),'prompt':'unit','model':'unit','socket':'expired','token':'unit'}
            binding=digest(job); path=folder/'job.json'; path.write_text(json.dumps(job|{'binding':binding}))
            (root/'ao-sessions').mkdir(); (root/'ao-sessions/wfsp-unit').write_text(json.dumps({'path':str(path),'binding':binding}))
            with self.assertRaisesRegex(GateError,'recover'): agent_main('claude',[])
            (folder/'ao-result.json').write_text(json.dumps({'binding':binding,'answer':'cached-unit'}))
            with patch('workflowkit.ao.api_worker') as worker,patch('builtins.print'):
                self.assertEqual(agent_main('claude',[]),0); worker.assert_not_called()
            with self.assertRaisesRegex(GateError,'BINDING'): agent_main('opencode',[])

if __name__=='__main__': unittest.main()
