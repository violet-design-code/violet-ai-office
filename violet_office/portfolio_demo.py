"""Synthetic phase-10 scenario, always temporary DB, no external clients."""
import json
import tempfile
from pathlib import Path
from datetime import timedelta
from .store import Store,now
from .office import Office,JST
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db'); office=Office(store)
        office.add_user('synthetic_owner','representative','SyntheticDemoPass!123'); owner=Principal('representative','synthetic_owner'); service=office.portfolio
        service.seed(owner)
        today=now().astimezone(JST).date(); start=today.isoformat(); end=(today+timedelta(days=1)).isoformat()
        contact=service.contact(owner,'BIZ-B','合成シナリオの顧客','synthetic-customer-reference')
        for purpose in ('contact','schedule'): service.consent(owner,'BIZ-B',contact,purpose,True,'synthetic-only-consent')
        inquiry=service.case(owner,'BIZ-B','synthetic-inquiry','inquiry','鏡清掃の問い合わせ',contact)
        estimate=service.document(owner,'BIZ-B','estimate',inquiry)
        quote=service.operation(owner,'BIZ-B','synthetic-quote','quote_send',inquiry,estimate)
        meeting=now()+timedelta(days=3)
        booking=service.case(owner,'BIZ-B','synthetic-booking','booking_request','清掃の日程希望',contact,meeting.isoformat(),(meeting+timedelta(hours=1)).isoformat())
        service.operation(owner,'BIZ-B','synthetic-booking','booking_confirm',booking)
        assignments=[]
        for business,kind,instruction in [('BIZ-A','sns','健康・美容事業の原稿'),('BIZ-B','copy','清掃事業の返信文案'),('BIZ-C','planning','制作のヒアリング手順'),('BIZ-D','operations','相談受付と同意の手順'),('BIZ-E','copy','セルフホワイトニングの案内'),('BIZ-F','copy','スマホ教室の案内')]:
            task=service.task(owner,business,'synthetic-task',kind,instruction)
            service.run(owner,business,task)
            assignments.append({'business':business,'agent':store.db.execute('SELECT agent FROM tasks WHERE id=?',(task,)).fetchone()[0],'task_id':task})
        service.ledger(owner,'BIZ-B','synthetic-revenue',start,'revenue',6000,'actual','合成テストの申告。実際の売上ではない。')
        service.ledger(owner,'BIZ-B','synthetic-expense',start,'expense',1500,'actual','合成テストの申告。実際の経費ではない。')
        service.measure(owner,'BIZ-B',start,'inquiries',1,'actual','合成シナリオ')
        service.measure(owner,'BIZ-B',start,'owner_minutes',12,'actual','合成シナリオ')
        seminar=office.seminars.save(owner,{'title':'合成の事業セミナー','start_at':meeting.isoformat(),'end_at':(meeting+timedelta(hours=1)).isoformat(),'deadline':meeting.isoformat(),'location':'合成の会場','fee_yen':0,'description':'連携の参照だけを試験','audience':'public','capacity':''})
        before=dict(office.seminars.get(seminar)); service.link_seminar(owner,'BIZ-A',seminar)
        report=service.report(owner,start,end)
        result={'mode':'synthetic_temporary_database_not_real_business_results','source':'代表が確認した6事業の名称と提供状況。金額・件数は合成の検証値。','businesses':[dict(r) for r in store.db.execute('SELECT id,name,status,pricing,region FROM portfolio_businesses ORDER BY id')],
            'assignments':assignments,'registered_agents':len(store.agents),'dashboard':service.dashboard(owner,start,end),'report_id':report,'report_status':'approval_pending',
            'external_operation_states':[r[0] for r in store.db.execute('SELECT status FROM portfolio_operations')],
            'seminar_original_unchanged':before==dict(office.seminars.get(seminar)),
            'old_data_automatically_assigned':False,'actual_external_services':[],
            'new_paid_api_calls':0,'external_send_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall()),'production_started':False}
        assert set(result['external_operation_states'])=={'approval_pending'} and result['registered_agents']==28
        assert result['seminar_original_unchanged'] and result['external_send_attempts']==0
        store.close(); return result

if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
