"""Actual Chromium, explicit synthetic assumptions, no business DB or outside traffic."""
import argparse,json,tempfile,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from violet_office.store import Store
from violet_office.office import Office
from violet_office.web import make_server
from violet_office.integrations.adapters import Principal
from tests.test_development import PARAMS


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args(); output=Path(args.output_dir); output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'synthetic.db'; store=Store(path); office=Office(store); office.add_user('owner','representative','SyntheticOwnerPass!123'); office.portfolio.seed(Principal('representative','owner'))
        office.add_user('staff','business_operator','SyntheticStaffPass!123'); office.portfolio.grant(Principal('representative','owner'),'BIZ-B','staff',True); store.close()
        server=make_server(path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                def login(user,password):
                    page.goto(base+'/login'); page.locator('[name="username"]').fill(user); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
                def getform(action,match=None):
                    f=page.locator('form[action="/actions/'+action+'"]')
                    if match: f=f.filter(has=page.locator('input[name="'+match[0]+'"][value="'+match[1]+'"]'))
                    f=f.first
                    for parent in page.locator('details').filter(has=f).all():
                        if parent.get_attribute('open') is None: parent.locator('> summary').click()
                    return f
                login('owner','SyntheticOwnerPass!123'); page.goto(base+'/development')
                f=getform('bd_create'); f.locator('[name="title"]').fill('合成の鏡清掃派生案'); f.locator('[name="business"]').select_option('BIZ-B'); f.get_by_role('button').click()
                first=page.url.split('id=')[1]; assert '未算定' in page.locator('main').inner_text()
                f=getform('bd_scenario',('name','standard'))
                for key,value in PARAMS.items(): f.locator('[name="'+key+'"]').fill(value)
                f.locator('[name="basis"]').fill('合成の検証仮定。実際の市場価格や収益ではない。'); f.get_by_role('button').click()
                f=getform('bd_proposal'); f.locator('[name="with_content"]').check(); f.get_by_role('button').click()
                check=Store(path); assert check.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0; check.close()
                f=getform('bd_proposal_decide',('decision','approve')); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
                f=getform('bd_plan'); f.locator('[name="horizon"]').select_option('7'); f.locator('[name="kpi"]').fill('計測範囲と仮定の確認'); f.get_by_role('button').click()
                getform('bd_run').get_by_role('button').click()
                f=getform('bd_actual'); f.locator('[name="metric"]').select_option('revenue_yen'); f.locator('[name="value"]').fill('100'); f.locator('[name="mode"]').select_option('mock'); f.locator('[name="evidence"]').fill('合成ブラウザ検証'); f.get_by_role('button').click()
                getform('bd_validation_review').get_by_role('button').click()
                page.goto(base+'/development'); f=getform('bd_create'); f.locator('[name="title"]').fill('合成の独立新規案'); f.get_by_role('button').click(); second=page.url.split('id=')[1]
                page.goto(base+'/development'); f=getform('bd_compare'); f.locator('[name="ideas"]').fill(first+','+second); f.get_by_role('button').click(); comparison=page.url.split(base)[1]
                assert '順位なし' in page.locator('main').inner_text()
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':844})
                    for route in ('/development','/development-idea?id='+first,comparison,'/businesses','/seminar-admin'):
                        response=page.goto(base+route); assert response.status==200,(route,response.status)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                    page.goto(base+'/development'); page.screenshot(path=str(output/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
                page.get_by_role('button',name='ログアウト',exact=True).click(); login('staff','SyntheticStaffPass!123')
                assert page.goto(base+'/development-idea?id='+first).status==200
                assert page.goto(base+'/development-idea?id='+second).status==403
                browser.close()
                check=Store(path)
                assert check.db.execute('SELECT COUNT(*) FROM portfolio_ledger').fetchone()[0]==0
                assert check.db.execute("SELECT COUNT(*) FROM bd_actuals WHERE mode='actual'").fetchone()[0]==0
                assert check.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0]==0
                check.close()
        finally: server.shutdown(); server.server_close()
    print(json.dumps({'mode':'synthetic_temporary_database','idea_creation':'passed','explicit_assumption_simulation':'passed','proposal_approval_before_tasks':'passed','internal_plan_only':'passed','comparison_missing_data_unranked':'passed','actuals_separated_from_mock_forecast':'passed','cross_business_access':'denied','desktop_routes':5,'mobile_routes':5,'external_browser_requests':'blocked','real_iphone':'not_tested'},ensure_ascii=False))

if __name__=='__main__': main()
