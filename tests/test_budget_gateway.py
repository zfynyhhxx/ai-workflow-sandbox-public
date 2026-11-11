import concurrent.futures
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from workflowkit.gateway import Gateway,RESERVE,usage_of
from workflowkit.store import Store,GateError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state')
    def test_reserve_and_settle(self):
        r=self.store.reserve('a',RESERVE); self.store.settle(r,20,{'input_tokens':10})
        self.assertEqual(self.store.balance()['accounted_microusd'],20)
        self.assertIsNone(self.store.balance()['actual_cost'])
    def test_global_concurrency(self):
        self.store.reserve('a',RESERVE)
        with self.assertRaisesRegex(GateError,'CONCURRENCY'): self.store.reserve('b',RESERVE)
    def test_unknown_keeps_full_reservation(self):
        r=self.store.reserve('a',RESERVE); self.store.settle(r)
        self.assertEqual(self.store.balance()['accounted_microusd'],RESERVE)
    def test_budget_never_resets_on_restart(self):
        for _ in range(3):
            r=self.store.reserve('a',RESERVE); self.store.settle(r)
        reopened=Store(self.store.root)
        with self.assertRaisesRegex(GateError,'BUDGET'): reopened.reserve('b',RESERVE)
    def test_cannot_double_settle(self):
        r=self.store.reserve('a',1); self.store.settle(r,1)
        with self.assertRaises(GateError): self.store.settle(r,0)
    def test_over_reserved_usage_fail_closed(self):
        r=self.store.reserve('a',1)
        with self.assertRaises(GateError): self.store.settle(r,2)
        self.assertEqual(self.store.balance()['available_microusd'],999999)
    def test_approval_bound_to_task_and_rules(self):
        task={'task':'a','rules':'r1'}; self.store.approve(task,'run')
        self.assertTrue(self.store.authorized(task,'run'))
        self.assertFalse(self.store.authorized({'task':'b','rules':'r1'},'run'))
        self.assertFalse(self.store.authorized({'task':'a','rules':'r2'},'run'))
        self.assertFalse(self.store.authorized(task,'merge'))
    def test_tamper_signature_rejected(self):
        task={'task':'a'}; self.store.approve(task,'run')
        with self.store.connect() as c: c.execute("UPDATE approvals SET signature='forged'")
        self.assertFalse(self.store.authorized(task,'run'))
    def test_expired_approval_rejected(self):
        task={'task':'a'}; self.store.approve(task,'run',ttl=0.01); time.sleep(.02)
        self.assertFalse(self.store.authorized(task,'run'))
    def test_dependency_lock(self):
        with self.store.lock('worker','a'):
            with self.assertRaises(GateError):
                with self.store.lock('worker','b'): pass
        with self.store.lock('worker','b'): pass
    def test_usage_json_and_anthropic_sse(self):
        self.assertEqual(usage_of(b'{"usage":{"prompt_tokens":10,"completion_tokens":20}}','application/json')[0],27)
        s=b'data: {"message":{"usage":{"input_tokens":10}}}\n\ndata: {"usage":{"output_tokens":20}}\n\n'
        self.assertEqual(usage_of(s,'text/event-stream')[0],27)
    def test_missing_usage_unknown(self): self.assertEqual(usage_of(b'{}','application/json'),(None,None))
    def test_cache_usage_cannot_reduce_billing(self):
        for cache in (-1,True,'invalid'):
            body=json.dumps({'usage':{'input_tokens':10,'output_tokens':20,'cache_read_input_tokens':cache}}).encode()
            with self.assertRaises(GateError): usage_of(body,'application/json')


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.store=Store(self.root/'state')
        key=self.root/'key'; key.write_text('unit-test-provider'); key.chmod(0o600)
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert data['max_tokens']<=4096
                body=json.dumps({'usage':{'prompt_tokens':10,'completion_tokens':20},'choices':[]}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        self.upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        threading.Thread(target=self.upstream.serve_forever,daemon=True).start()
        self.addCleanup(self.upstream.server_close); self.addCleanup(self.upstream.shutdown)
        self.gateway=Gateway(self.store,self.root/'gateway',key,'test',
                             upstream='http://127.0.0.1:'+str(self.upstream.server_port))
        self.gateway.__enter__(); self.addCleanup(self.gateway.__exit__)
    def request(self,path='/v1/chat/completions',model='deepseek-flash',token=None):
        class Connection(http.client.HTTPConnection):
            def connect(inner):
                inner.sock=socket.socket(socket.AF_UNIX); inner.sock.connect(str(self.gateway.path))
        c=Connection('localhost'); c.request('POST',path,json.dumps({'model':model,'messages':[], 'max_tokens':100000}),
                    {'Authorization':'Bearer '+(token or self.gateway.token),'Content-Type':'application/json'})
        r=c.getresponse(); result=(r.status,r.read()); c.close(); return result
    def test_accounted_request(self):
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.store.balance()['accounted_microusd'],27)
    def test_native_background_requests_are_serialized_not_dropped(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.request()[0],range(2)))
        self.assertEqual(results,[200,200])
        self.assertEqual(self.store.balance()['accounted_microusd'],54)
        self.assertEqual(self.store.balance()['active_requests'],0)
    def test_wrong_token_never_calls_provider(self):
        self.assertEqual(self.request(token='bad')[0],403)
        self.assertEqual(self.store.balance()['accounted_microusd'],0)
    def test_wrong_endpoint_blocked(self): self.assertEqual(self.request(path='/approve')[0],403)
    def test_expensive_model_blocked(self):
        self.assertNotEqual(self.request(model='deepseek-v4-pro')[0],200)
        self.assertEqual(self.store.balance()['accounted_microusd'],0)


if __name__=='__main__': unittest.main()
