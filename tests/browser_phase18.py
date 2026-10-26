"""Loopback Chromium only, synthetic temporary DB, no external request permitted."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests import test_workflow_collaboration as helpers
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    fixture=helpers.CollaborationTests();fixture.setUp();fixture.assign();fixture.operator();deal=fixture.deal('mock');a=fixture.o.collaboration
    capability=a.propose(fixture.owner,fixture.b,'cap','capability',fixture.cap(False));external=a.propose(fixture.owner,fixture.b,'ext','external_send',{'reference':'合成の社内資料','reason':'合成検証・送信しない'})
    server=make_server(fixture.path,0);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo');page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            def login(user,password):
                page.goto(base+'/login');page.locator('[name="username"]').fill(user);page.locator('[name="password"]').fill(password);page.get_by_role('button',name='ログイン',exact=True).click();page.wait_for_load_state('networkidle')
            def forms(action):
                page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)');return page.locator('form[action="/actions/'+action+'"]')
            route='/ai-workflow?business=BIZ-B';login('owner','SyntheticOwnerPass!123');page.goto(base+route)
            f=forms('aw_submit');f.locator('[name="deal"]').select_option(deal);f.locator('[name="kind"]').select_option('inquiry');f.get_by_role('button').click();job=fixture.store.db.execute('SELECT id FROM aw_jobs').fetchone()[0];assert 'AI17' in page.locator('main').inner_text()
            f=forms('aw_handoff');f.locator('[name="target"]').select_option('AI20');f.locator('[name="kind"]').select_option('inquiry');f.locator('[name="reason"]').fill('合成の同一事業引き継ぎ');f.get_by_role('button').click();assert fixture.store.db.execute('SELECT agent FROM aw_jobs').fetchone()[0]=='AI20'
            forms('aw_run').get_by_role('button').click();assert fixture.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0]==1
            f=forms('aw_decide').filter(has=page.locator('[name="id"][value="'+capability+'"]')).filter(has=page.locator('[name="decision"][value="approve"]'));f.locator('[name="checked"]').check();f.get_by_role('button').click();assert fixture.store.db.execute('SELECT COUNT(*) FROM aw_capabilities').fetchone()[0]==0
            f=forms('aw_decide').filter(has=page.locator('[name="id"][value="'+external+'"]')).filter(has=page.locator('[name="decision"][value="approve"]'));f.locator('[name="checked"]').check();f.get_by_role('button').click();assert fixture.store.db.execute('SELECT COUNT(*) FROM scm_actions').fetchone()[0]==0;assert fixture.store.db.execute('SELECT status FROM aw_changes WHERE id=?',(external,)).fetchone()[0]=='approved_manual_only'
            forms('aw_apply_capability').get_by_role('button').click();assert fixture.store.db.execute('SELECT available FROM aw_capabilities').fetchone()[0]==0
            result=page.request.get(base+'/ai-workflow-data?business=BIZ-B');assert result.status==200;assert len(result.json()['employees'])==28;assert result.json()['external_execution'] is False;assert '合成顧客' not in result.text()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for target in (route,'/ai-workflow','/workflow?business=BIZ-B','/customer-journey?business=BIZ-B','/business?business=BIZ-B','/sales?business=BIZ-B','/seminar-office?business=BIZ-B'):
                    response=page.goto(base+target);assert response.status==200,(target,response.status);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),target
                page.goto(base+route);page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
            page.get_by_role('button',name='ログアウト',exact=True).click();login('staff','SyntheticStaffPass!123');assert page.goto(base+route).status==200;assert page.locator('form[action="/actions/aw_decide"]').count()==0;assert page.goto(base+'/ai-workflow?business=BIZ-A').status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database','job_submission_and_role_routing':'passed','same_business_handoff':'passed','local_template_only':'passed','representative_approval_before_internal_scope_apply':'passed','external_approval_no_execution':'passed','28_existing_employee_read_api':'passed','no_customer_pii_in_work_api':'passed','human_acl':'passed','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','real_iphone':'not_tested','actual_external_services':[],'production_started':False},ensure_ascii=False))
    finally:server.shutdown();server.server_close();fixture.tearDown()


if __name__=='__main__':main()
