"""VIOLET AI OFFICE: loopback-only development server, no external dispatch route."""
import argparse
import getpass
import hmac
import os
import sqlite3
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .store import Store
from .office import Office
from . import web_views as views

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

    def response(self,status,body='',extra=None,content_type='text/html; charset=utf-8'):
        raw=body.encode() if isinstance(body,str) else body
        return status,{**SECURITY,'Content-Type':content_type,**(extra or {})},raw

    @staticmethod
    def cookie(token,clear=False):
        return f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={0 if clear else 28800}'

    def handle(self,method,target,headers,body=b''):
        if headers.get('Host') not in self.hosts:
            return self.response(403,'接続先を確認してください。')
        if len(target)>2048 or len(body)>131072:
            return self.response(413,'リクエストが大きすぎます。')
        if method not in ('GET','POST'):
            return self.response(405,'許可されていない操作です。')
        parsed=urlsplit(target)
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
                if headers.get('Content-Type','').split(';')[0]!='application/x-www-form-urlencoded':
                    return self.response(415,'フォーム形式を確認してください。')
                data=parse_qs(body.decode('utf-8'),keep_blank_values=True,max_num_fields=40)
                if any(len(v)!=1 for v in data.values()):
                    return self.response(400,'重複した入力です。')
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
            params=parse_qs(parsed.query,max_num_fields=10)
            if any(len(v)!=1 for v in params.values()):
                raise ValueError('Duplicate parameter')
            ref=params.get('id',[None])[0]
            pages={
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


def make_server(db_path,port=8765):
    class Handler(BaseHTTPRequestHandler):
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
            length=self.headers.get('Content-Length','0')
            if self.headers.get('Transfer-Encoding') or not length.isdigit() or int(length)>131072:
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
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
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
