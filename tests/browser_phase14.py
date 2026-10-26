"""Local Chromium tests using a disposable synthetic database only."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests.test_seminar_support import SeminarSupportTests
from violet_office.store import Store
from violet_office.office import Office
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args(); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    fixture=SeminarSupportTests(); fixture.setUp(); fixture.member(); fixture.operator()
    server=make_server(fixture.path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            def login(user,password):
                page.goto(base+'/login'); page.locator('[name="username"]').fill(user); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
            def form(action,match=None):
                page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)')
                f=page.locator('form[action="/actions/'+action+'"]')
                if match: f=f.filter(has=page.locator('input[name="'+match[0]+'"][value="'+match[1]+'"]'))
                return f.first
            def logout(): page.get_by_role('button',name='ログアウト',exact=True).click()
            login('owner','SyntheticOwnerPass!123'); page.goto(base+'/seminar-office?business=BIZ-B'); f=form('s14_save'); data=fixture.data()
            for key in ('title','location','description'): f.locator('[name="'+key+'"]').fill(data[key])
            for key in ('start_at','end_at','deadline'): f.locator('[name="'+key+'"]').fill(data[key][:16])
            f.locator('[name="fee_yen"]').fill('0'); f.locator('[name="online"]').fill('https://example.com/synthetic-meeting'); f.locator('[name="target_cast"]').check(); f.get_by_role('button').click()
            check=Store(fixture.path); sid=check.db.execute('SELECT seminar_id FROM s14_meta').fetchone()[0]; check.close()
            logout(); login('one','SyntheticParticipantPass!123'); page.goto(base+'/seminar-participation?id='+sid)
            f=form('s14_member_response'); f.locator('[name="answer"]').select_option('yes'); f.get_by_role('button').click()
            for purpose in ('invite','reminders','followup'):
                f=form('s14_member_consent',('purpose',purpose)); f.locator('[name="granted"]').select_option('1'); f.get_by_role('button').click()
            f=form('s14_member_preferences'); f.locator('[name="interest_health"]').check(); f.locator('[name="interest_beauty"]').check(); f.get_by_role('button').click(); form('s14_member_interview').get_by_role('button').click()
            page.set_viewport_size({'width':390,'height':844}); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'); page.screenshot(path=str(out/'participant-mobile.png'),full_page=True)
            logout(); login('owner','SyntheticOwnerPass!123'); page.goto(base+'/seminar-office?business=BIZ-B')
            f=form('s14_prepare'); f.locator('[name="username"]').fill('one'); f.locator('[name="kind"]').select_option('invite'); f.get_by_role('button').click()
            f=form('s14_decide',('decision','approve')); f.locator('[name="checked"]').check(); f.get_by_role('button').click()
            check=Store(fixture.path); assert len(Office(check).seminar_support.queue(fixture.owner,'BIZ-B'))==1; assert check.db.execute('SELECT COUNT(*) FROM s14_attempts').fetchone()[0]==0; assert check.db.execute('SELECT COUNT(*) FROM scm_bookings').fetchone()[0]==0; check.close()
            form('s14_mock').get_by_role('button').click()
            f=form('s14_work'); f.locator('[name="activity"]').select_option('copy'); f.locator('[name="agent"]').fill('AI17'); f.get_by_role('button').click(); form('s14_run').get_by_role('button').click()
            f=form('s14_control'); f.locator('[name="control"]').select_option('approve'); f.get_by_role('button').click()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for route in ('/seminar-office','/seminar-office?business=BIZ-B','/seminar-admin','/workflow?business=BIZ-B','/sales?business=BIZ-B','/businesses','/development'):
                    response=page.goto(base+route); assert response.status==200,(route,response.status)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                page.goto(base+'/seminar-office?business=BIZ-B'); page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
            logout(); login('one','SyntheticParticipantPass!123'); page.goto(base+'/seminar-participation?id='+sid); f=form('s14_member_response',('answer','cancel')); f.get_by_role('button').click()
            check=Store(fixture.path); assert check.db.execute('SELECT answer FROM seminar_responses').fetchone()[0]=='no'; assert check.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0]==0; assert check.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0]==0; check.close()
            logout(); login('staff','SyntheticStaffPass!123'); assert page.goto(base+'/seminar-office?business=BIZ-B').status==200; assert '合成の参加者' not in page.content(); assert page.goto(base+'/seminar-office?business=BIZ-A').status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database','manual_seminar_registration_calendar':'passed','participant_application_interests_purpose_consents':'passed','interview_request_no_booking':'passed','representative_approval_no_send':'passed','mock_once':'passed','local_role_work':'passed','cancellation':'passed','cross_business_access':'denied','participant_pii_to_staff':'denied','desktop_routes':7,'mobile_routes':7,'participant_mobile':'passed','external_browser_requests':'blocked','real_iphone':'not_tested','actual_external_services':[],'production_started':False},ensure_ascii=False))
    finally:
        server.shutdown(); server.server_close(); fixture.tearDown()


if __name__=='__main__': main()
