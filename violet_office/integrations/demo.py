"""Reproducible local synthetic scenario. No customer or network input accepted."""
import base64
import hashlib
import hmac
import json
from .adapters import Authorizer, MockAdapter
from .service import Integrations
from ..content import ContentOffice, SAMPLE_BRIEF
from ..store import now


def run_demo(store):
    # These explicitly synthetic strings are not provider credentials.
    auth=Authorizer('synthetic-demo-admin-'+'a'*32,'synthetic-demo-viewer-'+'v'*32)
    admin=auth.authenticate('synthetic-demo-admin-'+'a'*32)
    service=Integrations(store,'synthetic-demo-pseudonym-'+'p'*32,'synthetic-demo-signature','synthetic-channel',MockAdapter())
    store.db.execute('CREATE TABLE IF NOT EXISTS ix_demo (id INTEGER PRIMARY KEY CHECK(id=1), timestamp INTEGER NOT NULL)')
    store.db.execute('INSERT OR IGNORE INTO ix_demo VALUES (1,?)',(int(now().timestamp()*1000)-100000,))
    timestamp=store.db.execute('SELECT timestamp FROM ix_demo WHERE id=1').fetchone()[0]
    user='U'+'0'*32
    def event(n,kind='message',text=''):
        return {'webhookEventId':f'demo-{n}','timestamp':timestamp+n,'type':kind,
            'source':{'type':'user','userId':user},'message':{'type':'text','text':text}}
    def post(events):
        body=json.dumps({'destination':'synthetic-channel','events':events},ensure_ascii=False).encode()
        signature=base64.b64encode(hmac.new(b'synthetic-demo-signature',body,hashlib.sha256).digest()).decode()
        return service.webhook(body,signature)
    post([event(1,'follow'),event(2,text='同意:事業紹介'),event(3,text='停止'),event(4,text='再同意:事業紹介'),event(5,text='面談希望')])
    subject=service.subject_ref(user)
    store.db.execute('INSERT OR IGNORE INTO ix_slots VALUES (?,?,?)',('demo-slot','2030-01-10T01:00:00+00:00','2030-01-10T01:30:00+00:00'))
    booking=service.request_booking(admin,'demo-booking',subject,'demo-slot')
    status=store.db.execute('SELECT status FROM ix_operations WHERE id=?',(booking['operation_id'],)).fetchone()[0]
    if status=='approval_pending':
        # Approval is a simulated test step, not the representative's real approval.
        service.decide(admin,booking['operation_id'],'approve')
        service.dispatch_mock(admin,booking['operation_id'])
    campaign=service.content.generate('phase4-demo-content',SAMPLE_BRIEF,start_date='2030-01-10')
    content=next(c for c in service.content.report(campaign)['contents'] if c['kind']=='sns')
    service.propose_sns(admin,'demo-sns-operation',content['id'],['asset_demo'])
    return service,admin,service.dashboard_data(admin)
