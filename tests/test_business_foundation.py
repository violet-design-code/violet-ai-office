"""Business foundation checks using only disposable synthetic data."""
import json,sqlite3,unittest
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from tests import test_sales as helpers
from violet_office.store import Store,now
from violet_office.office import Office
from violet_office.integrations.adapters import Principal
from violet_office.business_foundation import migrate,ai_allowed
from violet_office.ops import backup,restore
from violet_office.web import OfficeApp


class BusinessFoundationTests(unittest.TestCase):
    setUp=helpers.SalesTests.setUp
    tearDown=helpers.SalesTests.tearDown
    headers=helpers.SalesTests.headers
    post=helpers.SalesTests.post
    operator=helpers.SalesTests.operator
    def create(self,key='one',name='合成の新規事業'):
        return self.o.business_foundation.create(self.owner,key,name,'合成の概要','自由カテゴリ',None)
    def assign(self,b,agent='AI17',perms=None,active=True):
        old=self.store.db.execute('SELECT revision FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,agent)).fetchone()
        self.o.business_foundation.assign(self.owner,b,agent,'既存役割の補足','この事業の内部作業',perms if perms is not None else ['business_read','work'],active,old[0] if old else 0)
    def update(self,b,**values):
        f=self.o.business_foundation; row=f.get(self.owner,b); business=self.store.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone()
        return f.update(self.owner,b,values.get('overview',row['overview']),values.get('category',row['category']),values.get('started_on',row['started_on']),values.get('enabled',bool(row['enabled'])),values.get('status',business['status']),values.get('confirmed',bool(business['offering_confirmed'])),row['revision'],row['business_revision'])
    def test_registration_unique_id_idempotency_and_no_seeded_performance(self):
        before=[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')]; b=self.create(); self.assertEqual(b,self.create()); self.assertTrue(b.startswith('BIZ-'))
        self.assertEqual(self.store.db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0],'preparing')
        self.assertEqual(self.o.business_foundation.get(self.owner,b)['ai_enforced'],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_ledger WHERE business_id=?',(b,)).fetchone()[0],0)
        self.assertEqual(before,[tuple(r) for r in self.store.db.execute("SELECT * FROM portfolio_businesses WHERE id IN ('BIZ-A','BIZ-B','BIZ-C','BIZ-D','BIZ-E','BIZ-F')")])
        with self.assertRaises(ValueError): self.create(name='同じキーの変更')
    def test_same_name_does_not_merge_and_categories_are_extensible(self):
        a=self.create('a'); b=self.create('b'); self.assertNotEqual(a,b)
        self.update(a,category='後から追加する分類',started_on=self.day.isoformat()); self.assertEqual(self.o.business_foundation.get(self.owner,a)['category'],'後から追加する分類')
        self.assertEqual(self.store.db.execute('SELECT kind FROM portfolio_businesses WHERE id=?',(a,)).fetchone()[0],'other')
    def test_update_requires_both_revisions_and_active_confirmation(self):
        b=self.create(); f=self.o.business_foundation; row=f.get(self.owner,b)
        with self.assertRaises(ValueError): self.update(b,status='active',confirmed=False)
        self.update(b,status='active',confirmed=True)
        with self.assertRaises(ValueError): f.update(self.owner,b,'合成','合成',None,True,'active',True,row['revision'],row['business_revision'])
    def test_old_settings_edit_is_not_silently_overwritten(self):
        b=self.create(); f=self.o.business_foundation; stale=f.get(self.owner,b)
        self.o.portfolio.update(self.owner,b,{'services':'既存画面で修正した概要'},1)
        self.assertEqual(f.get(self.owner,b)['overview'],'既存画面で修正した概要')
        with self.assertRaises(ValueError): f.update(self.owner,b,stale['overview'],'合成',None,True,'preparing',False,stale['revision'],stale['business_revision'])
    def test_disabled_business_preserves_data_and_blocks_all_scoped_writes(self):
        b=self.create(); c=self.o.sales.customer(self.owner,b,'合成顧客','synthetic-ref'); self.assign(b); t=self.o.portfolio.task(self.owner,b,'test','referral','合成の内部作業',agent='AI17')
        self.update(b,enabled=False)
        with self.assertRaises(PermissionError): self.o.sales.customer(self.owner,b,'合成','synthetic-ref')
        with self.assertRaises(PermissionError): self.o.portfolio.ledger(self.owner,b,'new',self.day.isoformat(),'revenue',100,'actual','合成')
        with self.assertRaises(PermissionError): self.store.claim(t)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_contacts WHERE business_id=?',(b,)).fetchone()[0],1)
        self.update(b,enabled=True); self.assertTrue(self.o.business_foundation.get(self.owner,b)['enabled'])
    def test_assignment_many_to_many_all_28_definitions_unchanged(self):
        a=self.create('a'); b=self.create('b'); before=Path('config/agents.json').read_bytes()
        for agent in self.store.agents: self.assign(a,agent)
        self.assign(b,'AI17'); self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM bf_ai_assignments WHERE agent='AI17'").fetchone()[0],2)
        self.assertEqual(len(self.store.agents),28); self.assertEqual(before,Path('config/agents.json').read_bytes())
    def test_scoped_ai_reads_do_not_inherit_representative_global_access(self):
        a=self.create('a'); b=self.create('b'); self.assign(a); f=self.o.business_foundation
        self.assertEqual(f.read_for_ai(self.owner,a,'AI17')['business_id'],a)
        with self.assertRaises(PermissionError): f.read_for_ai(self.owner,b,'AI17')
        with self.assertRaises(PermissionError): f.read_for_ai(self.owner,a,'AI17','customers')
        with self.assertRaises(PermissionError): f.read_for_ai(self.owner,a,'AI17','finance_read',self.day.isoformat(),(self.day+timedelta(days=1)).isoformat())
    def test_permissions_cannot_grant_external_approval_or_privilege_expansion(self):
        b=self.create(); f=self.o.business_foundation
        for perms in (['external_send'],['approve'],['spend'],['production_access'],['work'],['business_read','customers'],[['work']]):
            with self.assertRaises(ValueError): f.assign(self.owner,b,'AI17','合成','合成',perms)
        with self.assertRaises(ValueError): self.assign(b,'AI29')
    def test_human_business_acl_and_customers_remain_separate(self):
        a=self.create('a'); b=self.create('b'); c=self.o.sales.customer(self.owner,a,'同じ合成名','synthetic-ref'); d=self.o.sales.customer(self.owner,b,'同じ合成名','synthetic-ref'); self.assertNotEqual(c,d)
        self.o.add_user('staff','business_operator','SyntheticStaffPass!123'); self.o.portfolio.grant(self.owner,a,'staff',True); staff=Principal('business_operator','staff')
        self.assertEqual(self.o.business_foundation.get(staff,a)['business_id'],a)
        with self.assertRaises(PermissionError): self.o.business_foundation.get(staff,b)
        with self.assertRaises(PermissionError): self.o.business_foundation.assign(staff,a,'AI17','合成','合成',['business_read'])
        with self.assertRaises(ValueError): self.o.portfolio.row(self.owner,b,'portfolio_contacts',c)
    def test_assignment_revocation_blocks_pending_and_inflight_output(self):
        b=self.create(); self.assign(b); t=self.o.portfolio.task(self.owner,b,'pending','referral','合成',agent='AI17'); self.assign(b,active=False)
        with self.assertRaises(PermissionError): self.store.claim(t)
        self.assign(b); run=self.store.claim(t); self.assign(b,active=False)
        with self.assertRaises(PermissionError): self.store.finish(run,'合成成果物')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts WHERE task_id=?',(t,)).fetchone()[0],0)
    def test_portfolio_selection_is_limited_to_assigned_specialists(self):
        b=self.create()
        with self.assertRaises(PermissionError): self.o.portfolio.task(self.owner,b,'none','referral','合成')
        self.assign(b,'AI17'); t=self.o.portfolio.task(self.owner,b,'one','referral','合成')
        self.assertEqual(self.store.db.execute('SELECT agent FROM tasks WHERE id=?',(t,)).fetchone()[0],'AI17')
        with self.assertRaises(ValueError): self.o.portfolio.task(self.owner,b,'bad','referral','合成',agent='AI16')
    def test_sales_and_workflow_use_assignment_gates(self):
        b=self.create(); c=self.o.sales.customer(self.owner,b,'合成','synthetic-ref'); deal=self.o.sales.deal(self.owner,b,'deal',c,'合成','合成内容'); self.o.workflow.attach(self.owner,b,deal)
        with self.assertRaises(PermissionError): self.o.workflow.assign(self.owner,b,deal,'bad','AI17','合成','合成',self.day.isoformat())
        self.assign(b,'AI17'); self.assertTrue(self.o.sales.work(self.owner,b,deal,'reply','reply',agent='AI17'))
        self.assertTrue(self.o.workflow.assign(self.owner,b,deal,'ok','AI17','合成','合成',self.day.isoformat()))
    def test_legacy_businesses_and_pending_tasks_unchanged_until_opt_in(self):
        pf=self.o.portfolio; t=pf.task(self.owner,'BIZ-B','legacy','referral','合成',agent='AI17'); old=[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')]
        f=self.o.business_foundation; f.adopt(self.owner,'BIZ-B','既存事業の追加分類'); self.assertEqual(f.get(self.owner,'BIZ-B')['ai_enforced'],0)
        with self.assertRaises(ValueError): f.enforce(self.owner,'BIZ-B',True,True)
        pf.run(self.owner,'BIZ-B',t); self.assign('BIZ-B'); self.assign('BIZ-B','AI28'); f.enforce(self.owner,'BIZ-B',True,True)
        self.assertEqual(old,[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')])
    def test_assignment_changes_are_versioned_and_cannot_automatically_weaken_policy(self):
        b=self.create(); self.assign(b)
        with self.assertRaises(ValueError): self.o.business_foundation.assign(self.owner,b,'AI17','合成','合成',['business_read'],revision=0)
        with self.assertRaises(ValueError): self.o.business_foundation.enforce(self.owner,b,False,True)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM bf_events WHERE event='employee_assignment_changed'").fetchone()[0],1)
    def test_sales_expenses_profit_and_estimates_are_scoped(self):
        a=self.create('a'); b=self.create('b'); pf=self.o.portfolio
        pf.ledger(self.owner,a,'sales',self.day.isoformat(),'revenue',100,'actual','合成の確認記録'); pf.ledger(self.owner,a,'cost',self.day.isoformat(),'expense',30,'actual','合成の確認記録'); pf.ledger(self.owner,b,'mock',self.day.isoformat(),'revenue',999,'estimated','合成')
        end=(self.day+timedelta(days=1)).isoformat(); x=self.o.business_foundation.summary(self.owner,a,self.day.isoformat(),end); y=self.o.business_foundation.summary(self.owner,b,self.day.isoformat(),end)
        self.assertEqual(x['portfolio']['profit_yen'],70); self.assertIsNone(y['portfolio']['revenue_yen']); self.assertIsNone(x['sales']['confirmed_net_yen'])
    def test_custom_kpi_definition_modes_units_and_revision_history(self):
        b=self.create(); f=self.o.business_foundation; f.kpi(self.owner,b,'repeat_count','継続回数','回'); day=self.day.isoformat()
        for mode,value in [('actual','0'),('estimate','10'),('mock','999')]: f.measure(self.owner,b,'repeat_count',day,value,mode,'合成の根拠')
        f.measure(self.owner,b,'repeat_count',day,'2','actual','合成の訂正',1)
        values=[dict(r) for r in self.store.db.execute('SELECT * FROM bf_kpi_samples WHERE business_id=?',(b,))]; self.assertEqual(len(values),3); self.assertEqual(next(r['value'] for r in values if r['mode']=='actual'),'2')
        with self.assertRaises(ValueError): f.kpi(self.owner,b,'repeat_count','継続回数','人',True,1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM bf_events WHERE event='kpi_observation_revised'").fetchone()[0],4)
    def test_custom_kpi_cross_business_and_invalid_numbers_rejected(self):
        a=self.create('a'); b=self.create('b'); f=self.o.business_foundation; f.kpi(self.owner,a,'count','件数','件')
        with self.assertRaises(ValueError): f.measure(self.owner,b,'count',self.day.isoformat(),'1','actual','合成')
        for value in ('NaN','Infinity','-1'):
            with self.assertRaises(ValueError): f.measure(self.owner,a,'count',self.day.isoformat(),value,'actual','合成')
        with self.assertRaises(ValueError): f.kpi(self.owner,a,'bad;DROP','合成','回')
    def test_disabled_and_paused_business_blocks_ai_reads_and_late_writes(self):
        b=self.create(); self.assign(b); t=self.o.portfolio.task(self.owner,b,'job','referral','合成',agent='AI17'); run=self.store.claim(t); self.update(b,enabled=False)
        with self.assertRaises(PermissionError): self.o.business_foundation.read_for_ai(self.owner,b,'AI17')
        with self.assertRaises(PermissionError): self.store.finish(run,'合成')
    def test_web_auth_scope_csrf_and_no_full_phase20_console(self):
        b=self.create(); h,c=self.headers(); app=OfficeApp(self.path,8765); response=app.handle('GET','/business-foundation?business='+b,h)
        self.assertEqual(response[0],200); self.assertNotIn(b'/actions/bf_assign',response[2])
        self.assertEqual(self.post(h,'wrong','bf_adopt',{'business':self.b,'category':'合成'})[0],403)
        staff=self.operator(); sh,sc=self.headers('staff','business_operator'); self.assertEqual(app.handle('GET','/business-foundation?business='+b,sh)[0],403)
        self.assertEqual(self.post(sh,sc,'bf_create',{'key':'bad','name':'合成','overview':'合成','category':'合成'})[0],403)
    def test_creation_command_nonce_and_xss_escaping(self):
        h,c=self.headers(); data={'key':'web','name':'合成事業','overview':'合成概要','category':'合成カテゴリ'}
        first=self.post(h,c,'bf_create',data,'a'*32); self.assertEqual(first[0],303); self.assertEqual(first,self.post(h,c,'bf_create',data,'a'*32))
        b=self.store.db.execute("SELECT business_id FROM bf_creations WHERE request_key='web'").fetchone()[0]; self.store.db.execute("UPDATE bf_profiles SET category='<script>alert(1)</script>' WHERE business_id=?",(b,))
        response=OfficeApp(self.path,8765).handle('GET','/business-foundation?business='+b,h); self.assertIn(b'&lt;script',response[2]); self.assertNotIn(b'<script>alert',response[2])
    def test_restore_revokes_assignment_preserves_business_and_customer_rows(self):
        b=self.create(); self.assign(b); self.o.sales.customer(self.owner,b,'合成顧客','synthetic-ref')
        snap=Path(self.temp.name)/'snapshot.db'; dst=Path(self.temp.name)/'recovery.db'; backup(self.path,snap); restore(snap,dst); db=sqlite3.connect(dst)
        try:
            self.assertEqual(db.execute('SELECT active FROM bf_ai_assignments').fetchone()[0],0); self.assertEqual(db.execute('SELECT COUNT(*) FROM portfolio_contacts WHERE business_id=?',(b,)).fetchone()[0],1); self.assertEqual(db.execute('SELECT ai_enforced FROM bf_profiles').fetchone()[0],1)
        finally: db.close()
    def test_migration_is_additive_idempotent_and_keeps_previous_rows(self):
        self.o.sales.customer(self.owner,'BIZ-B','合成顧客','synthetic-ref')
        for t in ('bf_creations','bf_ai_assignments','bf_kpi_samples','bf_kpis','bf_events','bf_profiles'): self.store.db.execute('DROP TABLE '+t)
        self.store.db.execute('DELETE FROM office_migrations WHERE version=15'); tables=[r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='office_migrations'")]
        snapshot=lambda:{t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in tables}
        before=snapshot(); migrate(self.store); migrate(self.store); self.assertEqual(before,snapshot()); self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[]); self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_profiles').fetchone()[0],0)
    def test_parallel_same_creation_key_creates_only_one_business(self):
        def create(_):
            st=Store(self.path)
            try: return Office(st).business_foundation.create(self.owner,'parallel','合成','合成','合成')
            finally: st.close()
        with ThreadPoolExecutor(max_workers=2) as pool: result=list(pool.map(create,range(2)))
        self.assertEqual(result[0],result[1]); self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM bf_creations WHERE request_key='parallel'").fetchone()[0],1)

    def test_seminar_extension_and_development_cannot_ignore_new_assignment_policy(self):
        from tests import test_seminar_support as sem_helpers
        b=self.create(); service=self.o.seminar_support
        sid=service.save(self.owner,b,sem_helpers.SeminarSupportTests.data(self),['cast'])
        with self.assertRaises(PermissionError): service.work(self.owner,b,sid,'copy','none')
        self.assign(b,'AI17'); task=service.work(self.owner,b,sid,'copy','allowed','AI17'); self.assertTrue(service.run(self.owner,b,task))
        idea=self.o.development.create(self.owner,'合成の候補',{},b)
        with self.assertRaises(PermissionError): self.o.development.proposal(self.owner,idea)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bd_proposals WHERE idea_id=?',(idea,)).fetchone()[0],0)
    def test_pause_and_emergency_stop_invalidate_late_ai_output(self):
        b=self.create(); self.assign(b); task=self.o.portfolio.task(self.owner,b,'late','referral','合成',agent='AI17'); run=self.store.claim(task)
        self.update(b,status='paused')
        with self.assertRaises(PermissionError): self.store.finish(run,'合成')
        self.update(b,status='preparing'); self.o.portfolio.halt(self.owner,True)
        with self.assertRaises(PermissionError): self.store.finish(run,'合成')

    def test_independent_reviewer_also_needs_business_assignment(self):
        b=self.create(); self.assign(b); task=self.o.portfolio.task(self.owner,b,'review','referral','合成',agent='AI17'); run=self.store.claim(task); artifact=self.store.finish(run,'合成の中立成果物')
        with self.assertRaises(PermissionError): self.store.review(artifact,'AI28','pass')
        self.assign(b,'AI28'); self.store.review(artifact,'AI28','pass')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0],1)
