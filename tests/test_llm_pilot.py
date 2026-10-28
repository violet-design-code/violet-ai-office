"""Three-employee pilot. Synthetic SQLite + fake transports only; never paid API calls."""
import json,os,sqlite3,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlencode
from unittest.mock import patch
from uuid import uuid4
from tests import test_sales as helpers
from violet_office.office import Office
from violet_office.store import Store,now
from violet_office.seminars import JST
from violet_office.llm_pilot import migrate,STAGES
from violet_office.llm_provider import MockProvider,OpenAIProvider,ModelError,Result,config
from violet_office.integrations.adapters import Principal
from violet_office.web import OfficeApp
from violet_office.ops import backup,restore

ENV={'VIOLET_LLM_LIVE_ENABLED':'1','OPENAI_API_KEY':'synthetic-key-not-valid-for-api','VIOLET_LLM_MODEL':'synthetic-test-model','VIOLET_LLM_INPUT_USD_PER_MILLION':'1','VIOLET_LLM_OUTPUT_USD_PER_MILLION':'2','VIOLET_LLM_PRICE_REFERENCE':'synthetic price for fake-transport tests only'}
class FakeLive(MockProvider):mode='openai'
class Failure(MockProvider):
    def __init__(self,code='rate_limited',unknown=False,retryable=True):self.code=code;self.unknown=unknown;self.retryable=retryable
    def invoke(self,*args):raise ModelError(self.code,self.unknown,self.retryable)

class PilotTests(unittest.TestCase):
    setUp=helpers.SalesTests.setUp
    tearDown=helpers.SalesTests.tearDown
    headers=helpers.SalesTests.headers
    operator=helpers.SalesTests.operator
    customer=helpers.SalesTests.customer
    def assigned(self,b=None):
        b=b or self.b;f=self.o.business_foundation
        if not self.store.db.execute('SELECT 1 FROM bf_profiles WHERE business_id=?',(b,)).fetchone():f.adopt(self.owner,b,'合成の試験事業')
        for a in ('AI01','AI07','AI09'):
            old=self.store.db.execute('SELECT revision FROM bf_ai_assignments WHERE business_id=? AND agent=?',(b,a)).fetchone();f.assign(self.owner,b,a,'合成の下書き','当該合成事業のみ',['business_read','work'],True,old[0] if old else 0)
    def job(self,mode='mock',key=None,b=None,count=1):
        return self.o.llm.create(self.owner,b or self.b,key or uuid4().hex,'Instagram投稿の下書きを作成してください。提供条件は要確認。',count,mode,True)
    def prepare(self,mode='mock'):
        self.assigned();return self.job(mode)
    def caps(self,amount=1000000):
        month=now().astimezone(JST).strftime('%Y-%m');self.o.portfolio.budget(self.owner,self.b,month,amount);self.store.db.execute('INSERT OR REPLACE INTO office_budgets VALUES(?,?)',(month,amount))
    def live(self):
        self.assigned();self.caps();j=self.job('openai');self.o.llm.authorize_live(self.owner,self.b,j,True);return j
    def post(self,data,user='owner',role='representative',csrf=True):
        h,c=self.headers(user,role);return OfficeApp(self.path,8765).handle('POST','/llm-pilot-action',h,urlencode({**({'csrf':c} if csrf else {}),**data}).encode())
    def test_28_definitions_business_data_and_customer_preserved(self):
        before=Path('config/agents.json').read_bytes();rows=[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')];c=self.customer();j=self.prepare();self.o.llm.execute_job(self.owner,self.b,j);self.assertEqual(before,Path('config/agents.json').read_bytes());self.assertEqual(rows,[tuple(r) for r in self.store.db.execute('SELECT * FROM portfolio_businesses')]);self.assertIsNotNone(self.store.db.execute('SELECT 1 FROM portfolio_contacts WHERE id=?',(c,)).fetchone());self.assertEqual(len(self.store.agents),28)
    def test_missing_assignment_does_not_create_job_or_grant_rights(self):
        with self.assertRaises(PermissionError):self.job()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lp_jobs').fetchone()[0],0);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0)
    def test_exact_four_stages_only_three_employees(self):
        j=self.prepare();self.assertEqual(self.o.llm.execute_job(self.owner,self.b,j),'approval_pending');steps=self.store.db.execute('SELECT agent,state FROM lp_steps ORDER BY step').fetchall();self.assertEqual([r[0] for r in steps],['AI01','AI07','AI09','AI01']);self.assertTrue(all(r[1]=='succeeded' for r in steps));self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lp_calls').fetchone()[0],4)
    def test_instructions_not_supported_or_unconfirmed_rejected(self):
        self.assigned()
        for brief,check in [('契約を確定して',True),('Instagram原稿',False)]:
            with self.assertRaises(ValueError):self.o.llm.create(self.owner,self.b,'bad',brief,1,'mock',check)
    def test_prompt_secret_and_contact_rejected(self):
        self.assigned()
        for brief in ('Instagram API_KEY=secrettoken123','Instagram user@example.com','Instagram 090-1234-5678'):
            with self.assertRaises(ValueError):self.o.llm.create(self.owner,self.b,uuid4().hex,brief,checked=True)
    def test_request_idempotent_and_conflicting_input_blocked(self):
        self.assigned();j=self.job(key='same');self.assertEqual(j,self.job(key='same'))
        with self.assertRaises(ValueError):self.job(key='same',count=2)
    def test_business_operator_read_scope_but_cannot_execute(self):
        self.operator();self.assigned();j=self.job();p=Principal('business_operator','staff');self.assertEqual(self.o.llm.row(p,self.b,j)['id'],j)
        with self.assertRaises(PermissionError):self.o.llm.row(p,'BIZ-A',j)
        with self.assertRaises(PermissionError):self.o.llm.execute_job(p,self.b,j)
    def test_cross_business_id_cannot_access_existing_job(self):
        j=self.prepare()
        with self.assertRaises(ValueError):self.o.llm.row(self.owner,'BIZ-A',j)
    def test_no_customer_rows_in_model_context(self):
        self.customer();j=self.prepare();payload=self.o.llm.messages(self.o.llm.row(self.owner,self.b,j),0);self.assertNotIn('合成顧客',json.dumps(payload,ensure_ascii=False));self.assertNotIn('OPENAI_API_KEY',json.dumps(payload))
    def test_prior_output_handoff_is_persisted_and_hashed(self):
        j=self.prepare();self.o.llm.execute_next(self.owner,self.b,j);r=self.o.llm.row(self.owner,self.b,j);m=json.loads(self.o.llm.messages(r,1)[1]['content']);self.assertEqual(m['untrusted_previous_artifacts'][0]['agent'],'AI01');self.store.db.execute("UPDATE lp_steps SET output='{}' WHERE job_id=? AND step=0",(j,))
        with self.assertRaises(ValueError):self.o.llm.execute_next(self.owner,self.b,j)
    def test_live_disabled_without_network_even_if_key_present(self):
        self.assigned();j=self.job('openai')
        with patch.dict(os.environ,{'VIOLET_LLM_LIVE_ENABLED':'0'}),patch('violet_office.llm_provider.build_opener',side_effect=AssertionError('Network forbidden')):
            with self.assertRaises(ModelError):self.o.llm.execute_next(self.owner,self.b,j)
    def test_live_needs_individual_approval(self):
        with patch.dict(os.environ,ENV):
            self.assigned();self.caps();j=self.job('openai')
            with self.assertRaises(PermissionError):self.o.llm.execute_next(self.owner,self.b,j,FakeLive())
    def test_missing_or_zero_budget_blocks_before_provider(self):
        with patch.dict(os.environ,ENV):
            j=self.live();self.caps(0)
            with self.assertRaises(ValueError):self.o.llm.execute_next(self.owner,self.b,j,FakeLive())
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lp_calls').fetchone()[0],0)
    def test_fake_live_usage_estimates_not_confirmed_billing(self):
        with patch.dict(os.environ,ENV):
            j=self.live();self.o.llm.execute_next(self.owner,self.b,j,FakeLive());c=self.store.db.execute('SELECT * FROM lp_calls').fetchone();self.assertEqual((c['input_tokens'],c['output_tokens']),(100,100));self.assertEqual(c['estimated_microusd'],300);cost=self.store.db.execute('SELECT * FROM office_costs WHERE id=?',(c['office_cost'],)).fetchone();self.assertEqual(cost['kind'],'estimated');self.assertEqual(cost['agent'],'AI01')
    def test_two_pending_calls_reserve_global_budget_atomically(self):
        with patch.dict(os.environ,ENV):
            j=self.live();call,_,_,_=self.o.llm.begin(self.owner,self.b,j);hold=self.store.db.execute('SELECT reserved_microusd FROM lp_calls WHERE id=?',(call,)).fetchone()[0];self.caps(hold);z=self.job('openai');self.o.llm.authorize_live(self.owner,self.b,z,True)
            with self.assertRaises(ValueError):self.o.llm.begin(self.owner,self.b,z)
    def test_model_price_change_invalidates_paid_approval(self):
        with patch.dict(os.environ,ENV):
            j=self.live()
            with patch.dict(os.environ,{'VIOLET_LLM_INPUT_USD_PER_MILLION':'3'}),self.assertRaises(PermissionError):self.o.llm.begin(self.owner,self.b,j)
    def test_io_happens_outside_write_transaction(self):
        fixture=self
        class AssertProvider(MockProvider):
            def invoke(self,*args):fixture.assertFalse(fixture.store.db.in_transaction);return super().invoke(*args)
        j=self.prepare();self.o.llm.execute_next(self.owner,self.b,j,AssertProvider())
    def test_concurrent_execution_claim_prevents_duplicate_model_call(self):
        j=self.prepare();call,_,_,_=self.o.llm.begin(self.owner,self.b,j)
        store=Store(self.path)
        try:
            with self.assertRaises(ValueError):Office(store).llm.begin(self.owner,self.b,j)
        finally:store.close()
    def test_stale_step_form_does_not_execute_next_stage(self):
        j=self.prepare();self.o.llm.execute_next(self.owner,self.b,j,expected_step=0)
        with self.assertRaises(ValueError):self.o.llm.execute_next(self.owner,self.b,j,expected_step=0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lp_calls').fetchone()[0],1)
    def test_retry_only_known_rejection_bounded_three_and_delayed(self):
        j=self.prepare();s=self.o.llm;s.execute_next(self.owner,self.b,j,Failure())
        with self.assertRaises(ValueError):s.execute_next(self.owner,self.b,j,Failure())
        for _ in range(2):self.store.db.execute('UPDATE lp_calls SET retry_at=NULL');s.execute_next(self.owner,self.b,j,Failure())
        self.assertEqual(s.row(self.owner,self.b,j)['state'],'failed');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lp_calls').fetchone()[0],3)
    def test_unknown_result_never_retried_and_budget_remains_held(self):
        j=self.prepare();self.o.llm.execute_next(self.owner,self.b,j,Failure('network_result_unknown',True,False));self.assertEqual(self.o.llm.row(self.owner,self.b,j)['state'],'reconciliation_required');self.assertEqual(self.store.db.execute('SELECT state FROM lp_calls').fetchone()[0],'unknown')
        with self.assertRaises(ValueError):self.o.llm.execute_next(self.owner,self.b,j)
    def test_stop_and_resume_without_automatic_execution(self):
        j=self.prepare();self.o.llm.control(self.owner,self.b,j,'stop')
        self.assertEqual(self.o.llm.execute_job(self.owner,self.b,j),'paused')
        self.o.llm.control(self.owner,self.b,j,'resume');self.assertEqual(self.o.llm.row(self.owner,self.b,j)['state'],'pending');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM lp_calls').fetchone()[0],0)
    def test_global_stop_blocks_jobs(self):
        j=self.prepare();self.o.llm.halt(self.owner,True)
        with self.assertRaises(ValueError):self.o.llm.execute_next(self.owner,self.b,j)
    def test_stop_during_model_call_holds_result(self):
        fixture=self
        class StopProvider(MockProvider):
            def invoke(self,*args):fixture.o.llm.control(fixture.owner,fixture.b,j,'stop');return super().invoke(*args)
        j=self.prepare();self.assertEqual(self.o.llm.execute_next(self.owner,self.b,j,StopProvider()),'paused');self.assertEqual(self.store.db.execute('SELECT state FROM lp_steps WHERE step=0').fetchone()[0],'held')
    def test_assignment_revoked_during_io_holds_result(self):
        fixture=self
        class RevokeProvider(MockProvider):
            def invoke(self,*args):fixture.store.db.execute("UPDATE bf_ai_assignments SET active=0 WHERE business_id=? AND agent='AI09'",(fixture.b,));return super().invoke(*args)
        j=self.prepare();self.assertEqual(self.o.llm.execute_next(self.owner,self.b,j,RevokeProvider()),'paused')
    def test_privilege_revoked_during_io_usage_still_recorded(self):
        fixture=self
        class RevokeProvider(MockProvider):
            def invoke(self,*args):fixture.store.db.execute("UPDATE office_users SET role='viewer' WHERE username='owner'");return super().invoke(*args)
        j=self.prepare();self.assertEqual(self.o.llm.execute_next(self.owner,self.b,j,RevokeProvider()),'paused');self.assertIsNotNone(self.store.db.execute('SELECT input_tokens FROM lp_calls').fetchone()[0])
    def test_output_attempt_to_add_employee_or_secret_is_rejected(self):
        class Bad(MockProvider):
            def invoke(self,*args):return Result({'purpose':'合成','assignments':['AI28'],'notes':'合成'},100,100)
        j=self.prepare();self.assertEqual(self.o.llm.execute_next(self.owner,self.b,j,Bad()),'failed');self.assertIsNone(self.store.db.execute('SELECT output FROM lp_steps WHERE step=0').fetchone()[0]);self.assertEqual(self.store.db.execute('SELECT input_tokens FROM lp_calls').fetchone()[0],100)
    def test_raw_exception_not_saved(self):
        class Bad(MockProvider):
            def invoke(self,*args):raise RuntimeError('secret-live-key-never-log')
        j=self.prepare();self.o.llm.execute_next(self.owner,self.b,j,Bad());dump='\n'.join(self.store.db.iterdump());self.assertNotIn('secret-live-key-never-log',dump)
    def test_core_controls_cannot_bypass_scoped_pilot(self):
        j=self.prepare();task=self.o.llm.row(self.owner,self.b,j)['final_task']
        with self.assertRaises(PermissionError):self.store.claim(task)
        self.o.llm.execute_job(self.owner,self.b,j);r=self.o.llm.row(self.owner,self.b,j)
        with self.assertRaises(PermissionError):self.store.review(r['final_artifact'],self.store.agents['AI01']['reviewer'],'pass')
    def test_representative_approval_records_existing_approval_and_no_send(self):
        j=self.prepare();self.o.llm.execute_job(self.owner,self.b,j)
        with self.assertRaises(ValueError):self.o.llm.decide(self.owner,self.b,j,'approve',False,'合成の確認')
        self.o.llm.decide(self.owner,self.b,j,'approve',True,'合成の内容・事実・権利等を人間が確認');r=self.o.llm.row(self.owner,self.b,j);self.assertEqual(r['state'],'approved_draft_no_execution');self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM approvals WHERE task_id=?',(r['final_task'],)).fetchone()[0],1);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM portfolio_operations').fetchone()[0],0)
    def test_quarantine_stale_call_keeps_unknown_state(self):
        j=self.prepare();self.o.llm.begin(self.owner,self.b,j);self.store.db.execute('UPDATE lp_calls SET created_at=?',((now()-timedelta(seconds=100)).isoformat(),));self.o.llm.quarantine(self.owner,self.b,j);self.assertEqual(self.o.llm.row(self.owner,self.b,j)['state'],'reconciliation_required')
    def test_migration_backup_exists_and_migration_repeats(self):
        files=list((self.path.parent/'backups').glob('*before-llm*.sqlite3'));self.assertEqual(len(files),1);db=sqlite3.connect('file:'+str(files[0])+'?mode=ro',uri=True)
        try:self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='lp_jobs'").fetchone())
        finally:db.close()
        migrate(self.store);self.assertEqual(len(list((self.path.parent/'backups').glob('*before-llm*.sqlite3'))),1)
    def test_restore_preserves_drafts_and_holds_execution(self):
        j=self.prepare();self.o.llm.execute_next(self.owner,self.b,j);a=Path(self.temp.name)/'copy.db';z=Path(self.temp.name)/'restored.db';backup(self.path,a);restore(a,z);db=sqlite3.connect(z)
        try:self.assertEqual(db.execute('SELECT state FROM lp_jobs').fetchone()[0],'paused');self.assertIsNotNone(db.execute('SELECT output FROM lp_steps WHERE step=0').fetchone()[0]);self.assertIsNone(db.execute('SELECT live_approval FROM lp_jobs').fetchone()[0])
        finally:db.close()
    def test_http_auth_csrf_and_no_default_business_seed(self):
        h,c=self.headers();app=OfficeApp(self.path,8765);self.assertEqual(app.handle('GET','/llm-pilot?business='+self.b,h)[0],200);self.assertEqual(self.post({'action':'create','business':self.b},csrf=False)[0],403);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM bf_ai_assignments').fetchone()[0],0)

    def test_backup_failure_blocks_new_migration(self):
        self.store.db.execute('DELETE FROM office_migrations WHERE version=22')
        with patch('violet_office.ops.backup',side_effect=OSError('synthetic backup failure')):
            with self.assertRaises(OSError):migrate(self.store)
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM office_migrations WHERE version=22').fetchone())
    def test_business_dashboard_includes_fake_live_estimate(self):
        with patch.dict(os.environ,ENV):
            j=self.live();self.o.llm.execute_next(self.owner,self.b,j,FakeLive());start=now().astimezone(JST).date();r=self.o.portfolio.summary(self.owner,self.b,start.isoformat(),(start+timedelta(days=1)).isoformat());self.assertEqual(r['api_microusd']['estimated'],300);self.assertIsNone(r['api_microusd']['confirmed'])
    def test_price_and_key_not_saved_in_prompts_or_logs(self):
        with patch.dict(os.environ,ENV):
            j=self.live();self.o.llm.execute_next(self.owner,self.b,j,FakeLive());self.assertNotIn(ENV['OPENAI_API_KEY'],'\n'.join(self.store.db.iterdump()))
    def test_openai_transport_contract_mocked_no_network(self):
        data={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'purpose':'合成','assignments':['AI07','AI09'],'notes':'合成'})}}],'usage':{'prompt_tokens':11,'completion_tokens':12}}
        class Response:
            def __init__(self):self.parts=[json.dumps(data).encode(),b'']
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read1(self,n):return self.parts.pop(0)
        with patch.dict(os.environ,ENV),patch('violet_office.llm_provider.build_opener') as opener:
            opener.return_value.open.return_value=Response();r=OpenAIProvider().invoke([{'role':'user','content':'JSON 合成'}],config('openai'),'synthetic-request');req=opener.return_value.open.call_args.args[0];self.assertEqual(req.full_url,'https://api.openai.com/v1/chat/completions');self.assertEqual(r.input_tokens,11);self.assertEqual(json.loads(req.data)['response_format'],{'type':'json_object'});self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],20)
    def test_transport_no_fallback_when_live_not_enabled(self):
        with patch.dict(os.environ,{'VIOLET_LLM_LIVE_ENABLED':'0'}),patch('violet_office.llm_provider.build_opener') as opener:
            with self.assertRaises(ModelError):OpenAIProvider().invoke([],{},'none')
            opener.assert_not_called()
    def test_transport_timeout_becomes_unknown_without_raw_error(self):
        with patch.dict(os.environ,ENV),patch('violet_office.llm_provider.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError('secret-header-do-not-log')
            with self.assertRaises(ModelError) as context:OpenAIProvider().invoke([],config('openai'),'synthetic')
            self.assertTrue(context.exception.unknown);self.assertNotIn('secret-header',str(context.exception))
    def test_transport_redirect_is_rejected(self):
        from violet_office.llm_provider import NoRedirect
        with self.assertRaises(ModelError):NoRedirect().redirect_request(None,None,None,None,None,None)
    def test_restore_cannot_reuse_live_approval(self):
        with patch.dict(os.environ,ENV):
            j=self.live();a=Path(self.temp.name)/'copy.db';z=Path(self.temp.name)/'restored.db';backup(self.path,a);restore(a,z);db=sqlite3.connect(z)
            try:self.assertIsNone(db.execute('SELECT live_approval FROM lp_jobs').fetchone()[0])
            finally:db.close()

    def test_pre_start_backup_precedes_any_schema_initialization(self):
        from violet_office.llm_pilot import backup_before_start
        old=Path(self.temp.name)/'older.db';db=sqlite3.connect(old);db.execute('CREATE TABLE preserved(value TEXT)');db.execute("INSERT INTO preserved VALUES('synthetic')");db.commit();db.close()
        # A healthy real Store database is required; an arbitrary file is refused rather than mutated.
        before=old.read_bytes()
        with self.assertRaises(ValueError):backup_before_start(old)
        self.assertEqual(before,old.read_bytes())

    def test_populated_legacy_migration_preserves_business_customers_assignments(self):
        from violet_office.llm_pilot import backup_before_start
        self.customer();self.assigned();legacy=Path(self.temp.name)/'legacy-populated.db';db=sqlite3.connect(legacy);self.store.db.backup(db)
        # Reconstruct pre-22 schema in a throwaway copy, never in the source or business DB.
        for name in ('lp_audit','lp_calls','lp_steps','lp_controls','lp_jobs'):db.execute('DROP TABLE '+name)
        db.execute('DELETE FROM office_migrations WHERE version=22');db.commit();db.close()
        saved=backup_before_start(legacy);self.assertTrue(saved.is_file());store=Store(legacy)
        try:
            tables=('portfolio_businesses','portfolio_contacts','bf_ai_assignments','office_users');before={t:[tuple(r) for r in store.db.execute('SELECT * FROM '+t)] for t in tables};migrate(store);self.assertEqual(before,{t:[tuple(r) for r in store.db.execute('SELECT * FROM '+t)] for t in tables});self.assertEqual(store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:store.close()
