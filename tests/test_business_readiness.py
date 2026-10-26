"""Phase20-ready services on synthetic data only; no external communication."""
import json,sqlite3,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tests import test_sales as helpers
from violet_office.business_readiness import migrate
from violet_office.business_foundation import ai_allowed
from violet_office.store import Store
from violet_office.office import Office
from violet_office.web import OfficeApp
from violet_office.ops import backup,restore


class ReadinessTests(unittest.TestCase):
    setUp=helpers.SalesTests.setUp
    tearDown=helpers.SalesTests.tearDown
    deal=helpers.SalesTests.deal
    customer=helpers.SalesTests.customer
    operator=helpers.SalesTests.operator
    headers=helpers.SalesTests.headers
    post=helpers.SalesTests.post
    def new_values(self,name='合成の新事業'):return {'name':name,'overview':'合成検証・実提供の主張ではない','category':'合成カテゴリ'}
    def update_values(self,b=None,**changes):
        b=b or self.b;r=self.store.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone();f=self.store.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(b,)).fetchone()
        return {'name':r['name'],'overview':r['services'],'category':f['category'] if f else '代表確認の合成分類','started_on':f['started_on'] if f else None,'status':r['status'],'enabled':bool(f['enabled']) if f else True,'confirmed':True,**changes}
    def assign_values(self,agent='AI17',permissions=None):return {'agent':agent,'role_note':'合成の担当業務','scope_note':'当該合成事業のみ','permissions':['business_read','work','finance_read'] if permissions is None else permissions,'active':True}
    def adopt(self,b=None):self.o.business_foundation.adopt(self.owner,b or self.b,'合成の明示採用')
    def apply(self,kind,values,b=None,key=None):
        r=self.o.readiness;i=r.prepare(self.owner,key or kind+str(self.store.db.execute('SELECT COUNT(*) FROM br_requests').fetchone()[0]),kind,b,values);r.decide(self.owner,i,'approve',True);return r.apply(self.owner,i)
    def test_preview_does_not_create_business_or_assignments(self):
        before=self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0];view=self.o.readiness.preview(self.owner,'create',None,self.new_values());i=self.o.readiness.prepare(self.owner,'new','create',None,self.new_values());self.assertEqual(view['after']['name'],'合成の新事業');self.assertFalse(view['external_execution']);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0],before)
        with self.assertRaises(ValueError):self.o.readiness.apply(self.owner,i)
    def test_approval_is_not_application_and_new_business_safe_defaults(self):
        r=self.o.readiness;i=r.prepare(self.owner,'new','create',None,self.new_values());r.decide(self.owner,i,'approve',True);self.assertIsNone(self.store.db.execute("SELECT id FROM portfolio_businesses WHERE name='合成の新事業'").fetchone());b=r.apply(self.owner,i);row=self.store.db.execute('SELECT * FROM portfolio_businesses WHERE id=?',(b,)).fetchone();self.assertEqual(row['status'],'preparing');self.assertTrue(self.store.db.execute('SELECT ai_enforced FROM bf_profiles WHERE business_id=?',(b,)).fetchone()[0]);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_contacts WHERE business_id=?',(b,)).fetchone()[0],0);self.assertEqual(len(r.contract(self.owner,b)['employees']),28)
    def test_prepare_and_apply_replay_idempotent_even_after_creation(self):
        r=self.o.readiness;i=r.prepare(self.owner,'new','create',None,self.new_values());r.decide(self.owner,i,'approve',True);b=r.apply(self.owner,i);self.assertEqual(b,r.apply(self.owner,i));self.assertEqual(i,r.prepare(self.owner,'new','create',None,self.new_values()))
        with self.assertRaises(ValueError):r.prepare(self.owner,'new','create',None,self.new_values('別の合成名'))
    def test_unicode_case_and_spacing_duplicate_rejected_without_merging(self):
        self.apply('create',self.new_values('VIOLET TEST'))
        with self.assertRaises(ValueError):self.o.readiness.preview(self.owner,'create',None,self.new_values('ＶＩＯＬＥＴ　ＴＥＳＴ'))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM portfolio_businesses WHERE name='VIOLET TEST'").fetchone()[0],1)
    def test_two_approved_creations_cannot_duplicate_business(self):
        r=self.o.readiness;a=r.prepare(self.owner,'a','create',None,self.new_values());z=r.prepare(self.owner,'z','create',None,self.new_values());r.decide(self.owner,a,'approve',True);r.decide(self.owner,z,'approve',True);r.apply(self.owner,a)
        with self.assertRaises(ValueError):r.apply(self.owner,z)
    def test_edit_adopts_only_explicit_category_and_preserves_business_id(self):
        before=self.store.db.execute('SELECT id FROM portfolio_businesses ORDER BY id').fetchall();self.apply('update',self.update_values(name='合成の名称改訂',category='合成の代表確認カテゴリ'),self.b);self.assertEqual(before,self.store.db.execute('SELECT id FROM portfolio_businesses ORDER BY id').fetchall());self.assertEqual(self.o.business_foundation.get(self.owner,self.b)['category'],'合成の代表確認カテゴリ')
    def test_stop_and_reactivate_keep_customer_ledger_consent_and_history(self):
        c=self.customer();self.o.portfolio.consent(self.owner,self.b,c,'contact',True,'合成の目的同意');self.o.portfolio.ledger(self.owner,self.b,'x',self.day.isoformat(),'revenue',100,'actual','合成');before={t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in ('portfolio_contacts','portfolio_consents','portfolio_ledger')};self.apply('update',self.update_values(status='paused',enabled=False),self.b)
        with self.assertRaises(PermissionError):self.o.portfolio.writable(self.owner,self.b)
        self.apply('update',self.update_values(status='active',enabled=True),self.b);self.assertEqual(before,{t:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+t)] for t in before});self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],0)
    def test_operating_without_confirmation_and_unknown_fields_rejected(self):
        for values in (self.update_values(confirmed=False),self.update_values(enabled=False),{**self.update_values(),'pricing':'invented'}):
            with self.assertRaises(ValueError):self.o.readiness.preview(self.owner,'update',self.b,values)
        with self.assertRaises(ValueError):self.o.readiness.prepare(self.owner,'new','create',None,{**self.new_values(),'id':'BIZ-A'})
    def test_28_existing_definitions_unchanged_and_no_employee_recreation(self):
        registry=json.dumps(self.store.agents,sort_keys=True);self.adopt();self.apply('assign',self.assign_values(),self.b);self.apply('unassign',{'agent':'AI17'},self.b);self.assertEqual(registry,json.dumps(self.store.agents,sort_keys=True));self.assertEqual(len(self.o.readiness.contract(self.owner,self.b)['employees']),28)
    def test_employee_can_be_assigned_to_many_businesses(self):
        self.adopt();self.adopt('BIZ-A');self.apply('assign',self.assign_values(),self.b);self.apply('assign',self.assign_values(), 'BIZ-A');self.assertTrue(ai_allowed(self.store,'AI17',self.b,'work'));self.assertTrue(ai_allowed(self.store,'AI17','BIZ-A','work'))
    def test_release_enforces_old_legacy_tasks_without_changing_other_employees(self):
        self.adopt();f=self.o.business_foundation;v=self.assign_values();f.assign(self.owner,self.b,'AI17',v['role_note'],v['scope_note'],v['permissions']);d=self.deal('mock');self.o.workflow.attach(self.owner,self.b,d);task=self.o.workflow.assign(self.owner,self.b,d,'old','AI17','合成','合成',self.day.isoformat());self.assertTrue(ai_allowed(self.store,'AI02',self.b,'work',strict=False));self.apply('unassign',{'agent':'AI17'},self.b)
        with self.assertRaises(PermissionError):self.store.claim(task)
        self.assertTrue(ai_allowed(self.store,'AI02',self.b,'work',strict=False));self.assertFalse(ai_allowed(self.store,'AI17',self.b,'work',strict=False));self.assertEqual(f.get(self.owner,self.b)['ai_enforced'],0)
    def test_permission_reduction_is_effective_and_release_is_logical(self):
        self.adopt();self.apply('assign',self.assign_values(),self.b);self.apply('assign',self.assign_values(permissions=['business_read']),self.b);self.assertFalse(ai_allowed(self.store,'AI17',self.b,'work',strict=False));self.assertFalse(ai_allowed(self.store,'AI17',self.b,'finance_read'));self.apply('unassign',{'agent':'AI17'},self.b);self.assertEqual(self.store.db.execute('SELECT active FROM bf_ai_assignments').fetchone()[0],0);self.assertGreaterEqual(self.store.db.execute('SELECT COUNT(*) FROM br_events').fetchone()[0],9)
    def test_new_assignment_requires_explicit_existing_metadata(self):
        with self.assertRaises(ValueError):self.o.readiness.prepare(self.owner,'a','assign',self.b,self.assign_values())
        self.assertIsNone(self.store.db.execute('SELECT * FROM bf_profiles WHERE business_id=?',(self.b,)).fetchone())
    def test_unknown_role_permissions_and_scope_secrets_rejected(self):
        self.adopt()
        for values in (self.assign_values('AI99'),self.assign_values(permissions=['external_send']),self.assign_values(permissions=['work']),{**self.assign_values(),'scope_note':'token=private_secret_value'}):
            with self.assertRaises(ValueError):self.o.readiness.prepare(self.owner,'a','assign',self.b,values)
    def test_rejected_request_cannot_apply(self):
        r=self.o.readiness;i=r.prepare(self.owner,'new','create',None,self.new_values());r.decide(self.owner,i,'reject',True)
        with self.assertRaises(ValueError):r.apply(self.owner,i)
    def test_changed_settings_invalidate_approved_preview_and_display_stale(self):
        self.adopt();r=self.o.readiness;i=r.prepare(self.owner,'u','update',self.b,self.update_values(overview='合成の編集'));r.decide(self.owner,i,'approve',True);self.store.db.execute('UPDATE portfolio_businesses SET revision=revision+1 WHERE id=?',(self.b,));self.assertTrue(r.listing(self.owner,self.b)[0]['stale'])
        with self.assertRaises(ValueError):r.apply(self.owner,i)
    def test_contract_and_approval_history_are_business_scoped(self):
        self.adopt();self.adopt('BIZ-A');self.o.readiness.prepare(self.owner,'b','assign',self.b,self.assign_values());self.o.readiness.prepare(self.owner,'a','assign','BIZ-A',self.assign_values());staff=self.operator();self.assertEqual(len(self.o.readiness.listing(staff,self.b)),1)
        with self.assertRaises(PermissionError):self.o.readiness.contract(staff,'BIZ-A')
        with self.assertRaises(PermissionError):self.o.readiness.listing(staff)
    def test_operator_cannot_prepare_approve_or_apply_management_changes(self):
        staff=self.operator();r=self.o.readiness;i=r.prepare(self.owner,'new','create',None,self.new_values())
        with self.assertRaises(PermissionError):r.prepare(staff,'u','update',self.b,self.update_values())
        with self.assertRaises(PermissionError):r.decide(staff,i,'approve',True)
        with self.assertRaises(PermissionError):r.apply(staff,i)
    def test_assignment_approval_does_not_apply_before_confirmation(self):
        self.adopt();r=self.o.readiness;i=r.prepare(self.owner,'a','assign',self.b,self.assign_values())
        with self.assertRaises(ValueError):r.decide(self.owner,i,'approve',False)
        r.decide(self.owner,i,'approve',True);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0);r.apply(self.owner,i);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],1)
    def test_edit_rechecks_introduction_without_inventing_official_conditions(self):
        j=self.o.customer_journey
        from violet_office.customer_journey import DETAILS
        j.save_profile(self.owner,self.b,'unconfirmed',{k:'' for k in DETAILS});old=j.profile(self.owner,self.b);self.apply('update',self.update_values(overview='合成内容の改訂'),self.b);new=j.profile(self.owner,self.b);self.assertEqual(new['status'],'pending');self.assertEqual(new['details'],old['details']);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM cj_sources').fetchone()[0],0)
    def test_mutating_other_business_does_not_change_customers_or_finances(self):
        c=self.customer();before=tuple(self.store.db.execute('SELECT * FROM portfolio_contacts WHERE id=?',(c,)).fetchone());self.apply('update',self.update_values('BIZ-A',overview='合成の別事業変更'),'BIZ-A');self.assertEqual(before,tuple(self.store.db.execute('SELECT * FROM portfolio_contacts WHERE id=?',(c,)).fetchone()));self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0],0)
    def test_legacy_approved_changes_do_not_bypass_current_assignment_version(self):
        self.adopt();r=self.o.readiness;i=r.prepare(self.owner,'a','assign',self.b,self.assign_values());r.decide(self.owner,i,'approve',True);self.o.business_foundation.assign(self.owner,self.b,'AI20','合成','合成',['business_read','work'])
        with self.assertRaises(ValueError):r.apply(self.owner,i)
    def test_parallel_apply_produces_exactly_one_business(self):
        r=self.o.readiness;i=r.prepare(self.owner,'new','create',None,self.new_values());r.decide(self.owner,i,'approve',True)
        def worker(_):
            store=Store(self.path)
            try:return Office(store).readiness.apply(self.owner,i)
            finally:store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(worker,range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM portfolio_businesses WHERE name='合成の新事業'").fetchone()[0],1)
    def test_http_auth_csrf_input_and_final_ui_not_implemented(self):
        r=self.o.readiness;h,c=self.headers();app=OfficeApp(self.path,8765);reply=app.handle('GET','/business-readiness-data',h);self.assertEqual(reply[0],200);self.assertEqual(len(json.loads(reply[2])['contract']['employees']),28);self.assertFalse(json.loads(reply[2])['contract']['final_phase20_ui']);self.assertEqual(app.handle('GET','/business-readiness-data?agent=AI01',h)[0],400);self.assertEqual(app.handle('GET','/business-readiness',{'Host':'127.0.0.1:8765'})[0],303);i=r.prepare(self.owner,'new','create',None,self.new_values());self.assertEqual(self.post(h,'bad','br_decide',{'id':i,'decision':'approve','checked':1})[0],403)
    def test_http_scoped_denial_persists_audit_and_no_customer_pii(self):
        self.adopt();self.customer();self.operator();h,c=self.headers('staff','business_operator');app=OfficeApp(self.path,8765);reply=app.handle('GET','/business-readiness-data?business='+self.b,h);self.assertEqual(reply[0],200);self.assertEqual(app.handle('GET','/business-readiness',h)[0],200);self.assertNotIn('合成顧客',reply[2].decode());self.assertEqual(app.handle('GET','/business-readiness-data?business=BIZ-A',h)[0],403);self.assertEqual(self.post(h,c,'br_prepare',{'business':self.b,'key':'x','kind':'assign','payload':json.dumps(self.assign_values())})[0],403);self.assertGreater(self.store.db.execute("SELECT COUNT(*) FROM ops_audit WHERE outcome='denied'").fetchone()[0],0)
    def test_xss_escaped_in_confirmation_view(self):
        self.o.readiness.prepare(self.owner,'new','create',None,self.new_values('<script>alert(1)</script>'));h,c=self.headers();reply=OfficeApp(self.path,8765).handle('GET','/business-readiness',h);self.assertEqual(reply[0],200);self.assertIn(b'&lt;script',reply[2]);self.assertNotIn(b'<script>alert',reply[2])
    def test_migration_preserves_all_existing_rows_and_repeats(self):
        self.customer();db=self.store.db
        for t in ('br_events','br_requests','br_assignment_controls'):db.execute('DROP TABLE '+t)
        db.execute('DELETE FROM office_migrations WHERE version=19');tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='office_migrations'")];snapshot=lambda:{t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables};before=snapshot();migrate(self.store);migrate(self.store);self.assertEqual(before,snapshot());self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_restore_invalidates_unapplied_approvals_and_keeps_controlled_assignments(self):
        self.adopt();self.apply('assign',self.assign_values(),self.b);r=self.o.readiness;i=r.prepare(self.owner,'u','update',self.b,self.update_values());r.decide(self.owner,i,'approve',True);src=Path(self.temp.name)/'backup.db';dst=Path(self.temp.name)/'restore.db';backup(self.path,src);restore(src,dst);db=sqlite3.connect(dst)
        try:self.assertEqual(db.execute('SELECT status FROM br_requests WHERE id=?',(i,)).fetchone()[0],'approval_pending');self.assertEqual(db.execute('SELECT COUNT(*) FROM br_assignment_controls').fetchone()[0],1);self.assertEqual(db.execute('SELECT active FROM bf_ai_assignments').fetchone()[0],0)
        finally:db.close()

    def test_stateless_http_preview_does_not_save_request_or_business(self):
        from urllib.parse import urlencode
        h,c=self.headers();body=urlencode({'csrf':c,'kind':'create','payload':json.dumps(self.new_values())}).encode();before=self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0];reply=OfficeApp(self.path,8765).handle('POST','/business-readiness-preview',h,body);self.assertEqual(reply[0],200);self.assertEqual(json.loads(reply[2])['after']['name'],'合成の新事業');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM br_requests').fetchone()[0],0);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0],before)
    def test_phase18_waiting_assignment_and_handoff_use_approved_settings_only(self):
        self.adopt();d=self.deal('mock');a=self.o.collaboration;job=a.submit(self.owner,self.b,'ready',d,'inquiry',self.day.isoformat());self.assertEqual(a.row(self.owner,self.b,job)['state'],'waiting_admin');r=self.o.readiness;i=r.prepare(self.owner,'assign','assign',self.b,self.assign_values());r.decide(self.owner,i,'approve',True);self.assertIsNone(a.dispatch(self.owner,self.b,job));r.apply(self.owner,i);self.assertTrue(a.dispatch(self.owner,self.b,job));self.apply('assign',self.assign_values('AI20'),self.b);a.handoff(self.owner,self.b,job,'handoff','AI20','inquiry','合成の同一事業引き継ぎ');self.apply('unassign',{'agent':'AI20'},self.b)
        with self.assertRaises(PermissionError):a.run(self.owner,self.b,job)

    def test_batch_assignments_are_one_approved_change_and_do_not_touch_other_roles(self):
        self.adopt();r=self.o.readiness;payload={'assignments':[self.assign_values('AI28'),self.assign_values('AI17')]};i=r.prepare(self.owner,'batch','assign_many',self.b,payload);r.decide(self.owner,i,'approve',True);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0);r.apply(self.owner,i);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],2);self.assertTrue(ai_allowed(self.store,'AI17',self.b,'work'));self.assertTrue(ai_allowed(self.store,'AI28',self.b,'work'));self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM br_assignment_controls').fetchone()[0],2)
    def test_batch_failure_rolls_back_all_assignment_changes(self):
        from unittest.mock import patch
        self.adopt();r=self.o.readiness;i=r.prepare(self.owner,'batch','assign_many',self.b,{'assignments':[self.assign_values('AI17'),self.assign_values('AI28')]});r.decide(self.owner,i,'approve',True);original=self.o.business_foundation.assign;calls=[]
        def failing(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise ValueError('synthetic failure')
            return original(*args,**kwargs)
        with patch.object(self.o.business_foundation,'assign',side_effect=failing):
            with self.assertRaises(ValueError):r.apply(self.owner,i)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM br_assignment_controls').fetchone()[0],0);self.assertEqual(r.row(self.owner,i)['status'],'approved')
    def test_batch_rejects_duplicates_unknown_employees_and_unbounded_changes(self):
        self.adopt()
        for nodes in ([self.assign_values()]*2,[self.assign_values('AI99')],[self.assign_values()]*29):
            with self.assertRaises(ValueError):self.o.readiness.prepare(self.owner,'batch','assign_many',self.b,{'assignments':nodes})
