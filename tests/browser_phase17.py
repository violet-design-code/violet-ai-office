"""Real Chromium on disposable synthetic DB; external browser requests blocked."""
import argparse,json,threading,shutil
from pathlib import Path
from datetime import timedelta
from playwright.sync_api import sync_playwright
from tests import test_customer_journey as helpers
from violet_office.web import make_server
from violet_office.store import now


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    fixture=helpers.CustomerJourneyTests();fixture.setUp();j=fixture.profile(approved=True);fixture.assign();fixture.operator()
    server=make_server(fixture.path,0);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo');page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            def login(user,password):
                page.goto(base+'/login');page.locator('[name="username"]').fill(user);page.locator('[name="password"]').fill(password);page.get_by_role('button',name='ログイン',exact=True).click();page.wait_for_load_state('networkidle')
            def form(action):
                page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)');return page.locator('form[action="/actions/'+action+'"]')
            route='/customer-journey?business=BIZ-B';login('owner','SyntheticOwnerPass!123');page.goto(base+route)
            f=form('cj_intake');f.locator('[name="name"]').fill('合成ブラウザ顧客');f.locator('[name="channel"]').fill('synthetic-reference');f.locator('[name="interest"]').fill('合成の任意相談');f.locator('[name="inquiry"]').fill('合成の問い合わせ・実顧客ではない');f.locator('[name="mode"]').select_option('mock');f.get_by_role('button').click();assert '合成の任意相談' in page.locator('main').inner_text()
            d=fixture.store.db.execute('SELECT deal_id FROM cj_cases').fetchone()[0];c=j.case(fixture.owner,fixture.b,d)['contact_id']
            page.goto(base+'/sales?business=BIZ-B')
            for purpose in ('contact','schedule','followup'):
                f=form('scm_consent');f.locator('[name="contact"]').select_option(c);f.locator('[name="purpose"]').select_option(purpose);f.locator('[name="notice"]').fill('合成の本人希望と目的の同意');f.get_by_role('button').click()
            page.goto(base+route);f=form('cj_route');f.locator('[name="kind"]').select_option('consult');f.get_by_role('button').click();assert 'AI18' in page.locator('main').inner_text();assert fixture.store.db.execute('SELECT COUNT(*) FROM cj_routes').fetchone()[0]==3
            f=form('cj_request');start=now()+timedelta(days=3);f.locator('[name="start"]').fill(start.isoformat());f.locator('[name="end"]').fill((start+timedelta(hours=1)).isoformat());f.get_by_role('button').click();assert '候補・未確定' in page.locator('main').inner_text();assert fixture.store.db.execute('SELECT status FROM scm_bookings').fetchone()[0]=='candidate'
            page.goto(base+'/workflow?business=BIZ-B');form('wf_run').first.get_by_role('button').click();assert '固定テンプレート' in page.locator('main').inner_text();assert fixture.store.db.execute('SELECT COUNT(*) FROM cj_work_inputs').fetchone()[0]==1
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for target in (route,'/customer-journey','/business?business=BIZ-B','/sales?business=BIZ-B','/workflow?business=BIZ-B','/seminar-office?business=BIZ-B','/businesses'):
                    response=page.goto(base+target);assert response.status==200,(target,response.status);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),target
                page.goto(base+route);page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
            # A revision, even after a prior approval, returns to administrator review.
            f=form('cj_profile');f.locator('[name="summary"]').fill('合成の改訂・要確認');f.get_by_role('button').click();assert '管理者承認待ち' in page.locator('main').inner_text();assert not j.usable(fixture.owner,fixture.b)
            page.get_by_role('button',name='ログアウト',exact=True).click();login('staff','SyntheticStaffPass!123');assert page.goto(base+route).status==200;assert page.locator('form[action="/actions/cj_profile"]').count()==0;assert page.goto(base+'/customer-journey?business=BIZ-A').status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database','assisted_interest_intake':'passed','purpose_specific_consent_forms':'passed','existing_three_role_routing':'passed','local_template_and_provenance':'passed','interview_candidate_no_confirmation':'passed','profile_revision_reapproval':'passed','human_business_acl':'passed','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','real_iphone':'not_tested','actual_external_services':[],'production_started':False},ensure_ascii=False))
    finally:server.shutdown();server.server_close();fixture.tearDown()


if __name__=='__main__':main()
