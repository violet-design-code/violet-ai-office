"""Chromium workflow/approval verification on disposable synthetic DB only."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests.test_workflow import WorkflowTests
from violet_office.store import Store
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args(); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    fixture=WorkflowTests(); fixture.setUp(); d,seminar=fixture.seminar(); other=fixture.deal(); fixture.operator()
    server=make_server(fixture.path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            def login(user,password):
                page.goto(base+'/login'); page.locator('[name="username"]').fill(user); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
            def form(action,match=None):
                page.locator('details').evaluate_all('(items)=>items.forEach(x=>x.open=true)')
                f=page.locator('form[action="/actions/'+action+'"]')
                if match: f=f.filter(has=page.locator('input[name="'+match[0]+'"][value="'+match[1]+'"]'))
                return f.first
            login('owner','SyntheticOwnerPass!123'); page.goto(base+'/workflow?business=BIZ-B')
            form('wf_attach',('deal',other)).get_by_role('button').click()
            f=form('wf_advance',('deal',d)); f.locator('[name="stage"]').select_option('prepare'); f.get_by_role('button').click()
            f=form('wf_assign',('deal',d)); f.locator('[name="agent"]').select_option('AI17'); f.locator('[name="purpose"]').fill('合成の返信準備'); f.locator('[name="basis"]').fill('合成資料の参照'); f.locator('[name="due"]').fill(fixture.day.isoformat()); f.get_by_role('button').click()
            f=form('wf_control'); f.locator('[name="control"]').select_option('pause'); f.get_by_role('button').click(); assert page.locator('form[action="/actions/wf_run"]').count()==0
            f=form('wf_control'); f.locator('[name="control"]').select_option('resume'); f.get_by_role('button').click(); form('wf_run').get_by_role('button').click()
            page.locator('details').evaluate_all('(items)=>items.forEach(x=>x.open=true)')
            assert 'LLM未使用' in page.locator('main').inner_text()
            f=form('wf_decide',('decision','approve')); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
            f=form('wf_draft',('deal',d)); f.locator('[name="seminar"]').fill(seminar); f.locator('[name="username"]').fill('participant'); f.locator('[name="kind"]').select_option('invite'); f.get_by_role('button').click()
            f=form('wf_draft_decide',('decision','approve')); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
            check=Store(fixture.path); office=Office(check); assert len(office.workflow.queue(fixture.owner,'BIZ-B'))==1
            assert check.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0]==0; assert check.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0]==0; check.close()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for route in ('/workflow','/workflow?business=BIZ-B','/workflow?business=BIZ-A','/sales?business=BIZ-B','/seminar-admin','/businesses','/development'):
                    response=page.goto(base+route); assert response.status==200,(route,response.status)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                page.goto(base+'/workflow?business=BIZ-B'); page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
            check=Store(fixture.path); Office(check).seminars.respond('participant',seminar,'no'); assert Office(check).workflow.queue(fixture.owner,'BIZ-B')==[]; check.close()
            page.get_by_role('button',name='ログアウト',exact=True).click(); login('staff','SyntheticStaffPass!123'); assert page.goto(base+'/workflow?business=BIZ-B').status==200; assert page.goto(base+'/workflow?business=BIZ-A').status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database','attach_and_manual_progress':'passed','assignment_pause_resume_local_template':'passed','representative_approval_no_execution':'passed','seminar_queue_and_decline_invalidation':'passed','cross_business_access':'denied','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','real_iphone':'not_tested','actual_external_services':[],'production_started':False},ensure_ascii=False))
    finally:
        server.shutdown(); server.server_close(); fixture.tearDown()


if __name__=='__main__': main()
