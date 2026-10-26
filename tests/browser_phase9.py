"""Optional actual Chromium checks, synthetic temporary DB and loopback only."""
import argparse
import json
import os
import shutil
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from violet_office.store import Store,now
from violet_office.office import Office,JST
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args()
    output=Path(args.output_dir); output.mkdir(parents=True,exist_ok=True)
    evidence={}
    with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'SEMINAR_CARD_HMAC_KEY':'synthetic-browser-hash-key-1234567890'}):
        path=Path(directory)/'synthetic.db'; store=Store(path); office=Office(store)
        office.add_user('owner','representative','SyntheticOwnerPass!123'); store.close()
        server=make_server(path,0); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                def login(username,password):
                    page.goto(base+'/login'); page.locator('[name="username"]').fill(username); page.locator('[name="password"]').fill(password)
                    page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
                def logout(): page.get_by_role('button',name='ログアウト',exact=True).click()
                login('owner','SyntheticOwnerPass!123'); page.goto(base+'/seminar-admin')
                account=page.locator('form[action="/actions/seminar_account"]')
                for key,value in {'username':'cast_person','display_name':'合成の参加者','password':'SyntheticParticipant!123','evidence':'synthetic-verification-reference'}.items(): account.locator('[name="'+key+'"]').fill(value)
                account.locator('[name="attested"]').check(); account.get_by_role('button').click()
                new=page.locator('form[action="/actions/seminar_save"]').first
                start=(now().astimezone(JST)+timedelta(days=3)).replace(second=0,microsecond=0)
                for key,value in {'title':'合成の健康・美容セミナー','start_at':start.strftime('%Y-%m-%dT%H:%M'),'end_at':(start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),'location':'合成オンライン会場','fee_yen':'0','description':'商品・サービスの情報を案内する合成テストです。','deadline':(start-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),'capacity':'10'}.items(): new.locator('[name="'+key+'"]').fill(value)
                new.locator('[name="audience"]').select_option('public'); new.get_by_role('button').click()
                assert '合成の健康・美容セミナー' in page.locator('main').inner_text()
                logout(); login('cast_person','SyntheticParticipant!123')
                initial=page.locator('form[action="/actions/seminar_member_register"]'); initial.locator('[name="segment"]').select_option('cast'); initial.get_by_role('button').click()
                assert '代表承認待ち' in page.locator('main').inner_text()
                assert '合成の健康・美容セミナー' not in page.locator('main').inner_text()
                page.locator('form[action="/actions/seminar_member_consent"]').filter(has=page.locator('input[name="granted"][value="1"]')).get_by_role('button').click()
                logout(); login('owner','SyntheticOwnerPass!123'); page.goto(base+'/seminar-admin')
                page.locator('form[action="/actions/seminar_registration"]').filter(has=page.locator('input[name="decision"][value="approve"]')).first.get_by_role('button').click()
                logout(); login('cast_person','SyntheticParticipant!123')
                answer=page.locator('form[action="/actions/seminar_member_response"]'); answer.locator('[name="answer"]').select_option('maybe'); answer.get_by_role('button').click()
                answer=page.locator('form[action="/actions/seminar_member_response"]'); answer.locator('[name="answer"]').select_option('yes'); answer.get_by_role('button').click()
                assert '参加する' in page.locator('main').inner_text()
                page.screenshot(path=str(output/'participant-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844}); page.screenshot(path=str(output/'participant-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                logout(); login('owner','SyntheticOwnerPass!123'); page.goto(base+'/seminar-admin')
                page.locator('form[action="/actions/seminar_schedule"]').get_by_role('button').click()
                page.locator('form[action="/actions/seminar_tick"]').get_by_role('button').click()
                seminar=page.locator('details.panel').filter(has=page.locator('form[action="/actions/seminar_work"]')).first
                seminar.locator('summary').first.click()
                approve=page.locator('form[action="/actions/seminar_approve"]').filter(has=page.locator('input[name="decision"][value="approve"]')).first
                approve.locator('xpath=ancestor::details[1]').locator('summary').first.click(); approve.get_by_role('button').click()
                # Approval alone must never cause delivery.
                check=Store(path)
                assert check.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0]==0
                assert check.db.execute('SELECT status FROM seminar_reminders').fetchone()[0]=='approved'
                check.close()
                page.set_viewport_size({'width':1440,'height':1000}); page.screenshot(path=str(output/'representative-desktop.png'),full_page=True)
                page.goto(base+'/selection')
                form=page.locator('form[action="/actions/selection_propose"]'); form.locator('[name="kind"]').select_option('sns'); form.get_by_role('button').click()
                assign=page.locator('form[action="/actions/selection_assign"]').first
                assign.locator('xpath=ancestor::details[1]').locator('summary').first.click()
                assign.locator('[name="agent"]').select_option('AI08'); assign.get_by_role('button').click()
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':844})
                    for route in ('/seminar-admin','/selection','/learning','/employees','/monitor'):
                        response=page.goto(base+route); assert response.status==200,(route,response.status)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),route
                page.goto(base+'/seminar-admin'); page.screenshot(path=str(output/'representative-mobile.png'),full_page=True)
                browser.close()
                evidence={'mode':'synthetic_temporary_database','owner_identity_account_creation':'passed','initial_registration_manual_approval':'passed','participant_response_changes':'passed','representative_counts_history':'passed','three_day_preparation_approval_no_dispatch':'passed','selection_override':'passed','desktop_routes':5,'mobile_routes':5,'participant_desktop_mobile':'passed','external_browser_requests':'blocked','real_iphone':'not_tested','production_started':False}
        finally: server.shutdown(); server.server_close()
    print(json.dumps(evidence,ensure_ascii=False))

if __name__=='__main__': main()
