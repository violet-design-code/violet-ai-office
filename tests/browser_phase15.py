"""Minimal business foundation UI/API checks, synthetic temp DB only."""
import argparse,json,threading,shutil
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright
from tests.test_business_foundation import BusinessFoundationTests
from violet_office.store import Store
from violet_office.office import Office
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args(); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    fixture=BusinessFoundationTests(); fixture.setUp(); fixture.operator()
    server=make_server(fixture.path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            def login(user,password):
                page.goto(base+'/login'); page.locator('[name="username"]').fill(user); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
            def form(action):
                page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)'); return page.locator('form[action="/actions/'+action+'"]') .first
            login('owner','SyntheticOwnerPass!123'); page.goto(base+'/business-foundation'); f=form('bf_create'); f.locator('[name="name"]').fill('合成の追加事業'); f.locator('[name="overview"]').fill('合成の事業概要・実提供を意味しない'); f.locator('[name="category"]').fill('将来追加する自由カテゴリ'); f.locator('[name="started_on"]').fill(fixture.day.isoformat()); f.get_by_role('button').click()
            b=page.url.split('business=')[1]; f=form('bf_update'); f.locator('[name="enabled"]').select_option('0'); f.get_by_role('button').click(); assert '無効・通常データ更新/AI実行を停止' in page.locator('main').inner_text()
            f=form('bf_update'); f.locator('[name="enabled"]').select_option('1'); f.get_by_role('button').click()
            csrf=page.locator('input[name="csrf"]').first.get_attribute('value')
            response=page.request.post(base+'/actions/bf_assign',form={'csrf':csrf,'nonce':uuid4().hex,'business':b,'agent':'AI17','role_note':'既存役職の補足','scope_note':'合成事業の内部返信準備','permissions':json.dumps(['business_read','work']),'active':'1','revision':'0'},headers={'Origin':base})
            assert response.status==200,response.status
            page.goto(base+'/business-foundation?business='+b); assert page.locator('form[action="/actions/bf_assign"]').count()==0; assert 'AI17' in page.locator('main').inner_text()
            check=Store(fixture.path); office=Office(check); office.portfolio.grant(fixture.owner,b,'staff',True); assert office.business_foundation.read_for_ai(fixture.owner,b,'AI17')['business_id']==b; assert check.db.execute('SELECT COUNT(*) FROM portfolio_ledger WHERE business_id=?',(b,)).fetchone()[0]==0; check.close()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for route in ('/business-foundation','/business-foundation?business='+b,'/businesses','/business?business=BIZ-B','/sales?business=BIZ-B','/workflow?business=BIZ-B','/seminar-office?business=BIZ-B'):
                    result=page.goto(base+route); assert result.status==200,(route,result.status); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                page.goto(base+'/business-foundation?business='+b); page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
            page.get_by_role('button',name='ログアウト',exact=True).click(); login('staff','SyntheticStaffPass!123'); assert page.goto(base+'/business-foundation?business='+b).status==200; assert page.locator('form[action="/actions/bf_update"]').count()==0; assert page.goto(base+'/business-foundation?business=BIZ-A').status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database','business_registration_update_disable_enable':'passed','authenticated_assignment_api':'passed','no_phase20_allocation_console':'passed','scoped_ai_metadata':'passed','human_scope_and_owner_only_settings':'passed','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','actual_external_services':[],'real_iphone':'not_tested','production_started':False},ensure_ascii=False))
    finally:
        server.shutdown(); server.server_close(); fixture.tearDown()


if __name__=='__main__': main()
