"""Synthetic local phase-9 scenario; no persistent business DB or external calls."""
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from .store import Store,now
from .office import Office
from .seminars import SEGMENTS,ANSWERS,BUSINESS_COPY
from .integrations.adapters import Principal


def demo():
    with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'SEMINAR_CARD_HMAC_KEY':'synthetic-demo-key-not-for-production-12345'}):
        store=Store(Path(directory)/'synthetic.db'); office=Office(store); sem=office.seminars
        representative=Principal('representative','synthetic_test_representative')
        clock=now(); start=clock+timedelta(days=3)
        identifier=sem.save(representative,{'title':'合成の健康・美容セミナー','start_at':start.isoformat(),'end_at':(start+timedelta(hours=1)).isoformat(),'deadline':(start-timedelta(hours=1)).isoformat(),'location':'合成オンライン会場','fee_yen':0,'description':'実際の商品・サービスに関する情報の確認を想定した合成テスト。事実・規約・法令は未確認。','audience':'public','capacity':20})
        for segment,answer in [('dealer','yes'),('subdealer','maybe'),('user','no'),('cast','unanswered')]:
            username='synthetic_'+segment
            office.add_user(username,'participant','SyntheticDemoPassword!123')
            sem.account(representative,username,'合成 '+SEGMENTS[segment],'synthetic-only-no-real-verification',True)
            sem.register(username,segment,'' if segment=='cast' else 'DEMO-'+segment+'-12345')
            sem.decide_registration(representative,username,'approve')
            sem.consent(username,True)
            if answer!='unanswered': sem.respond(username,identifier,answer)
        sem.schedule(representative,True); prepared=sem.tick(representative); duplicate=sem.tick(representative)
        sem.schedule(representative,False)
        tasks=sem.prepare_work(representative,identifier)
        office.selection.set_enabled(representative,True)
        plan=office.selection.propose(representative,'synthetic-selection','sns',(clock.date()-timedelta(days=1)).isoformat(),(clock.date()+timedelta(days=1)).isoformat())
        office.selection.set_enabled(representative,False)
        selected=store.db.execute('SELECT chosen,reason,status FROM selection_plans WHERE id=?',(plan,)).fetchone()
        recipients=sem.recipients(representative,identifier)
        result={'mode':'synthetic_temporary_database','real_identity_verification':False,'production_approval':False,
            'business_copy':BUSINESS_COPY,'migration_version':9,'seminar_id':identifier,
            'counts':{label:sum(r['answer']==key for r in recipients) for key,label in ANSWERS.items()},
            'reminders_prepared':prepared,'duplicate_preparation':duplicate,
            'reminder_states':[r[0] for r in store.db.execute('SELECT status FROM seminar_reminders')],
            'representative_approval':'原稿・再案内は代表承認待ちで停止',
            'local_roles':[r[0] for r in store.db.execute('SELECT agent FROM seminar_work')],
            'task_count':len(tasks),'selection':dict(selected),
            'scheduler_enabled':False,'selection_enabled':False,
            'mock_delivery_attempts':store.db.execute('SELECT COUNT(*) FROM seminar_attempts').fetchone()[0],
            'real_external_services':[],'new_paid_api_calls':0,
            'foreign_key_violations':len(store.db.execute('PRAGMA foreign_key_check').fetchall())}
        assert prepared==3 and duplicate==0 and set(result['reminder_states'])=={'approval_pending'}
        store.close(); return result

if __name__=='__main__': print(json.dumps(demo(),ensure_ascii=False,indent=2))
