"""Authenticated lifecycle controls that exist only on a desktop-owned server."""
from contextlib import contextmanager
from http.cookies import SimpleCookie, CookieError
import hmac
import os
from pathlib import Path
import threading


class DesktopGateway:
    def __init__(self, token, instance, workspace):
        if len(token)<32:raise ValueError('Desktop session token is required')
        self.token,self.instance,self.workspace=token,instance,str(Path(workspace).resolve())
        self.cookie='studio_session_'+instance[:16]
        self.lock=threading.RLock();self.active_requests=0;self.draining=False

    def authorized(self, request):
        supplied=request.headers.get('X-Studio-Token','')
        native=bool(supplied) and hmac.compare_digest(supplied,self.token)
        if not native:
            try:
                cookies=SimpleCookie(request.headers.get('Cookie',''))
                supplied=cookies[self.cookie].value if self.cookie in cookies else ''
            except CookieError:return False
            if not hmac.compare_digest(supplied,self.token):return False
        host=f'127.0.0.1:{request.server.server_port}'
        if request.headers.get('Host')!=host:return False
        if request.command=='POST':
            origin=request.headers.get('Origin')
            if origin!=f'http://{host}' and not (native and origin is None):return False
        return True

    @contextmanager
    def mutation(self):
        with self.lock:
            allowed=not self.draining
            if allowed:self.active_requests+=1
        try:yield allowed
        finally:
            if allowed:
                with self.lock:self.active_requests-=1

    def busy(self, app):
        from app.jobs.task_control import child_active, read
        reasons=[]
        with self.lock:
            if self.active_requests:reasons.append(f'คำขอ/อัปโหลด {self.active_requests} รายการ')
            for job in app.JOBS.iterdir():
                if job.is_dir() and (app.task_is_active(job.name) or child_active(job)):
                    reasons.append('Job '+job.name)
            root=app.UPLOADS.parent
            patterns=('previews/*/status.json','model_edits/records/*/status.json',
                      'model_variants/records/*/record.json','model_variants/records/*/glb_v1/status.json',
                      'model_variants/records/*/glb_v1/packages/*/status.json')
            for pattern in patterns:
                for path in root.glob(pattern):
                    state=read(path)
                    if state.get('state') in {'queued','running'} and app.task_lock_alive(path):
                        reasons.append(str(path.relative_to(root)))
            if app.verification_service.status().get('state')=='running':reasons.append('Verify')
            return {'busy':bool(reasons),'reasons':reasons,'active_requests':self.active_requests,
                    'draining':self.draining,'workspace':self.workspace,'instance':self.instance,
                    'pid':os.getpid(),'schema':1}

    def prepare_quit(self, app):
        with self.lock:
            status=self.busy(app)
            if not status['busy']:self.draining=True
            return {**status,'draining':self.draining}

    def handle_get(self, request, app, path):
        if path not in {'/api/desktop/health','/api/desktop/busy'}:return False
        request.send_json(self.busy(app));return True

    def handle_post(self, request, app, path):
        if path=='/api/desktop/prepare-quit':
            result=self.prepare_quit(app);request.send_json(result,409 if result['busy'] else 200);return True
        if path=='/api/desktop/resume':
            with self.lock:self.draining=False
            request.send_json(self.busy(app));return True
        if path=='/api/desktop/shutdown':
            result=self.prepare_quit(app)
            if result['busy']:request.send_json(result,409)
            else:
                request.send_json(result)
                threading.Thread(target=request.server.shutdown,daemon=True).start()
            return True
        return False
