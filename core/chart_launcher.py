"""Opt-in loopback bridge for file-based report buttons; no trading commands."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from .trade_chart import ROOT, open_trade_chart

SESSION = ROOT / '.local' / 'chart_launcher' / 'session.json'
CLIENT = SESSION.with_name('client.js')


def chart_button(native_path: Path, output_path: Path):
    """Local launcher credentials stay in an ignored JS file, never report markup."""
    from html import escape
    from urllib.parse import quote
    if not native_path.resolve().is_relative_to((ROOT/'reports').resolve()):
        return ''
    relative = quote(os.path.relpath(CLIENT, output_path.parent).replace('\\','/'), safe='/')
    report = str(native_path.resolve().relative_to((ROOT/'reports').resolve())).replace('\\','/')
    import hashlib
    digest = hashlib.sha256(native_path.read_bytes()).hexdigest()
    return (f'<p><button type="button" class="mbt-open-chart" data-report="{escape(report,quote=True)}" '
            f'data-report-hash="{digest}" style="padding:12px 18px;background:#173e41;color:#69e5bd;border:1px solid #35516b;border-radius:8px;cursor:pointer">'
            'Open trades in MT5</button> <span class="mbt-chart-status" role="status">'
            'Requires the local chart launcher and a closed MT5 terminal.</span></p>'
            f'<script src="{escape(relative,quote=True)}"></script>')


def _session_request(session, route):
    request = Request(f'http://127.0.0.1:{session["port"]}/{route}',
                      headers={'X-MBT-Token':session['token']},
                      data=b'{}' if route=='stop' else None)
    with urlopen(request, timeout=2) as response:
        return json.load(response)


def start_chart_launcher():
    if SESSION.is_file():
        try:
            session = json.loads(SESSION.read_text())
            if _session_request(session,'health').get('status') == 'ready':
                return {'status':'ready','port':session['port'],'expires_at':session['expires_at'],
                        'note':'Refresh the report page to enable its MT5 buttons.'}
        except (OSError,ValueError,KeyError):
            pass
    SESSION.parent.mkdir(parents=True,exist_ok=True)
    # Remove only stale owned readiness metadata, not reports or user terminal files.
    SESSION.unlink(missing_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    process = subprocess.Popen([sys.executable,'-m','core.chart_launcher'],cwd=str(ROOT),
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline and process.poll() is None:
        if SESSION.is_file():
            try:
                session=json.loads(SESSION.read_text())
                if _session_request(session,'health').get('status')=='ready':
                    return {'status':'ready','port':session['port'],'expires_at':session['expires_at'],
                            'note':'Refresh the HTML report. Buttons open display-only MT5 charts; close MT5 first.'}
            except (OSError,ValueError,KeyError):
                pass
        time.sleep(.1)
    raise RuntimeError('Local chart launcher did not become ready')


def stop_chart_launcher():
    if not SESSION.is_file():
        return {'status':'not_running'}
    try:
        return _session_request(json.loads(SESSION.read_text()),'stop')
    except (OSError,ValueError,KeyError):
        return {'status':'not_running'}


def _serve():
    token=secrets.token_urlsafe(32)
    expires=time.time()+1800
    state={'stop':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def authorized(self):
            # File reports send Origin:null. Web pages from other origins are rejected.
            return (self.headers.get('Origin') in (None,'null','file://') and
                    self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and
                    hmac.compare_digest(self.headers.get('X-MBT-Token',''),token))

        def send_json(self,code,payload):
            self.send_response(code)
            if self.headers.get('Origin') in ('null','file://'):
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_OPTIONS(self):
            if self.headers.get('Origin') not in ('null','file://'):
                self.send_json(403,{'error':'Only local file reports may use the launcher'})
                return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Access-Control-Allow-Methods','POST')
            self.send_header('Access-Control-Allow-Headers','Content-Type,X-MBT-Token')
            self.send_header('Access-Control-Allow-Private-Network','true')
            self.end_headers()

        def do_GET(self):
            if not self.authorized():
                self.send_json(403,{'error':'Unauthorized'})
                return
            self.send_json(200,{'status':'ready'} if self.path=='/health' else {'error':'Unknown route'})

        def do_POST(self):
            if not self.authorized():
                self.send_json(403,{'error':'Unauthorized'})
                return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:
                    raise ValueError('Invalid request size')
                request=json.loads(self.rfile.read(length))
                if self.path=='/stop':
                    state['stop']=True
                    self.send_json(200,{'status':'stopped'})
                    return
                if self.path!='/open' or set(request)!={'report','sha256'}:
                    raise ValueError('Only saved-report chart requests are supported')
                from .trade_chart import _saved_report
                path,_=_saved_report(str(ROOT/'reports'/request['report']),None,None,'in_sample')
                import hashlib
                if not hmac.compare_digest(hashlib.sha256(path.read_bytes()).hexdigest(),request['sha256']):
                    raise ValueError('Report changed after HTML generation; regenerate the report')
                self.send_json(200,open_trade_chart(report_path=str(path)))
            except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
                self.send_json(400,{'error':str(exc)})
    server=HTTPServer(('127.0.0.1',0),Handler)
    server.timeout=.5
    session={'port':server.server_port,'token':token,'expires_at':expires}
    SESSION.parent.mkdir(parents=True,exist_ok=True)
    client=r'''(()=>{
if(window.mbtChartLauncherBound)return;window.mbtChartLauncherBound=true;
const endpoint=ENDPOINT,token=TOKEN,expires=EXPIRES;
document.addEventListener('click',async e=>{
 const button=e.target.closest?.('.mbt-open-chart');if(!button)return;
 const label=button.nextElementSibling;
 if(Date.now()/1000>expires){label.textContent='Launcher expired. Ask the AI to start it, then refresh this report.';return;}
 if(!confirm('Open a display-only MT5 chart of these saved test deals? MT5 must be closed first. No backtest or live trades will run.'))return;
 button.disabled=true;label.textContent='Opening MT5 chart…';
 try{
  const response=await fetch(endpoint+'/open',{method:'POST',headers:{'Content-Type':'application/json','X-MBT-Token':token},
   body:JSON.stringify({report:button.dataset.report,sha256:button.dataset.reportHash})});
  const result=await response.json();
  label.textContent=result.error|| (result.status==='displayed'?'MT5 chart opened with '+result.drawn_deal_markers+' recorded deal markers.':
   'MT5 launch status: '+result.status+'; chart display was not fully confirmed.');
 }catch(error){label.textContent='Cannot reach the local launcher. Ask the AI to start it, refresh, and allow local-network access if your browser asks.';}
 finally{button.disabled=false;}
});})();'''
    client=client.replace('ENDPOINT',json.dumps(f'http://127.0.0.1:{server.server_port}')).replace('TOKEN',json.dumps(token)).replace('EXPIRES',str(expires))
    CLIENT.write_text(client,encoding='utf-8')
    SESSION.write_text(json.dumps(session),encoding='utf-8')
    try:
        while not state['stop'] and time.time()<expires:
            server.handle_request()
    finally:
        server.server_close()
        SESSION.unlink(missing_ok=True)


if __name__=='__main__':
    _serve()
