"""Existing UI and scoped JSON endpoints on isolated synthetic records only."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests.test_business_operations import BusinessOperationsTests
from violet_office.store import Store
from violet_office.office import Office
from violet_office.web import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-dir',required=True); args=parser.parse_args(); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    fixture=BusinessOperationsTests(); fixture.setUp(); fixture.operator(); f=fixture.custom(); start,end=fixture.dates(); f.measure(fixture.owner,fixture.b,'new_count',start,'5','actual','合成のブラウザ検証'); f.assign(fixture.owner,fixture.b,'AI27','数値集計','当該事業の集計',['business_read','work','finance_read']); task=fixture.o.portfolio.task(fixture.owner,fixture.b,'browser-ops','analysis','合成の内部分析',agent='AI27'); fixture.o.sales.customer(fixture.owner,fixture.b,'PRIVATE-SYNTHETIC-CUSTOMER','synthetic-private-ref')
    for row in fixture.store.db.execute('SELECT id FROM portfolio_businesses'):
        fixture.policy(row[0],'legacy'); fixture.o.portfolio.ledger(fixture.owner,row[0],'browser-revenue',start,'revenue',1000,'actual','合成データ。実際の取引ではない'); fixture.o.portfolio.ledger(fixture.owner,row[0],'browser-cost',start,'expense',200,'actual','合成データ。実際の取引ではない')
    server=make_server(fixture.path,0); threading.Thread(target=server.serve_forever,daemon=True).start(); base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox']); page=browser.new_page(viewport={'width':1440,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo')
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
            def login(user,password):
                page.goto(base+'/login'); page.locator('[name="username"]').fill(user); page.locator('[name="password"]').fill(password); page.get_by_role('button',name='ログイン',exact=True).click(); page.wait_for_load_state('networkidle')
            def form(action):
                page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)'); return page.locator('form[action="/actions/'+action+'"]') .first
            login('owner','SyntheticOwnerPass!123'); route='/business?business=BIZ-B&start='+start+'&end='+end; page.goto(base+route)
            f1=form('ops_rule'); f1.locator('[name="aggregation"]').select_option('sum'); f1.locator('[name="evidence"]').fill('合成の日別合計設定'); f1.get_by_role('button').click()
            f1=form('ops_target'); f1.locator('[name="target"]').fill('4'); f1.locator('[name="evidence"]').fill('合成の期間目標'); f1.get_by_role('button').click()
            assert '達成' in page.locator('#business-operations').inner_text(); assert '800' in page.locator('#business-operations').inner_text()
            endpoint='/business-task-kpis?task='+task+'&start='+start+'&end='+end; reply=page.request.get(base+endpoint); assert reply.status==200; assert reply.json()['agent']=='AI27'; assert 'PRIVATE-SYNTHETIC-CUSTOMER' not in reply.text(); assert page.request.get(base+endpoint+'&agent=AI01').status==400
            page.goto(base+'/businesses?start='+start+'&end='+end); assert 'PRIVATE-SYNTHETIC-CUSTOMER' not in page.content(); assert '6000' in page.locator('main').inner_text()
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':844})
                for target in (route,'/businesses?start='+start+'&end='+end,'/business-foundation?business=BIZ-B','/sales?business=BIZ-B','/workflow?business=BIZ-B','/seminar-office?business=BIZ-B','/development'):
                    response=page.goto(base+target); assert response.status==200,(target,response.status); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),target
                page.goto(base+route); assert page.locator('.ops-wide').evaluate('(n)=>n.scrollWidth>=n.clientWidth'); page.locator('#business-operations').screenshot(path=str(out/('desktop.png' if width==1440 else 'mobile.png')))
                if width==1440:
                    page.goto(base+'/businesses?start='+start+'&end='+end); page.screenshot(path=str(out/'global-desktop.png'),full_page=True)
            page.goto(base+route); dateform=page.locator('form[method="get"]').first; dateform.locator('[name="start"]').fill((fixture.day.replace(day=1)).isoformat()); dateform.get_by_role('button').click(); assert 'start='+fixture.day.replace(day=1).isoformat() in page.url
            page.get_by_role('button',name='ログアウト',exact=True).click(); login('staff','SyntheticStaffPass!123'); assert page.goto(base+route).status==200; assert page.locator('form[action="/actions/ops_finance"]').count()==0; assert page.goto(base+'/business?business=BIZ-A').status==403; assert page.request.get(base+'/business-operations-data?start='+start+'&end='+end).status==403
            browser.close()
        print(json.dumps({'mode':'synthetic_temporary_database_not_real_transactions','existing_business_ui_period_filter':'passed','custom_kpi_rule_target_achievement':'passed','selected_profit_and_global_same_basis_totals':'passed','global_pii':'not_exposed','authenticated_task_bound_kpi_api':'passed','agent_spoofing':'rejected','human_acl_and_owner_only_settings':'passed','desktop_routes':7,'mobile_routes':7,'external_browser_requests':'blocked','real_iphone':'not_tested','actual_external_services':[],'production_started':False},ensure_ascii=False))
    finally:
        server.shutdown(); server.server_close(); fixture.tearDown()


if __name__=='__main__': main()
