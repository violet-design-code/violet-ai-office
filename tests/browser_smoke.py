"""任意の実ブラウザ検証。PlaywrightとChromiumを導入済みの環境で実行。"""
import json, tempfile, threading, shutil, argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from violet_office.store import Store
from violet_office.office import Office
from violet_office.integrations.demo import run_demo
from violet_office.web import make_server

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description='ローカル合成データの実ブラウザ検証')
parser.add_argument('--output-dir')
parser.add_argument('--with-growth',action='store_true')
args=parser.parse_args()
artifacts=Path(args.output_dir) if args.output_dir else root/'examples'
artifacts.mkdir(parents=True,exist_ok=True)
desktop=artifacts/('desktop.png' if args.output_dir else 'phase-5-desktop.png')
mobile=artifacts/('mobile.png' if args.output_dir else 'phase-5-mobile.png')
with tempfile.TemporaryDirectory() as temp:
    db=Path(temp)/'visual.db'
    store=Store(db)
    office=Office(store)
    office.add_user('visual_owner','representative','SyntheticVisualTest!123')
    run_demo(store)
    office.coordinator.submit('visual-request','来週のInstagram投稿文とLINE配信を準備',priority=2)
    store.close()
    server=make_server(db,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1,locale='ja-JP',timezone_id='Asia/Tokyo')
        page.route('**/*',lambda route: route.continue_() if route.request.url.startswith(f'http://127.0.0.1:{server.server_port}/') else route.abort())
        base=f'http://127.0.0.1:{server.server_port}'
        page.goto(base)
        page.get_by_label('ユーザー名',exact=True).fill('visual_owner')
        page.get_by_label('パスワード',exact=True).fill('SyntheticVisualTest!123')
        page.get_by_role('button',name='ログイン',exact=True).click()
        page.wait_for_url(base+'/')
        page.screenshot(path=str(desktop),full_page=True)
        # Exercise the rendered forms, not only page rendering.
        page.goto(base+'/instructions')
        task_form=page.locator('form[action="/actions/submit"]')
        task_form.locator('[name="instruction"]').fill('市場調査をお願いします')
        task_form.get_by_role('button').click()
        page.wait_for_url('**/requests?id=*')
        assert 'AI03' in page.locator('main').inner_text()
        page.goto(base+'/employees?id=AI03')
        page.locator('form[action="/actions/start"]').first.get_by_role('button').click()
        page.locator('form[action="/actions/stop"]').first.get_by_role('button').click()
        assert '一時停止' in page.locator('main').inner_text()
        page.goto(base+'/approvals')
        reject=page.locator('form[action="/actions/content_decide"]').filter(has=page.locator('input[name="decision"][value="reject"]')).first
        reject.locator('xpath=ancestor::details[1]').locator('summary').first.click()
        reject.get_by_role('button').click()
        assert '却下' in page.locator('main').inner_text()
        growth_report_url=None
        if args.with_growth:
            page.goto(base+'/growth')
            goals=page.locator('form[action="/actions/growth_goals"]')
            goals.locator('[name="sns_posts"]').fill('4')
            goals.locator('[name="ai_budget_usd"]').fill('0')
            goals.locator('[name="owner_minutes"]').fill('120')
            goals.get_by_role('button').click()
            instruction=page.locator('form[action="/actions/growth_instruction"]')
            instruction.locator('[name="instruction"]').fill('今日の仕事を報告して')
            instruction.get_by_role('button').click()
            page.wait_for_url('**/reports?id=*')
            assert '未計測' in page.locator('main').inner_text()
            approve=page.locator('form[action="/actions/growth_decide"]').filter(has=page.locator('input[name="decision"][value="approve"]')).first
            approve.get_by_role('button').click()
            assert '承認済み・未実行' in page.locator('main').inner_text()
            page.goto(base+'/growth')
            schedule=page.locator('form[action="/actions/growth_schedule"]')
            schedule.locator('[name="prepare_draft"]').check()
            schedule.get_by_role('button').click()
            page.locator('form[action="/actions/growth_state"]').filter(has=page.locator('input[name="enabled"][value="1"]')).first.get_by_role('button').click()
            page.locator('form[action="/actions/growth_tick"]').get_by_role('button').click()
            page.locator('form[action="/actions/growth_state"]').filter(has=page.locator('input[name="enabled"][value="0"]')).first.get_by_role('button').click()
            check=Store(db)
            row=check.db.execute('SELECT id,status,decision FROM growth_runs WHERE schedule_id IS NOT NULL ORDER BY rowid DESC LIMIT 1').fetchone()
            assert row['status']=='succeeded_local' and row['decision']=='approval_pending'
            assert check.db.execute('SELECT enabled FROM growth_schedules').fetchone()[0]==0
            growth_report_url='/reports?id='+row['id']
            check.close()
            page.goto(base+'/growth')
            page.screenshot(path=str(desktop),full_page=True)
        routes=['/employees','/instructions','/approvals','/analytics','/costs','/monitor']
        if args.with_growth:
            routes+=['/growth','/reports']
        for route in routes:
            r=page.goto(base+route)
            assert r.status==200,(route,r.status)
        page.set_viewport_size({'width':390,'height':844})
        for route in ['/']+routes:
            page.goto(base+route)
            width=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
            assert width['scroll']<=width['width'],(route,width)
        page.goto(base+(growth_report_url if args.with_growth else '/employees'))
        page.screenshot(path=str(mobile),full_page=True)
        browser.close()
    server.shutdown()
    server.server_close()
print(json.dumps({'login':'passed','browser_form_operations':['task_assignment','manual_start','pause','content_rejection'],'growth_forms':'passed' if args.with_growth else 'not_run','desktop_pages':len(routes)+1,'mobile_pages_without_horizontal_overflow':len(routes)+1,'external_browser_requests':'blocked','screenshots':[str(desktop),str(mobile)]},ensure_ascii=False))
