"""Phase19 service/API readiness, review-only UI, synthetic DB and local Chromium."""
import argparse,json,threading,shutil
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright
from tests import test_business_readiness as helpers
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    fixture=helpers.ReadinessTests();fixture.setUp();fixture.adopt();fixture.operator();server=make_server(fixture.path,0);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo');page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
            def login(user,password):
                page.goto(base+'/login');page.locator('[name="username"]').fill(user);page.locator('[name="password"]').fill(password);page.get_by_role('button',name='ログイン',exact=True).click();page.wait_for_load_state('networkidle')
            def forms(action):
                page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)');return page.locator('form[action="/actions/'+action+'"]')
            def prepare(kind,payload,b=None):
                page.goto(base+'/business-readiness'+('?business='+b if b else ''));csrf=page.locator('[name="csrf"]').first.input_value();data={'csrf':csrf,'nonce':uuid4().hex,'key':uuid4().hex,'kind':kind,'payload':json.dumps(payload,ensure_ascii=False)}
                if b:data['business']=b
                response=page.request.post(base+'/actions/br_prepare',form=data,headers={'Origin':base});assert response.status==200,response.status
                return fixture.store.db.execute('SELECT id FROM br_requests WHERE entry_key=?',(data['key'],)).fetchone()[0]
            def approve_apply(identifier,b=None):
                page.goto(base+'/business-readiness'+('?business='+b if b else ''));f=forms('br_decide').filter(has=page.locator('[name="id"][value="'+identifier+'"]')).filter(has=page.locator('[name="decision"][value="approve"]'));f.locator('[name="checked"]').check();f.get_by_role('button').click();assert fixture.o.readiness.row(fixture.owner,identifier)['status']=='approved';f=forms('br_apply').filter(has=page.locator('[name="id"][value="'+identifier+'"]'));f.get_by_role('button').click();assert fixture.o.readiness.row(fixture.owner,identifier)['status']=='applied'
            login('owner','SyntheticOwnerPass!123');page.goto(base+'/business-readiness');csrf=page.locator('[name="csrf"]').first.input_value();reply=page.request.post(base+'/business-readiness-preview',form={'csrf':csrf,'kind':'create','payload':json.dumps(fixture.new_values())},headers={'Origin':base});assert reply.status==200;assert fixture.store.db.execute('SELECT COUNT(*) FROM br_requests').fetchone()[0]==0
            identifier=prepare('create',fixture.new_values());assert fixture.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0]==6;approve_apply(identifier);b=fixture.o.readiness.row(fixture.owner,identifier)['result_business'];assert '合成の新事業' in page.locator('main').inner_text()
            identifier=prepare('assign',fixture.assign_values(),b);assert fixture.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0]==0;approve_apply(identifier,b);assert fixture.o.readiness.contract(fixture.owner,b)['employees'][16]['assignment'] is not None
            identifier=prepare('assign',fixture.assign_values(),fixture.b);approve_apply(identifier,fixture.b)
            identifier=prepare('update',fixture.update_values(b,status='paused',enabled=False),b);approve_apply(identifier,b);assert fixture.store.db.execute('SELECT enabled FROM bf_profiles WHERE business_id=?',(b,)).fetchone()[0]==0
            identifier=prepare('update',fixture.update_values(b,status='active',enabled=True),b);approve_apply(identifier,b)
            identifier=prepare('unassign',{'agent':'AI17'},b);approve_apply(identifier,b);assert fixture.store.db.execute("SELECT active FROM bf_ai_assignments WHERE business_id=? AND agent='AI17'",(b,)).fetchone()[0]==0;assert fixture.store.db.execute("SELECT active FROM bf_ai_assignments WHERE business_id=? AND agent='AI17'",(fixture.b,)).fetchone()[0]==1
            result=page.request.get(base+'/business-readiness-data?business='+b);assert result.status==200;assert len(result.json()['contract']['employees'])==28;assert result.json()['contract']['final_phase20_ui'] is False
            route='/business-readiness?business='+b
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for target in (route,'/business-readiness','/business?business='+b,'/business-foundation?business='+b,'/ai-workflow?business='+b,'/sales?business='+b,'/customer-journey?business='+b):
                    response=page.goto(base+target);assert response.status==200,(target,response.status);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),target
                page.goto(base+route);page.screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')),full_page=True)
            page.get_by_role('button',name='ログアウト',exact=True).click();login('staff','SyntheticStaffPass!123');assert page.goto(base+'/business-readiness?business=BIZ-B').status==200;assert page.locator('form[action="/actions/br_apply"]').count()==0;assert page.goto(base+'/business-readiness?business=BIZ-A').status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database','stateless_preview':'passed','representative_review_then_separate_apply':'passed','business_creation_list_reflection':'passed','business_pause_reactivate':'passed','one_employee_two_businesses':'passed','release_is_logical_and_scoped':'passed','28_existing_employee_api':'passed','owner_only_mutations':'passed','final_phase20_editors':'not_implemented_by_design','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','real_iphone':'not_tested','actual_external_services':[],'production_started':False},ensure_ascii=False))
    finally:server.shutdown();server.server_close();fixture.tearDown()


if __name__=='__main__':main()
