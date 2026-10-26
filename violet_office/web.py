"""VIOLET AI OFFICE: loopback-only development server, no external dispatch route."""
import argparse
import getpass
import hmac
import json
import os
import sqlite3
import threading
import time
from collections import deque
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .store import Store
from .office import Office
from . import web_views as views
from . import growth_views
from . import learning_views
from . import phase9_views
from . import portfolio_views
from . import development_views
from . import sales_views
from . import workflow_views
from . import seminar_support_views
from . import business_foundation_views

COOKIE='violet_office_session'
SECURITY={
    'Content-Security-Policy':"default-src 'none'; style-src 'self'; script-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
    'Cache-Control':'no-store','Permissions-Policy':'camera=(), microphone=(), geolocation=()',
}


class OfficeApp:
    def __init__(self,db_path,port):
        self.db_path=str(db_path)
        self.port=port
        self.hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        self._rate_lock=threading.Lock()
        self._requests=deque()
        self._clock=time.monotonic

    def response(self,status,body='',extra=None,content_type='text/html; charset=utf-8'):
        raw=body.encode() if isinstance(body,str) else body
        return status,{**SECURITY,'Content-Type':content_type,**(extra or {})},raw

    @staticmethod
    def cookie(token,clear=False):
        return f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={0 if clear else 28800}'

    def handle(self,method,target,headers,body=b''):
        if headers.get('Host') not in self.hosts:
            return self.response(403,'接続先を確認してください。')
        if len(target)>2048 or len(body)>(3*1024*1024 if target=='/business-photo-upload' else 131072):
            return self.response(413,'リクエストが大きすぎます。')
        if method not in ('GET','POST'):
            return self.response(405,'許可されていない操作です。')
        with self._rate_lock:
            current=self._clock()
            while self._requests and self._requests[0]<=current-60:
                self._requests.popleft()
            if len(self._requests)>=180:
                return self.response(429,'リクエストが多いため、時間をおいてください。',{'Retry-After':'60'})
            self._requests.append(current)
        try:
            parsed=urlsplit(target)
        except ValueError:
            return self.response(400,'接続先を確認してください。')
        if parsed.scheme or parsed.netloc:
            return self.response(400,'入力を確認してください。')
        path=parsed.path
        if method=='GET' and path=='/assets/office.css':
            css=Path(__file__).with_name('web_assets').joinpath('office.css').read_bytes()
            return self.response(200,css,content_type='text/css; charset=utf-8')
        store=None
        office=None
        try:
            store=Store(self.db_path)
            office=Office(store)
            cookie=SimpleCookie()
            cookie.load(headers.get('Cookie',''))
            token=cookie[COOKIE].value if COOKIE in cookie else ''
            session=office.session(token)
            if method=='POST':
                if headers.get('Origin')!='http://'+headers['Host'] or headers.get('Sec-Fetch-Site')=='cross-site':
                    return self.response(403,'送信元を確認してください。')
                if path=='/business-photo-upload':
                    from .portfolio_upload import parse
                    if not headers.get('Content-Type','').startswith('multipart/form-data;'): return self.response(415,'写真フォームを確認してください。')
                    data=parse(headers['Content-Type'],body)
                else:
                    if headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded':
                        return self.response(415,'フォーム形式を確認してください。')
                    data=parse_qs(body.decode('utf-8'),keep_blank_values=True,max_num_fields=40)
                    if any(len(v)!=1 for v in data.values()): return self.response(400,'重複した入力です。')
                    data={k:v[0] for k,v in data.items()}
                if not session or not hmac.compare_digest(data.get('csrf',''),session['csrf']):
                    office.audit('anonymous','csrf_rejected')
                    return self.response(403,'セッションまたは送信の確認に失敗しました。再ログインしてください。')
                if path=='/login':
                    try:
                        new_token,_=office.login(data.get('username',''),data.get('password',''))
                    except PermissionError:
                        return self.response(401,views.login(session,'ログインできません。入力を確認し、試行が多い場合は時間をおいてください。'))
                    office.logout(token)
                    return self.response(303,extra={'Location':'/','Set-Cookie':self.cookie(new_token)})
                if not session.get('username'):
                    return self.response(401,'ログインが必要です。')
                if path=='/logout':
                    office.audit(session['username'],'logout')
                    office.logout(token)
                    return self.response(303,extra={'Location':'/login','Set-Cookie':self.cookie('',True)})
                if path=='/business-photo-upload':
                    result=office.commands(session,data.pop('nonce',''),'portfolio_photo',{k:v for k,v in data.items() if k!='csrf'})
                    return self.response(303,extra={'Location':result})
                if path.startswith('/actions/'):
                    action=path[len('/actions/'):]
                    result=office.commands(session,data.pop('nonce',''),action,{k:v for k,v in data.items() if k!='csrf'})
                    return self.response(303,extra={'Location':result})
                return self.response(404,'操作が見つかりません。')
            if path=='/login':
                if session and session.get('username'):
                    return self.response(303,extra={'Location':'/'})
                if not session:
                    token,_=office.new_session()
                    session=office.session(token)
                return self.response(200,views.login(session),{'Set-Cookie':self.cookie(token)})
            if not session or not session.get('username'):
                return self.response(303,extra={'Location':'/login'})
            if session['role']=='participant' and path not in ('/seminars','/seminar-participation'):
                return self.response(303,extra={'Location':'/seminars'})
            if session['role']=='business_operator' and path not in ('/businesses','/business','/business-photo','/development','/development-idea','/sales','/sales-deal','/sales-dashboard','/workflow','/seminar-office','/business-foundation','/business-operations-data','/business-task-kpis'):
                return self.response(303,extra={'Location':'/businesses'})
            params=parse_qs(parsed.query,max_num_fields=10)
            if any(len(v)!=1 for v in params.values()):
                raise ValueError('Duplicate parameter')
            ref=params.get('id',[None])[0]
            if path in ('/business-operations-data','/business-task-kpis'):
                from .integrations.adapters import Principal
                allowed={'task','start','end'} if path=='/business-task-kpis' else {'business','start','end'}
                if set(params)-allowed: raise ValueError('Unknown aggregate query field')
                actor=Principal(session['role'],session['username']); start=params.get('start',[''])[0]; end=params.get('end',[''])[0]
                if path=='/business-task-kpis': result=office.business_operations.for_task(actor,params.get('task',[''])[0],start,end)
                elif params.get('business'): result=office.business_operations.summary(actor,params['business'][0],start,end)
                else: result=office.business_operations.global_summary(actor,start,end)
                return self.response(200,json.dumps(result,ensure_ascii=False),content_type='application/json; charset=utf-8')
            if path=='/business-photo':
                from .integrations.adapters import Principal
                mime,blob=office.portfolio.photo_bytes(Principal(session['role'],session['username']),params.get('business',[''])[0],ref)
                return self.response(200,blob,{'Content-Disposition':'attachment; filename="photo.'+('png' if mime=='image/png' else 'jpg')+'"'},content_type=mime)
            pages={
                '/business-foundation':('事業拡張の基盤設定',lambda:business_foundation_views.home(office,session,params)),
                '/seminar-office':('セミナー運営・面談導線',lambda:seminar_support_views.office(office,session,params)),
                '/seminar-participation':('セミナー申込・参加希望',lambda:seminar_support_views.participant(office,session,params)),
                '/workflow':('AI社員の業務・営業支援',lambda:workflow_views.home(office,session,params)),
                '/sales':('営業・顧客管理',lambda:sales_views.home(office,session,params)),
                '/sales-deal':('営業案件の詳細',lambda:sales_views.detail(office,session,params,ref)),
                '/sales-dashboard':('売上・営業ダッシュボード',lambda:sales_views.dashboard(office,session,params)),
                '/development':('新規事業開発',lambda:development_views.home(office,session)),
                '/development-idea':('事業アイデア・検証',lambda:development_views.detail(office,session,ref)),
                '/development-compare':('事業候補の比較',lambda:development_views.comparison(office,session,ref)),
                '/businesses':('統合経営ダッシュボード',lambda:portfolio_views.home(office,session,params)),
                '/business':('事業別管理',lambda:portfolio_views.business(office,session,params)),
                '/business-reports':('AI経営会議',lambda:portfolio_views.reports(office,session)),
                '/selection':('AI社員の能力評価・選抜',lambda:phase9_views.selection(office,session)),
                '/seminar-admin':('代表専用セミナー管理',lambda:phase9_views.admin(office,session,params)),
                '/seminars':('セミナーのご案内',lambda:phase9_views.portal(office,session,ref)),
                '/learning':('知識・フィードバック・改善',lambda:learning_views.dashboard(office,session,params.get('evaluation',[None])[0],params.get('q',[''])[0],params.get('agent',['AI09'])[0],params.get('kind',['sns'])[0])),
                '/employee-learning':('社員の業務・改善履歴',lambda:learning_views.employee(office,session,ref)),
                '/growth':('目標・定期業務',lambda:growth_views.growth(office,session,params.get('month',[None])[0])),
                '/reports':('業務レポート',lambda:growth_views.reports(office,session,ref)),
                '/':('社長室ダッシュボード',lambda:views.home(office,session)),
                '/employees':('AI社員管理',lambda:views.employees(office,session,ref)),
                '/instructions':('統括本部長への指示',lambda:views.instructions(office,session)),
                '/requests':('依頼の進捗',lambda:views.request_view(office,session,ref)),
                '/approvals':('承認センター',lambda:views.approvals(office,session,params.get('campaign',[None])[0])),
                '/analytics':('集客・営業分析',lambda:views.analytics(office,session)),
                '/costs':('AI経費管理',lambda:views.costs(office,session)),
                '/monitor':('システム監視',lambda:views.monitor(office,session)),
            }
            if path not in pages:
                return self.response(404,'画面が見つかりません。')
            title,render=pages[path]
            return self.response(200,views.layout(title,render(),session,path))
        except PermissionError:
            if office:
                office.audit('authenticated','permission_denied')
                if path.startswith('/actions/ops_') and session and session.get('username'):
                    from .integrations.adapters import Principal
                    office.business_operations.audit(Principal(session['role'],session['username']),data.get('business'),'operations_command','denied')
            return self.response(403,'この操作の権限がありません。')
        except (ValueError,KeyError,TypeError,UnicodeError,CookieError,RecursionError):
            if office:
                office.audit('authenticated','invalid_input_or_state')
            return self.response(400,views.layout('操作を完了できません','<div class="notice">入力・タスク状態・監査の未確認項目を確認してください。処理は取り消されました。秘密値・個人情報は表示しません。</div><a href="/">社長室へ戻る</a>'))
        except sqlite3.Error:
            return self.response(503,'データベースを確認してください。安全のため処理を停止しました。')
        except Exception:
            if office:
                try:
                    office.audit('authenticated','unexpected_failure')
                except sqlite3.Error:
                    pass
            return self.response(500,'内部処理を停止しました。監査ログを確認してください。秘密値は表示しません。')
        finally:
            if store:
                store.close()


class BoundedLocalServer(ThreadingHTTPServer):
    max_workers=16
    request_queue_size=16
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self._workers=threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args,**kwargs)
    def process_request(self,request,client_address):
        if not self._workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except BaseException:
            self._workers.release()
            raise
    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self._workers.release()
    def handle_error(self,request,client_address):
        # Raw exceptions and HTTP request data must not reach stdout/stderr.
        pass


def make_server(db_path,port=8765):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()
        server_version='VioletLocal'
        sys_version=''
        def log_message(self,*args):
            # Never emit URLs, credentials, cookies or request bodies to access logs.
            pass
        def do_GET(self):
            self.respond('GET')
        def do_POST(self):
            self.respond('POST')
        def respond(self,method):
            self.connection.settimeout(5)
            if len(self.headers.get_all('Host',[]))!=1 or len(self.headers.get_all('Content-Length',[]))>1:
                self.send_error(400)
                return
            length=self.headers.get('Content-Length','0')
            if self.headers.get('Transfer-Encoding') or not length.isdigit() or int(length)>(3*1024*1024 if self.path=='/business-photo-upload' else 131072):
                self.send_error(413)
                return
            try:
                body=self.rfile.read(int(length)) if method=='POST' else b''
                status,headers,payload=self.server.app.handle(method,self.path,self.headers,body)
            except (TimeoutError,ConnectionError):
                self.close_connection=True
                return
            self.send_response(status)
            for k,v in headers.items():
                self.send_header(k,v)
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    # Bound to loopback unconditionally. No option exposes this on 0.0.0.0.
    server=BoundedLocalServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.app=OfficeApp(db_path,server.server_port)
    return server


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description='VIOLET AI OFFICE ローカル専用')
    parser.add_argument('command',choices=['init','serve'])
    parser.add_argument('--db',default='var/office.sqlite3')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--open-browser',action='store_true',help='PC内のブラウザを開く')
    args=parser.parse_args()
    if os.environ.get('INTEGRATION_MODE','mock') not in ('mock','local','test'):
        parser.error('本番モードは未対応です。外部接続は開始しません')
    path=Path(args.db)
    if args.command=='serve' and not path.is_file():
        parser.error('先にinitで代表アカウントを作成してください')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(path)
    try:
        path.chmod(0o600)
        office=Office(store)
        if args.command=='init':
            if office.db.execute('SELECT COUNT(*) FROM office_users').fetchone()[0]:
                parser.error('アカウントは作成済みです。既存設定は上書きしません')
            password=getpass.getpass('代表パスワード（12文字以上）: ')
            if password!=getpass.getpass('代表パスワード確認: '):
                parser.error('一致しません')
            viewer=getpass.getpass('閲覧者パスワード（12文字以上）: ')
            if viewer!=getpass.getpass('閲覧者パスワード確認: ') or viewer==password:
                parser.error('一致しない、または代表と同じパスワードです')
            def add():
                office.add_user('representative','representative',password)
                office.add_user('viewer','viewer',viewer)
            store._transaction(add)
            print('代表・閲覧者アカウントを作成しました。秘密値は保存ログに出力しません。')
            return
        if not office.db.execute('SELECT 1 FROM office_users').fetchone():
            parser.error('代表アカウントがありません。initを実行してください')
    except ValueError:
        parser.error('パスワード等の条件を確認してください')
    finally:
        store.close()
    if not 1024<=args.port<=65535:
        parser.error('ポートは1024〜65535を指定してください')
    server=make_server(path,args.port)
    print(f'VIOLET AI OFFICE 起動（PC内限定・ポート{args.port}）。ブラウザの開き方はdocs/phase-5.mdを参照してください。')
    if args.open_browser:
        import threading,webbrowser
        threading.Timer(0.3,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
