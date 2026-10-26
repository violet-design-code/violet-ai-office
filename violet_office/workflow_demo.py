"""Disposable synthetic scenario, never imports or writes business data."""
import json,tempfile
from pathlib import Path
from datetime import timedelta
from .store import Store,now
from .office import Office
from .seminars import JST
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db'); office=Office(store)
        office.add_user('synthetic_owner','representative','SyntheticDemoPass!123'); p=Principal('representative','synthetic_owner'); office.portfolio.seed(p)
        b='BIZ-B'; day=now().astimezone(JST).date(); contact=office.sales.customer(p,b,'合成の検証顧客','synthetic-channel-reference')
        for purpose in ('contact','schedule','followup'): office.portfolio.consent(p,b,contact,purpose,True,'合成データの同意。実在の顧客同意ではない。')
        deal=office.sales.deal(p,b,'synthetic-workflow',contact,'合成の鏡清掃問い合わせ','合成の検証内容。実在の顧客依頼ではない。')
        office.workflow.attach(p,b,deal); office.workflow.advance(p,b,deal,'prepare',1)
        tasks=[]
        for agent in ('AI17','AI28'):
            task=office.workflow.assign(p,b,deal,agent,agent,'合成の案内準備','合成の資料参照',day.isoformat()); office.workflow.run(p,b,task)
            tasks.append({'agent':agent,'status':office.workflow.task(p,b,task)['status'],'mode':'local_rule_template_not_llm'})
        office.add_user('synthetic_participant','participant','SyntheticParticipantPass!123'); sem=office.seminars
        sem.account(p,'synthetic_participant','合成参加者','合成の本人確認参照',True); sem.register('synthetic_participant','cast',''); sem.decide_registration(p,'synthetic_participant','approve'); sem.consent('synthetic_participant',True)
        start=now()+timedelta(days=6)
        sid=sem.save(p,{'title':'合成のセミナー','start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat(),'deadline':start.isoformat(),'location':'合成会場','fee_yen':0,'description':'合成の検証','audience':'public','capacity':''})
        office.portfolio.link_seminar(p,b,sid); office.sales.seminar(p,b,deal,sid,'synthetic_participant','合成の本人確認参照')
        office.workflow.draft(p,b,deal,sid,'synthetic_participant','invite')
        result={'mode':'synthetic_temporary_database_not_real_business_results','registered_agents':len(store.agents),'tasks':tasks,'seminar_draft_status':'approval_pending',
            'approved_queue_size':len(office.workflow.queue(p,b)),'dashboard':office.workflow.dashboard(p,b),
            'actual_external_services':[],'external_send_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'invoice_records':store.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0],'production_started':False,'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall())}
        assert result['external_send_attempts']==result['invoice_records']==result['approved_queue_size']==result['foreign_key_violations']==0
        store.close(); return result


if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
