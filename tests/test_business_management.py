"""Phase20 confirmation UI, immutable registry and synthetic SQLite fixtures."""
import json,sqlite3,unittest
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
from tests import test_business_readiness as helpers
from violet_office.business_management import migrate
from violet_office.business_management_views import confirm
from violet_office.integrations.adapters import Principal
from violet_office.business_foundation import ai_allowed
from violet_office.ops import backup,restore
from violet_office.web import OfficeApp

class ManagementTests(unittest.TestCase):
    setUp=helpers.ReadinessTests.setUp
    tearDown=helpers.ReadinessTests.tearDown
    headers=helpers.ReadinessTests.headers
    post=helpers.ReadinessTests.post
    operator=helpers.ReadinessTests.operator
    customer=helpers.ReadinessTests.customer
    deal=helpers.ReadinessTests.deal
    new_values=helpers.ReadinessTests.new_values
    update_values=helpers.ReadinessTests.update_values
    assign_values=helpers.ReadinessTests.assign_values
    adopt=helpers.ReadinessTests.adopt
    apply=helpers.ReadinessTests.apply
    def meta(self,**changes):return {'provided_services':'合成サービス','audience':'合成対象','manager':'owner','status':'preparing','confirmed':False,'month':'2026-10','revenue_yen':None,'profit_yen':None,**changes}
    def create(self,**changes):return self.apply('create',{**self.new_values(),'management':self.meta(**changes)})
    def edit(self,b,**changes):
        m=self.meta(**changes)
        return self.apply('update',{**self.update_values(b,status=m['status'],enabled=m['status']!='paused',confirmed=m['confirmed']),'management':m},b)
    def app(self):return OfficeApp(self.path,8765)
    def form_data(self,kind='create',b=None,**changes):return {'key':uuid4().hex,'business':b or '', 'kind':kind,'snapshot':self.o.readiness.snapshot(b),'name':'合成の新事業','overview':'合成の内容','category':'合成カテゴリー','provided_services':'合成サービス','audience':'合成対象','manager':'owner','status':'preparing','month':'2026-10','revenue_yen':'','profit_yen':'',**changes}
    def submit(self,data,user='owner',role='representative'):
        h,c=self.headers(user,role);return self.app().handle('POST','/business-management-confirm',h,urlencode({'csrf':c,**data}).encode())
    def test_new_business_button_and_28_cards(self):
        h,_=self.headers();self.assertIn('＋ 新規事業を追加',self.app().handle('GET','/business-management',h)[2].decode());body=self.app().handle('GET','/ai-management',h)[2].decode()
        for a in self.store.agents:self.assertIn(a,body)
    def test_new_form_confirm_approval_apply_separate(self):
        count=self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0];reply=self.submit(self.form_data());self.assertEqual(reply[0],200);self.assertIn('保存前の確認',reply[2].decode());r=self.store.db.execute('SELECT * FROM br_requests').fetchone();self.assertEqual(r['status'],'approval_pending');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0],count)
        self.o.readiness.decide(self.owner,r['id'],'approve',True);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_businesses').fetchone()[0],count);b=self.o.readiness.apply(self.owner,r['id']);self.assertEqual(self.store.db.execute('SELECT manager FROM bm_profiles WHERE business_id=?',(b,)).fetchone()[0],'owner')
    def test_monthly_goals_never_ledger_entries(self):
        b=self.create(revenue_yen=120000,profit_yen=30000);self.edit(b,month='2026-11',revenue_yen=200000,profit_yen=50000)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bm_month_goals WHERE business_id=?',(b,)).fetchone()[0],2);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_ledger WHERE business_id=?',(b,)).fetchone()[0],0)
    def test_blank_goals_null_and_zero_distinct(self):
        b=self.create();r=self.store.db.execute('SELECT * FROM bm_month_goals WHERE business_id=?',(b,)).fetchone();self.assertIsNone(r['revenue_yen']);self.edit(b,revenue_yen=0);self.assertEqual(self.store.db.execute('SELECT revenue_yen FROM bm_month_goals WHERE business_id=?',(b,)).fetchone()[0],0)
    def test_clear_goals_preserves_other_months(self):
        b=self.create(revenue_yen=100);self.edit(b,month='2026-11',revenue_yen=200);self.edit(b);self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT month,revenue_yen FROM bm_month_goals WHERE business_id=? ORDER BY month',(b,))],[('2026-10',None),('2026-11',200)])
    def test_invalid_money_and_month_rejected(self):
        for change in ({'revenue_yen':-1},{'profit_yen':True},{'revenue_yen':1.5},{'revenue_yen':10**13},{'month':'2026-13'},{'month':None,'revenue_yen':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.o.readiness.preview(self.owner,'create',None,{**self.new_values(),'management':self.meta(**change)})
    def test_manager_must_be_existing_allowed_account(self):
        self.o.add_user('guest','viewer','SyntheticViewerPass!123')
        for name in ('missing','guest'):
            with self.assertRaises(ValueError):self.create(manager=name)
    def test_manager_does_not_grant_acl(self):
        self.operator();b=self.create(manager='staff');self.assertIsNone(self.store.db.execute('SELECT 1 FROM portfolio_acl WHERE business_id=?',(b,)).fetchone())
        with self.assertRaises(PermissionError):self.o.portfolio.access(Principal('business_operator','staff'),b)
    def test_manager_change_audited_before_after(self):
        b=self.create();self.operator();self.edit(b,manager='staff');r=self.store.db.execute("SELECT * FROM br_requests WHERE kind='update'").fetchone();view=json.loads(r['preview']);self.assertEqual(view['before']['management']['manager'],'owner');self.assertEqual(view['after']['management']['manager'],'staff');self.assertEqual(r['approved_by'],'owner');self.assertEqual(r['applied_by'],'owner')
    def test_manager_role_revoked_before_apply_blocks_all(self):
        self.operator();r=self.o.readiness;i=r.prepare(self.owner,'manager','create',None,{**self.new_values(),'management':self.meta(manager='staff')});r.decide(self.owner,i,'approve',True);self.store.db.execute("UPDATE office_users SET role='viewer' WHERE username='staff'")
        with self.assertRaises(ValueError):r.apply(self.owner,i)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bm_profiles').fetchone()[0],0)
    def test_active_requires_confirmed(self):
        with self.assertRaises(ValueError):self.create(status='active')
        b=self.create(status='active',confirmed=True);self.assertEqual(self.store.db.execute('SELECT status FROM portfolio_businesses WHERE id=?',(b,)).fetchone()[0],'active')
    def test_stop_resume_existing_records_preserved(self):
        b=self.create();c=self.customer(b);self.edit(b,status='paused');self.edit(b,status='active',confirmed=True);self.assertIsNotNone(self.store.db.execute('SELECT * FROM portfolio_contacts WHERE id=? AND business_id=?',(c,b)).fetchone());self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],0)
    def test_optimistic_editor_stale_rejected(self):
        b=self.create();data=self.form_data('update',b);self.edit(b,revenue_yen=123);self.assertEqual(self.submit(data)[0],400)
    def test_approved_metadata_conflict_rejected(self):
        b=self.create();r=self.o.readiness;i=r.prepare(self.owner,'edit','update',b,{**self.update_values(b,status='preparing',confirmed=False),'management':self.meta(revenue_yen=3)});r.decide(self.owner,i,'approve',True);self.edit(b,revenue_yen=4)
        with self.assertRaises(ValueError):r.apply(self.owner,i)
    def test_replay_no_duplicates(self):
        v={**self.new_values(),'management':self.meta()};r=self.o.readiness;i=r.prepare(self.owner,'same','create',None,v);r.decide(self.owner,i,'approve',True);b=r.apply(self.owner,i);self.assertEqual(r.apply(self.owner,i),b);self.assertEqual(r.prepare(self.owner,'same','create',None,v),i)
    def test_operator_read_scoped_and_cannot_modify(self):
        self.operator();h,_=self.headers('staff','business_operator');app=self.app();self.assertEqual(app.handle('GET','/business-management?business='+self.b,h)[0],200);self.assertEqual(app.handle('GET','/business-management?business=BIZ-A',h)[0],403);self.assertEqual(self.submit(self.form_data(), 'staff','business_operator')[0],403)
    def test_csrf_and_origin_rejected(self):
        h,_=self.headers();app=self.app();self.assertEqual(app.handle('POST','/business-management-confirm',h,urlencode(self.form_data()).encode())[0],403)
    def test_unexpected_fields_no_side_effects(self):
        self.assertEqual(self.submit(self.form_data(price='123'))[0],400);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM br_requests').fetchone()[0],0)
    def test_xss_escaped_in_confirmation(self):
        reply=self.submit(self.form_data(name='<script>alert(1)</script>'));self.assertEqual(reply[0],200);self.assertNotIn('<script>',reply[2].decode())
    def test_all_28_existing_definitions_unchanged_multibusiness(self):
        before=json.dumps(self.store.agents,sort_keys=True);b=self.create();z=self.apply('create',{**self.new_values('合成の第二事業'),'management':self.meta()});v=self.assign_values();self.apply('assign',v,b);self.apply('assign',v,z);self.assertTrue(ai_allowed(self.store,'AI17',b,'work'));self.assertTrue(ai_allowed(self.store,'AI17',z,'work'));self.assertEqual(before,json.dumps(self.store.agents,sort_keys=True));self.assertEqual(len(self.store.agents),28)
    def test_assignment_form_permission_reduction_and_release(self):
        b=self.create();data={'key':'assign','business':b,'kind':'assign','snapshot':self.o.readiness.snapshot(b),'agent':'AI17','role_note':'合成の案内準備','scope_note':'合成のみ','active':'1','business_read':'1','work':'1'};self.assertEqual(self.submit(data)[0],200);r=self.store.db.execute("SELECT id FROM br_requests WHERE entry_key='assign'").fetchone()[0];self.o.readiness.decide(self.owner,r,'approve',True);self.o.readiness.apply(self.owner,r);self.apply('assign',self.assign_values(permissions=['business_read']),b);self.assertFalse(ai_allowed(self.store,'AI17',b,'work'));self.apply('unassign',{'agent':'AI17'},b);self.assertFalse(ai_allowed(self.store,'AI17',b,'business_read'))
    def test_release_preserves_pending_tasks(self):
        # Existing legacy-work protection must remain effective from the new UI.
        helpers.ReadinessTests.test_release_enforces_old_legacy_tasks_without_changing_other_employees(self)
    def test_idempotent_migration_and_foreign_keys(self):
        b=self.create();before=[tuple(r) for r in self.store.db.execute('SELECT * FROM bm_profiles')];migrate(self.store);migrate(self.store);self.assertEqual(before,[tuple(r) for r in self.store.db.execute('SELECT * FROM bm_profiles')]);self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_backup_restore_metadata_goals_preserved(self):
        b=self.create(revenue_yen=200);source=Path(self.temp.name)/'backup.db';target=Path(self.temp.name)/'restore.db';backup(self.path,source);restore(source,target);db=sqlite3.connect(target)
        try:self.assertEqual(db.execute('SELECT revenue_yen FROM bm_month_goals WHERE business_id=?',(b,)).fetchone()[0],200);self.assertEqual(db.execute('SELECT manager FROM bm_profiles WHERE business_id=?',(b,)).fetchone()[0],'owner')
        finally:db.close()
    def test_unauthenticated_pages_redirect(self):
        for route in ('/business-management','/ai-management'):self.assertEqual(self.app().handle('GET',route,{'Host':'127.0.0.1:8765'})[0],303)
