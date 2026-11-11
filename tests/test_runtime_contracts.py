import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from workflowkit.engine import checks_successful,process_alive
from workflowkit.harness import execute,clean_env
from workflowkit.store import Store,GateError

class RuntimeTests(unittest.TestCase):
    def test_ci_cannot_accept_missing_or_skipped_checks(self):
        good={'status':'COMPLETED','conclusion':'SUCCESS'}
        self.assertTrue(checks_successful([good,{'state':'SUCCESS'}]))
        for value in ([],[{}],[{'state':'FAILURE'}],[good|{'conclusion':'SKIPPED'}],
                      [good|{'conclusion':'CANCELLED'}],[good|{'status':'IN_PROGRESS'}]):
            self.assertFalse(checks_successful(value))
    def test_process_timeout_reaps_own_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'events.json'
            with self.assertRaisesRegex(GateError,'TIMEOUT'):
                execute([sys.executable,'-c','import time; time.sleep(30)'],cwd=tmp,prompt='',timeout=.05,log=log)
            self.assertFalse(process_alive(json.loads((Path(tmp)/'process.json').read_text())))
    def test_environment_does_not_inherit_provider_keys(self):
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'unit-only','DEEPSEEK_API_KEY':'unit-only','GITHUB_TOKEN':'unit-only'}):
            env=clean_env()
        for key in ('ANTHROPIC_API_KEY','DEEPSEEK_API_KEY','GITHUB_TOKEN'):
            self.assertNotIn(key,env)
    def test_atomic_paid_reservation_across_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state')
            def reserve(_):
                try: return store.reserve('parallel',100)
                except GateError: return None
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(reserve,range(16)))
            self.assertEqual(sum(x is not None for x in results),1)
            self.assertEqual(store.balance()['accounted_microusd'],100)
    def test_loopback_preload(self):
        preload=Path(__file__).resolve().parents[1]/'scripts/ao-loopback.cjs'
        code="require(process.argv[1]);const s=require('net').createServer();s.listen({port:0,host:'0.0.0.0'},()=>{console.log(JSON.stringify(s.address()));s.close();});"
        result=subprocess.run(['node','-e',code,str(preload)],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['address'],'127.0.0.1')

if __name__=='__main__': unittest.main()
