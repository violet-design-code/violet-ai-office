"""Actual local Chromium form operations, synthetic DB, PC and iPhone-sized viewport."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests import test_business_management as helpers
from violet_office.web import make_server

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True);results=[]
    fixture=helpers.ManagementTests();fixture.setUp();fixture.operator();server=make_server(fixture.path,0);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            for width in (1440,390):
                page=browser.new_page(viewport={'width':width,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo');page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/login');page.get_by_label('ユーザー名',exact=True).fill('owner');page.get_by_label('パスワード',exact=True).fill('SyntheticOwnerPass!123');page.get_by_role('button',name='ログイン',exact=True).click()
                def screen(name):
                    assert page.locator('main').is_visible();assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),name
                    page.screenshot(path=str(out/f'{width}-{name}.png'),full_page=True);results.append({'width':width,'check':name,'result':'passed'})
                def apply():
                    page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)');f=page.locator('form[action="/actions/br_decide"]').filter(has=page.locator('input[name="decision"][value="approve"]')).last;f.locator('input[name="checked"]').check();f.get_by_role('button').click();page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)');page.locator('form[action="/actions/br_apply"]').last.get_by_role('button').click();assert page.locator('h1').inner_text()=='事業管理'
                page.goto(base+'/business-management');page.get_by_role('link',name='＋ 新規事業を追加').click();screen('new-form')
                f=page.locator('form[action="/business-management-confirm"]');f.locator('[name="name"]').fill('合成の第20段階検証'+str(width));f.locator('[name="overview"]').fill('合成の内容・実在する顧客や事業ではありません');f.locator('[name="category"]').fill('合成分類');f.locator('[name="provided_services"]').fill('合成サービス');f.locator('[name="manager"]').select_option('owner');f.locator('[name="revenue_yen"]').fill('120000');f.get_by_role('button').click();screen('confirmation');apply();b=fixture.store.db.execute('SELECT id FROM portfolio_businesses WHERE name=?',('合成の第20段階検証'+str(width),)).fetchone()[0];screen('business-saved')
                def edit(state):
                    f=page.locator('form[action="/business-management-confirm"]');f.locator('[name="status"]').select_option(state)
                    if state=='active':f.locator('[name="confirmed"]').check()
                    f.get_by_role('button').click();apply();assert fixture.store.db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0]==state
                edit('paused');screen('business-paused');edit('active');screen('business-resumed')
                page.goto(base+'/ai-management?business='+b);assert page.locator('details.panel > summary').filter(has_text='AI').count()==28;screen('employees28')
                panel=page.locator('details.panel').filter(has=page.locator('summary').filter(has_text='AI17 ')).first;panel.locator('summary').click();f=panel.locator('form').first;f.locator('[name="role_note"]').fill('合成の問い合わせ整理');f.locator('[name="scope_note"]').fill('当該合成事業だけ');f.locator('[name="business_read"]').check();f.locator('[name="work"]').check();f.get_by_role('button').click();apply();assert fixture.store.db.execute('SELECT active FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,'AI17')).fetchone()[0]==1
                # A second business assignment to the same employee is additive.
                page.goto(base+'/ai-management?business=BIZ-B');fixture.adopt();page.reload();panel=page.locator('details.panel').filter(has=page.locator('summary').filter(has_text='AI17 ')).first;panel.locator('summary').click();f=panel.locator('form').first;f.locator('[name="role_note"]').fill('合成の兼務');f.locator('[name="scope_note"]').fill('合成のみ');f.locator('[name="business_read"]').check();f.get_by_role('button').click();apply();assert fixture.store.db.execute("SELECT COUNT(*) FROM bf_ai_assignments WHERE agent='AI17' AND active=1").fetchone()[0]>=2
                page.goto(base+'/ai-management?business='+b);panel=page.locator('details.panel').filter(has=page.locator('summary').filter(has_text='AI17 ')).first;panel.locator('summary').click();screen('assigned');panel.get_by_role('button',name='担当解除を確認').click();apply();assert fixture.store.db.execute("SELECT active FROM bf_ai_assignments WHERE business_id=? AND agent='AI17'",(b,)).fetchone()[0]==0;screen('released');assert not errors;page.close()
            browser.close()
        (out/'result.json').write_text(json.dumps({'data_mode':'synthetic','external_execution':False,'real_iphone_tested':False,'checks':results},ensure_ascii=False,indent=2));print(json.dumps({'passed':len(results),'failed':0,'real_iphone_tested':False}))
    finally:server.shutdown();server.server_close();fixture.tearDown()
if __name__=='__main__':main()
