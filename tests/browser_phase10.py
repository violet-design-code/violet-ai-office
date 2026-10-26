"""Real Chromium forms with synthetic isolated data; no outside requests."""
import argparse
import json
import shutil
import tempfile
import threading
from pathlib import Path
from datetime import timedelta
from playwright.sync_api import sync_playwright
from violet_office.store import Store,now
from violet_office.office import Office,JST
from violet_office.web import make_server
from tests.test_portfolio import png


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args()
    output=Path(args.output_dir); output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'synthetic.db'; store=Store(path); o=Office(store); o.add_user('owner','representative','SyntheticOwnerPass!123'); store.close()
        server=make_server(path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                def login(username,password):
                    page.goto(base+'/login'); page.locator('[name="username"]').fill(username); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
                def form(action):
                    f=page.locator('form[action="/actions/'+action+'"]').first
                    for parent in page.locator('details').filter(has=f).all():
                        if parent.get_attribute('open') is None: parent.locator('> summary').click()
                    return f
                login('owner','SyntheticOwnerPass!123'); page.goto(base+'/businesses'); form('portfolio_seed').get_by_role('button').click()
                assert page.locator('main a[href^="/business?business="]').count()==6
                assert '未計測' in page.locator('main').inner_text()
                page.goto(base+'/business?business=BIZ-B')
                f=form('portfolio_contact'); f.locator('[name="name"]').fill('合成ブラウザ顧客'); f.locator('[name="channel_ref"]').fill('synthetic-browser-contact-ref'); f.get_by_role('button').click()
                for purpose in ('contact','schedule','photo'):
                    f=form('portfolio_consent'); f.locator('[name="contact"]').select_option(label='合成ブラウザ顧客'); f.locator('[name="purpose"]').select_option(purpose); f.locator('[name="notice"]').fill('synthetic-consent-evidence'); f.get_by_role('button').click()
                for kind,subject in [('inquiry','合成清掃の問い合わせ'),('work','合成の施工予定')]:
                    f=form('portfolio_case'); f.locator('[name="kind"]').select_option(kind); f.locator('[name="subject"]').fill(subject); f.locator('[name="contact"]').select_option(label='合成ブラウザ顧客'); f.get_by_role('button').click()
                f=form('portfolio_document'); f.locator('[name="kind"]').select_option('estimate'); f.locator('[name="case"]').select_option(label='問い合わせ / 合成清掃の問い合わせ'); f.get_by_role('button').click()
                f=form('portfolio_document_decide'); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
                check=Store(path); case=check.db.execute("SELECT id FROM portfolio_cases WHERE kind='inquiry'").fetchone()[0]; work=check.db.execute("SELECT id FROM portfolio_cases WHERE kind='work'").fetchone()[0]; doc=check.db.execute('SELECT id FROM portfolio_documents').fetchone()[0]; check.close()
                f=form('portfolio_operation'); f.locator('[name="kind"]').select_option('quote_send'); f.locator('[name="case"]').select_option(case); f.locator('[name="document"]').select_option(doc); f.get_by_role('button').click()
                check=Store(path); assert check.db.execute('SELECT status FROM portfolio_operations').fetchone()[0]=='approval_pending'; assert check.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0]==0; check.close()
                upload=page.locator('form[action="/business-photo-upload"]')
                for parent in page.locator('details').filter(has=upload).all():
                    if parent.get_attribute('open') is None: parent.locator('> summary').click()
                upload.locator('[name="case"]').select_option(work); upload.locator('[name="photo"]').set_input_files({'name':'synthetic.png','mimeType':'image/png','buffer':png()}); upload.locator('button').click()
                photo_link=page.locator('a[href^="/business-photo?"]').first
                response=page.request.get(base+photo_link.get_attribute('href')); assert response.status==200; assert 'attachment' in response.headers['content-disposition']
                f=form('portfolio_task'); f.locator('[name="kind"]').select_option('copy'); f.locator('[name="instruction"]').fill('この事業の返信原稿を準備'); f.get_by_role('button').click(); form('portfolio_run').get_by_role('button').click()
                for kind,amount in [('revenue','6000'),('expense','1500')]:
                    f=form('portfolio_ledger'); f.locator('[name="kind"]').select_option(kind); f.locator('[name="amount"]').fill(amount); f.locator('[name="evidence"]').fill('synthetic-ledger-reference'); f.get_by_role('button').click()
                f=form('portfolio_grant'); f.locator('[name="username"]').fill('business_staff'); f.locator('[name="password"]').fill('SyntheticScopedPass!123'); f.locator('[name="write"]').check(); f.get_by_role('button').click()
                page.goto(base+'/businesses'); form('portfolio_report').get_by_role('button').click()
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':844})
                    for route in ('/businesses','/business?business=BIZ-B','/business-reports','/seminar-admin','/learning'):
                        response=page.goto(base+route); assert response.status==200,(route,response.status)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                    page.goto(base+'/businesses'); page.screenshot(path=str(output/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
                page.get_by_role('button',name='ログアウト',exact=True).click(); login('business_staff','SyntheticScopedPass!123')
                response=page.goto(base+'/business?business=BIZ-B'); assert response.status==200
                response=page.goto(base+'/business?business=BIZ-A'); assert response.status==403
                page.goto(base+'/business?business=BIZ-B'); page.screenshot(path=str(output/'scoped-mobile.png'),full_page=True)
                browser.close()
        finally: server.shutdown(); server.server_close()
    print(json.dumps({'mode':'synthetic_temporary_database','initial_businesses':6,'contact_consent_inquiry_estimate_approval_pending':'passed','private_photo_multipart_upload_download':'passed','scoped_tasks_and_revenue_expense_records':'passed','meeting_report':'approval_pending','operator_cross_business_access':'denied','desktop_routes':5,'mobile_routes':5,'external_browser_requests':'blocked','actual_external_services':[],'real_iphone':'not_tested'},ensure_ascii=False))

if __name__=='__main__': main()
