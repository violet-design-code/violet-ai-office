"""PC/390px local browser pipeline and approvals. Built-in mock only, all remote requests blocked."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests import test_llm_pilot as helpers
from violet_office.web import make_server

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True);results=[];fixture=helpers.PilotTests();fixture.setUp();fixture.assigned();fixture.operator();server=make_server(fixture.path,0);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            for width in (1440,390):
                page=browser.new_page(viewport={'width':width,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo');page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/login');page.locator('[name="username"]').fill('owner');page.locator('[name="password"]').fill('SyntheticOwnerPass!123');page.get_by_role('button',name='ログイン',exact=True).click();page.goto(base+'/llm-pilot');page.get_by_role('link',name='鏡のウロコ取り出張サービス',exact=True).click()
                def screen(name):
                    assert page.locator('main').is_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),name
                    page.screenshot(path=str(out/f'{width}-{name}.png'),full_page=True);results.append({'width':width,'check':name,'state':'passed'})
                screen('request')
                f=page.locator('form').filter(has=page.locator('[name="action"][value="create"]'));f.locator('[name="brief"]').fill('Instagram投稿の合成下書き'+str(width)+'を作ってください。料金・健康効果を断定しない。');f.locator('[name="checked"]').check();f.get_by_role('button').click();screen('ai01-assigned')
                panels=page.locator('details.panel');panel=panels.first;f=panel.locator('form').filter(has=page.locator('[name="action"][value="run"]'));f.locator('[name="checked"]').check();f.get_by_role('button').click();screen('draft-and-ai01-report')
                row=fixture.store.db.execute('SELECT * FROM lp_jobs ORDER BY rowid DESC LIMIT 1').fetchone();assert row['state']=='approval_pending';assert fixture.store.db.execute('SELECT COUNT(*) FROM lp_calls WHERE job_id=?',(row['id'],)).fetchone()[0]==4
                panel=page.locator('details.panel').first;f=panel.locator('form').filter(has=page.locator('[name="action"][value="decide"]')).filter(has=page.locator('[name="decision"][value="approve"]'));f.locator('[name="checked"]').check();f.locator('[name="note"]').fill('合成の下書き・事実・権利・表現・要確認項目を代表が手動確認。実公開しない。');f.get_by_role('button').click();assert fixture.o.llm.row(fixture.owner,fixture.b,row['id'])['state']=='approved_draft_no_execution';screen('approved-no-external-execution')
                page.goto(base+'/costs');screen('usage-estimates-mock')
                page.goto(base+'/ai-management?business='+fixture.b);assert page.locator('details.panel > summary').filter(has_text='AI').count()==28;screen('28-preserved')
                assert not errors,errors;page.close()
            browser.close()
        report={'data_mode':'synthetic_temporary_database','provider':'built_in_mock','checks':results,'live_openai_called':False,'instagram_posts':0,'line_messages':0,'production_started':False,'real_iphone_tested':False};(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':len(results),'failed':0,'real_api_calls':0}))
    finally:server.shutdown();server.server_close();fixture.tearDown()
if __name__=='__main__':main()
