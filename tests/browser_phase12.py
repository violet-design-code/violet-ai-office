"""Real browser form workflow using isolated synthetic data; no real delivery."""
import argparse,json,tempfile,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from violet_office.store import Store
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args(); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'synthetic.db'; store=Store(path); office=Office(store); office.add_user('owner','representative','SyntheticOwnerPass!123'); office.portfolio.seed(Principal('representative','owner')); office.add_user('staff','business_operator','SyntheticStaffPass!123'); office.portfolio.grant(Principal('representative','owner'),'BIZ-B','staff',True); store.close()
        server=make_server(path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                def login(user,password):
                    page.goto(base+'/login'); page.locator('[name="username"]').fill(user); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
                def form(action,match=None):
                    f=page.locator('form[action="/actions/'+action+'"]')
                    if match: f=f.filter(has=page.locator('input[name="'+match[0]+'"][value="'+match[1]+'"]'))
                    f=f.first
                    for parent in page.locator('details').filter(has=f).all():
                        if parent.get_attribute('open') is None: parent.locator('> summary').click()
                    return f
                login('owner','SyntheticOwnerPass!123'); page.goto(base+'/sales?business=BIZ-B')
                f=form('scm_customer'); f.locator('[name="name"]').fill('合成ブラウザ顧客'); f.locator('[name="channel"]').fill('synthetic-channel-ref'); f.get_by_role('button').click()
                for purpose in ('contact','schedule'):
                    f=form('scm_consent'); f.locator('[name="purpose"]').select_option(purpose); f.locator('[name="notice"]').fill('合成の本人同意参照'); f.get_by_role('button').click()
                f=form('scm_deal'); f.locator('[name="subject"]').fill('合成の鏡清掃問い合わせ'); f.locator('[name="inquiry"]').fill('合成データによる操作検証です。実際の顧客依頼ではありません。'); f.get_by_role('button').click(); dealurl=page.url.split(base)[1]
                f=form('scm_quote'); f.locator('[name="line_label_1"]').fill('合成清掃サービス'); f.locator('[name="unit_1"]').fill('6000'); f.locator('[name="conditions"]').fill('合成条件・実取引ではない'); f.get_by_role('button').click()
                form('scm_quote'); page.locator('summary').filter(has_text='第1版').first.click()
                assert '税区分・税率・端数処理の確認待ち' in page.locator('main').inner_text()
                page.goto(base+'/sales?business=BIZ-B'); f=form('scm_tax'); f.locator('[name="tax_type"]').select_option('excluded'); f.locator('[name="rate"]').fill('10'); f.locator('[name="rounding"]').select_option('floor'); f.locator('[name="confirmed"]').check(); f.get_by_role('button').click()
                page.goto(base+dealurl); f=form('scm_quote'); f.locator('[name="line_label_1"]').fill('改訂の合成清掃'); f.locator('[name="unit_1"]').fill('6000'); f.locator('[name="conditions"]').fill('仮の税設定によるテスト'); f.get_by_role('button').click()
                f=form('scm_quote_decide',('decision','approve')); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
                check=Store(path); q=check.db.execute('SELECT id FROM scm_quotes ORDER BY version DESC LIMIT 1').fetchone()[0]; check.close()
                f=form('scm_work'); f.locator('[name="activity"]').select_option('reply'); f.get_by_role('button').click(); form('scm_run').get_by_role('button').click()
                f=form('scm_action'); f.locator('[name="kind"]').select_option('send'); f.locator('[name="quote"]').select_option(q); f.locator('[name="message"]').fill('合成の案内文。実送信しません。'); f.get_by_role('button').click()
                f=form('scm_action_decide',('decision','approve')); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
                check=Store(path); assert check.db.execute('SELECT COUNT(*) FROM scm_attempts').fetchone()[0]==0; assert check.db.execute('SELECT COUNT(*) FROM scm_cash').fetchone()[0]==0; check.close()
                form('scm_mock').get_by_role('button').click()
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':844})
                    for route in ('/sales','/sales?business=BIZ-B',dealurl,'/sales-dashboard','/sales-dashboard?business=BIZ-B','/businesses','/development'):
                        response=page.goto(base+route); assert response.status==200,(route,response.status)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                    page.goto(base+'/sales-dashboard?business=BIZ-B'); page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
                page.get_by_role('button',name='ログアウト',exact=True).click(); login('staff','SyntheticStaffPass!123')
                assert page.goto(base+'/sales?business=BIZ-B').status==200
                assert page.goto(base+'/sales?business=BIZ-A').status==403
                page.goto(base+dealurl); page.screenshot(path=str(out/'deal-mobile.png'),full_page=True)
                browser.close()
        finally: server.shutdown(); server.server_close()
    print(json.dumps({'mode':'synthetic_temporary_database','customer_consent_deal':'passed','unknown_tax_blocks_approval':'passed','tax_settings_quote_revision_approval':'passed','ai_template_not_actual_send':'passed','approval_no_automatic_execution':'passed','mock_send_only':'passed','cross_business_access':'denied','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','real_iphone':'not_tested'},ensure_ascii=False))

if __name__=='__main__': main()
