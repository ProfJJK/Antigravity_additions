"""Narrow authenticated loopback API. Workers have no claim or completion endpoint."""
from __future__ import annotations
import hmac
from copy import deepcopy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit


def redact(value):
    if isinstance(value,dict):
        return {key:(deepcopy(item) if key in ('payload','output','artifacts') else redact(item))
                for key,item in value.items()
                if key not in ('fencing_token','attempt_id','lease_owner','owner')}
    if isinstance(value,list):
        return [redact(item) for item in value]
    return value


def public_workflow(workflow: dict) -> dict:
    # Fencing tokens and leases are control-plane authority, never model/tool output.
    return redact(workflow)


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self,runtime,token: str):
        if len(token)<32:
            raise ValueError('Controller token must contain at least 32 random characters')
        self.runtime,self.token = runtime,token
        self._requests = threading.BoundedSemaphore(16)
        super().__init__(('127.0.0.1',runtime.config.port),Handler)

    def get_request(self):
        request,address = super().get_request()
        request.settimeout(5)
        return request,address

    def process_request(self,request,client_address):
        if not self._requests.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except BaseException:
            self._requests.release()
            raise

    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self._requests.release()


class Handler(BaseHTTPRequestHandler):
    server: ControlServer
    def log_message(self,*args):
        # Default HTTP logs may include user-supplied paths; structured service logs suffice.
        return None

    def reply(self,code,data):
        payload=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        supplied=self.headers.get('Authorization','')
        if not hmac.compare_digest(supplied.encode('utf-8'),('Bearer '+self.server.token).encode('utf-8')):
            self.reply(401,{'error':'Unauthorized'})
            return
        runtime=self.server.runtime
        path=urlsplit(self.path).path
        try:
            if self.command=='GET' and path=='/health':
                self.reply(200,runtime.status())
            elif self.command=='GET' and path.startswith('/workflow/'):
                self.reply(200,public_workflow(runtime.store.workflow(path[len('/workflow/'):])) )
            elif self.command=='POST' and path in ('/submit','/cancel'):
                size=int(self.headers.get('Content-Length','0'))
                if not 1<=size<=4*1024*1024:
                    raise ValueError('Request body must be 1..4194304 bytes')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):
                    raise ValueError('Request must be a JSON object')
                if path=='/submit':
                    count=data.get('chapter_count',6)
                    if type(count) is not int or not 1<=count<=len(runtime.config.workers):
                        raise ValueError('chapter_count must fit the provisioned distinct worker identity pool')
                    workflow=runtime.store.submit(data['objective'],data.get('requirements',['REQ-001']),
                                                  count,data.get('workflow_id'))
                    self.reply(202,public_workflow(workflow))
                else:
                    runtime.cancel(data['workflow_id'])
                    self.reply(200,public_workflow(runtime.store.workflow(data['workflow_id'])))
            else:
                self.reply(404,{'error':'Unknown operation; claim and completion are private to the Warden'})
        except (ValueError,KeyError,TypeError) as exc:
            self.reply(400,{'error':str(exc)})
        except Exception:
            self.reply(500,{'error':'Controller operation failed; inspect the protected Warden log'})


class ControlClient:
    def __init__(self,port:int,token_file:str|Path):
        if not 1024<=port<=65535:
            raise ValueError('Invalid controller port')
        self.port,self.token_file=port,Path(token_file)

    def call(self,operation:str,data:dict|None=None):
        token=self.token_file.read_text(encoding='utf-8').strip()
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=30)
        try:
            body=json.dumps(data) if data is not None else None
            connection.request('POST' if data is not None else 'GET',operation,body,
                               {'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=connection.getresponse()
            value=json.loads(response.read())
            if response.status>=400:
                raise RuntimeError(value.get('error','Controller rejected request'))
            return value
        finally:
            connection.close()
