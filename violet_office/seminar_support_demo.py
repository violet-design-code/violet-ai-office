"""Disposable synthetic seminar operations, no real sending or customer records."""
import json,tempfile
from pathlib import Path
from datetime import datetime,time,timedelta
from .store import Store,now
from .office import Office
from .seminars import JST
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory:
        store=Store(Path(directory)/'synthetic.db'); office=Office(store); office.add_user('synthetic_owner','representative','SyntheticDemoPass!123'); owner=Principal('representative','synthetic_owner'); office.portfolio.seed(owner)
        service=office.seminar_support; business='BIZ-B'; day=now().astimezone(JST).date(); start=datetime.combine(day+timedelta(days=3),time(18),JST)
        sid=service.save(owner,business,{'title':'合成の健康・美容セミナー','start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat(),'deadline':(start-timedelta(hours=2)).isoformat(),'location':'合成会場','fee_yen':0,'capacity':10,'description':'合成の検証内容。具体的条件は代表確認待ち。'},['cast'],'https://example.com/synthetic-meeting','mock')
        office.add_user('synthetic_participant','participant','SyntheticParticipantPass!123'); participant=Principal('participant','synthetic_participant'); office.seminars.account(owner,participant.identity,'合成参加者','合成の本人確認',True); office.seminars.register(participant.identity,'cast',''); office.seminars.decide_registration(owner,participant.identity,'approve'); office.seminars.consent(participant.identity,True)
        for purpose in ('invite','reminders','followup'): service.consent(participant,sid,purpose,True)
        service.preferences(participant,sid,['health','beauty']); service.response(participant,sid,'yes'); service.request_interview(participant,sid)
        invite=service.prepare(owner,business,sid,participant.identity,'invite'); service.decide(owner,business,invite,'approve',True); service.mock(owner,business,invite)
        reminder=service.prepare(owner,business,sid,participant.identity,'previous_day')
        task=service.work(owner,business,sid,'copy','synthetic-copy','AI17'); service.run(owner,business,task)
        result={'mode':'synthetic_temporary_database_not_real_business_results','registered_agents':len(store.agents),'seminar_mode':'mock','invitation_state':store.db.execute('SELECT status FROM s14_drafts WHERE id=?',(invite,)).fetchone()[0],
            'reminder_state':store.db.execute('SELECT status FROM s14_drafts WHERE id=?',(reminder,)).fetchone()[0],'local_task':'AI17 / fixed_template / representative_review_pending',
            'metrics':service.metrics(owner,business,sid),'scheduler_started':False,'real_booking_records':store.db.execute('SELECT COUNT(*) FROM scm_bookings').fetchone()[0],
            'invoice_records':store.db.execute('SELECT COUNT(*) FROM scm_invoices').fetchone()[0],'external_send_attempts':store.db.execute('SELECT COUNT(*) FROM ix_attempts').fetchone()[0],
            'actual_external_services':[],'paid_api_calls':0,'production_started':False,'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall())}
        assert result['real_booking_records']==result['invoice_records']==result['external_send_attempts']==result['foreign_key_violations']==0
        assert result['metrics']['actual']['applicants'] is None and result['metrics']['mock_preview']['applicants']==1
        store.close(); return result


if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
