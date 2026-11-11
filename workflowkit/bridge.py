"""在无外网的 Worker 网络命名空间内，将 localhost API 转发到唯一挂载的计量 socket。"""
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
from pathlib import Path


def main():
    config=json.loads(Path('/runtime/launch.json').read_text())
    class Connection(http.client.HTTPConnection):
        def connect(self):
            import socket
            self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.sock.connect('/broker/api.sock')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            c=Connection('localhost',timeout=150)
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            headers={'Content-Type':'application/json','Authorization':'Bearer '+config['token']}
            c.request('POST',self.path,body,headers)
            r=c.getresponse(); data=r.read()
            self.send_response(r.status)
            self.send_header('Content-Type',r.getheader('Content-Type','application/json'))
            self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); c.close()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    env=os.environ.copy()
    env.update(config['env'])
    env.update({'ANTHROPIC_BASE_URL':base+'/anthropic','ANTHROPIC_AUTH_TOKEN':config['token'],
                'DEEPSEEK_API_KEY':config['token']})
    if config['harness']=='opencode':
        native=config['opencode']
        native['providers']['wf-deepseek']['settings']['baseURL']=base+'/v1'
        native['providers']['wf-deepseek']['settings']['apiKey']=config['token']
        folder='/home/worker/.config/opencode'; os.makedirs(folder,exist_ok=True)
        Path(folder+'/opencode.json').write_text(json.dumps(native))
    try:
        result=subprocess.run(config['command'],env=env)
    finally:
        server.shutdown(); server.server_close()
    return result.returncode


if __name__=='__main__':
    sys.exit(main())
