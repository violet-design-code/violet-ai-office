"""Private representative calendar. JST, explicit confirmation, durable mock-only notifications."""
import json,re
from datetime import datetime,date,time,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from .store import now
from .seminars import JST
from .portfolio import text
from .integrations.adapters import Principal
from .business_foundation import ai_allowed
from .content import encode,digest


def migrate(store):
    if store.db.execute('SELECT 1 FROM office_migrations WHERE version=21').fetchone():return
    def op():
        for sql in (Path(__file__).resolve().parent.parent/'migrations/021_schedule.sql').read_text().split(';'):
            if sql.strip():store.db.execute(sql)
        store.db.execute('INSERT INTO office_migrations VALUES(21,?)',(now().isoformat(),))
    store._transaction(op)


def stamp(value):
    d=datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(d,datetime) or d.tzinfo is None:raise ValueError('Timezone-aware datetime required')
    return d.astimezone(timezone.utc).isoformat()


def local_input(value):
    d=datetime.fromisoformat(value)
    if d.tzinfo is not None:raise ValueError('JST wall time expected')
    return d.replace(tzinfo=JST)


class Schedule:
    def __init__(self,o):self.o=o;self.store=o.store;self.db=o.db
    def access(self,p,b=None):
        try:self.o.portfolio.access(p,b,owner=True)
        except (PermissionError,ValueError):self.o.audit('authenticated','schedule_denied');raise
    def event(self,p,ref,event):self.db.execute('INSERT INTO sc_audit(owner,actor,ref,event,created_at) VALUES(?,?,?,?,?)',(p.identity,p.identity,ref,event,now().isoformat()))
    def row(self,p,identifier):
        self.access(p);r=self.db.execute('SELECT * FROM sc_events WHERE id=? AND owner=?',(identifier,p.identity)).fetchone()
        if not r:raise ValueError('本人の予定を選択してください。')
        self.access(p,r['business_id']);return r
    def assignment(self,p,b=None):
        self.access(p,b)
        return {'agent':'AI23','supervisor':'AI01','role':self.store.agents['AI23']['role'],'mode':'rule_based','business_id':b,'business_work_allowed':ai_allowed(self.store,'AI23',b,'work',strict=True) if b else None,'note':'個人予定は代表本人の確認操作。事業内の自動業務は既存の担当・権限がある場合のみ。設定は自動変更しません。'}
    def save(self,p,key,title,start,end,person='',location='',business=None,identifier=None,revision=0,checked=False):
        self.access(p,business)
        if checked is not True:raise ValueError('日時・内容を確認してチェックしてください。')
        title=text(title,300);person=text(person,150) if person else '';location=text(location,500) if location else '';key=text(key,80);a,z=stamp(start),stamp(end)
        if a>=z or datetime.fromisoformat(z)-datetime.fromisoformat(a)>timedelta(days=7):raise ValueError('終了日時は開始後、期間は7日以内で指定してください。')
        fingerprint=digest([title,a,z,person,location,business])
        def op():
            old=self.row(p,identifier) if identifier else self.db.execute('SELECT * FROM sc_events WHERE owner=? AND entry_key=?',(p.identity,key)).fetchone()
            if old and not identifier:
                if old['entry_digest']!=fingerprint:raise ValueError('同じ登録キーで異なる内容は保存できません。')
                return old['id']
            if old and (old['revision']!=revision or old['status']!='active'):raise ValueError('予定が変更されています。再表示してください。')
            # One representative cannot be double-booked across any of their businesses.
            if self.db.execute("SELECT 1 FROM sc_events WHERE owner=? AND status='active' AND start_at<? AND end_at>? AND id<>?",(p.identity,z,a,identifier or '')).fetchone():raise ValueError('他の予定と重複しています。日時を変更してください。')
            ref=identifier or uuid4().hex
            if old:self.db.execute('UPDATE sc_events SET business_id=?,title=?,person=?,location=?,start_at=?,end_at=?,revision=revision+1 WHERE id=? AND owner=?',(business,title,person,location,a,z,ref,p.identity))
            else:self.db.execute("INSERT INTO sc_events VALUES(?,?,?,?,?,?,?,?,'active',1,?,?,?)",(ref,p.identity,business,title,person,location,a,z,key,fingerprint,now().isoformat()))
            self.invalidate(p,[old['start_at'] if old else None,a],ref);self.event(p,ref,'updated' if old else 'created');self.record_work(p,ref,'updated' if old else 'created');return ref
        return self.store._transaction(op)
    def record_work(self,p,ref,kind):
        r=self.row(p,ref);a=self.assignment(p,r['business_id'])
        state='rule_based_manual_record' if not r['business_id'] or a['business_work_allowed'] else 'admin_review'
        self.db.execute('INSERT INTO sc_work VALUES(?,?,?,?,?,?)',(ref,r['revision'],a['agent'],kind,state,now().isoformat()))
    def invalidate(self,p,starts,ref):
        self.db.execute("UPDATE sc_notifications SET state='superseded' WHERE owner=? AND event_id=? AND state NOT IN ('mock_sent','failed')",(p.identity,ref))
        for value in starts:
            if value:self.db.execute("UPDATE sc_notifications SET state='superseded' WHERE owner=? AND kind='daily' AND target_day=? AND state NOT IN ('mock_sent','failed')",(p.identity,datetime.fromisoformat(value).astimezone(JST).date().isoformat()))
    def cancel(self,p,identifier,revision,checked):
        def op():
            r=self.row(p,identifier)
            if checked is not True or r['revision']!=revision:raise ValueError('現在の予定とキャンセル内容を確認してください。')
            if r['status']=='cancelled':return
            self.db.execute("UPDATE sc_events SET status='cancelled',revision=revision+1 WHERE id=? AND owner=?",(identifier,p.identity));self.invalidate(p,[r['start_at']],identifier);self.event(p,identifier,'cancelled_no_physical_delete');self.record_work(p,identifier,'cancelled')
        self.store._transaction(op)
    def listing(self,p,start,end,business=None):
        self.access(p,business);a,z=stamp(start),stamp(end)
        if not 0<datetime.fromisoformat(z).timestamp()-datetime.fromisoformat(a).timestamp()<=366*86400:raise ValueError('検索期間は1年以内にしてください。')
        return [dict(r) for r in self.db.execute("SELECT * FROM sc_events WHERE owner=? AND status='active' AND start_at<? AND end_at>? AND (? IS NULL OR business_id=?) ORDER BY start_at,id",(p.identity,z,a,business,business))]
    def free(self,p,day,start='09:00',end='18:00'):
        a=datetime.combine(date.fromisoformat(day),time.fromisoformat(start),JST);z=datetime.combine(a.date(),time.fromisoformat(end),JST)
        if a>=z:raise ValueError('空き時間の検索範囲を確認してください。')
        rows=self.listing(p,a,z);cursor=a;slots=[]
        for r in rows:
            begin=datetime.fromisoformat(r['start_at']).astimezone(JST);finish=datetime.fromisoformat(r['end_at']).astimezone(JST)
            if begin>cursor:slots.append([cursor.isoformat(),min(begin,z).isoformat()])
            cursor=max(cursor,finish)
        if cursor<z:slots.append([cursor.isoformat(),z.isoformat()])
        return slots
    def interpret(self,p,instruction,current=None):
        self.access(p);q=text(instruction,2000);current=(current or now()).astimezone(JST);today=current.date()
        if any(v in q for v in ('変更','キャンセル','削除')):return {'intent':'select_event','answer':'対象の予定を一覧から選択し、変更・キャンセル内容を確認してください。まだ変更していません。','mode':'rule_based'}
        if any(v in q for v in ('教えて','予定は','空いて','次の予定')):
            if '次の予定' in q:
                row=self.db.execute("SELECT * FROM sc_events WHERE owner=? AND status='active' AND start_at>=? ORDER BY start_at LIMIT 1",(p.identity,stamp(current))).fetchone();events=[dict(row)] if row else [];label='次の予定'
            else:
                target=today+timedelta(days=1) if '明日' in q else today
                if '来週' in q:target=today+timedelta(days=7-today.weekday())
                for i,name in enumerate('月火水木金土日'):
                    if name+'曜日' in q:target=today+timedelta(days=(i-today.weekday())%7)
                if '空いて' in q:
                    slots=self.free(p,target.isoformat());return {'intent':'free','answer':target.isoformat()+'の09:00〜18:00（検索範囲）の空き時間：'+('、'.join(datetime.fromisoformat(a).strftime('%H:%M')+'〜'+datetime.fromisoformat(z).strftime('%H:%M') for a,z in slots) or '空き時間はありません。'),'slots':slots,'mode':'rule_based'}
                a=datetime.combine(target,time.min,JST);events=self.listing(p,a,a+timedelta(days=7 if '来週' in q else 1));label='来週の予定' if '来週' in q else target.isoformat()+'の予定'
            return {'intent':'query','events':events,'answer':label+'：'+('、'.join(datetime.fromisoformat(r['start_at']).astimezone(JST).strftime('%m月%d日%H:%M')+' '+r['title'] for r in events) or '登録されていません。'),'mode':'rule_based'}
        # Deterministic candidates; never fabricate duration, year, person or missing location.
        d=re.search(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日',q);t=re.search(r'(午前|午後)?\s*(\d{1,2})時(?:(\d{1,2})分)?',q);candidate={};questions=[]
        day=today+timedelta(days=1) if '明日' in q else today if '今日' in q else None
        if d:
            year=int(d[1]) if d[1] else today.year;day=date(year,int(d[2]),int(d[3]))
            if not d[1]:questions.append('年が未指定です。表示した年でよいか確認してください。')
        if day and t:
            hour=int(t[2])
            if t[1]:
                if not 1<=hour<=12:raise ValueError('午前・午後の時刻は1〜12時で指定してください。')
                hour=hour%12+(12 if t[1]=='午後' else 0)
            candidate['start']=datetime.combine(day,time(hour,int(t[3] or 0)),JST).isoformat()
        else:questions.append('開始日と開始時刻を指定してください。')
        person=re.search(r'([^\s、。と]+?さん)と',q);place=re.search(r'([^\s、。]+?)で',q)
        candidate['person']=person[1] if person else '';candidate['location']=place[1] if place else '';candidate['title']='打ち合わせ' if '打ち合わせ' in q else '面談' if '面談' in q else 'セミナー' if 'セミナー' in q else ''
        # Strip the date/time prefix from simple Japanese phrases, leaving uncertain values editable.
        for k in ('person','location'):
            candidate[k]=re.sub(r'^.*(?:から|時|分)', '',candidate[k]).strip()
            if k=='location' and 'と' in candidate[k]:candidate[k]=candidate[k].rsplit('と',1)[-1]
        questions.append('終了日時（または所要時間）を指定してください。')
        if not candidate['title']:questions.append('予定の内容を指定してください。')
        return {'intent':'draft','candidate':candidate,'questions':questions,'answer':'まだ登録していません。候補の日時・相手・場所を確認し、不足項目を入力してください。','mode':'rule_based'}
    def settings(self,p,enabled,consent,checked):
        self.access(p)
        if any(type(v) is not bool for v in (enabled,consent,checked)) or not checked or enabled and not consent:raise ValueError('本人の通知同意と設定確認が必要です。')
        def op():
            self.db.execute("INSERT INTO sc_settings VALUES(?,?,?,'mock',?) ON CONFLICT(owner) DO UPDATE SET enabled=excluded.enabled,consent=excluded.consent,updated_at=excluded.updated_at",(p.identity,enabled,consent,now().isoformat()))
            if not enabled or not consent:self.db.execute("UPDATE sc_notifications SET state='consent_hold' WHERE owner=? AND state IN ('pending','retry_wait')",(p.identity,))
            self.event(p,'settings','mock_schedule_enabled' if enabled else 'schedule_stopped')
        self.store._transaction(op)
    def _schedule(self,p,current):
        settings=self.db.execute('SELECT * FROM sc_settings WHERE owner=?',(p.identity,)).fetchone()
        if not settings or not settings['enabled'] or not settings['consent']:return 0
        local=current.astimezone(JST);horizon=current+timedelta(days=32);rows=self.listing(p,current-timedelta(days=1),horizon);groups={};prepared=0
        for r in rows:
            a=datetime.fromisoformat(r['start_at']);day=a.astimezone(JST).date();last=(datetime.fromisoformat(r['end_at'])-timedelta(microseconds=1)).astimezone(JST).date()
            while day<=last:
                groups.setdefault(day,[]).append(r);day+=timedelta(days=1)
            due=a-timedelta(minutes=60)
            if current<=a and due<=horizon:
                prepared+=self._enqueue(p,'hour',due,[r],event=r['id'])
        for day,events in groups.items():
            due=datetime.combine(day-timedelta(days=1),time(21),JST)
            if day>local.date() or day==local.date() and local.hour<1:prepared+=self._enqueue(p,'daily',due,events,day=day.isoformat())
        return prepared
    def _enqueue(self,p,kind,due,events,event=None,day=None):
        fingerprint=digest([[r['id'],r['revision']] for r in events]);key=digest([p.identity,kind,event or day,events[0]['start_at'] if event else day]);identifier=uuid4().hex
        before=self.db.total_changes
        self.db.execute("INSERT OR IGNORE INTO sc_notifications(id,owner,event_id,kind,due_at,target_day,fingerprint,dedupe_key,state) VALUES(?,?,?,?,?,?,?,?,'pending')",(identifier,p.identity,event,kind,stamp(due),day,fingerprint,key))
        inserted=int(self.db.total_changes>before)
        self.db.execute("UPDATE sc_notifications SET fingerprint=?,state=CASE WHEN state IN ('superseded','consent_hold','recovery_hold') THEN 'pending' ELSE state END WHERE dedupe_key=? AND state IN ('pending','retry_wait','superseded','consent_hold','recovery_hold')",(fingerprint,key))
        return inserted
    def _payload(self,p,row):
        if row['kind']=='hour':
            e=self.row(p,row['event_id']);events=[dict(e)] if e['status']=='active' else []
        else:
            day=date.fromisoformat(row['target_day']);a=datetime.combine(day,time.min,JST);events=self.listing(p,a,a+timedelta(days=1))
        if not events or digest([[r['id'],r['revision']] for r in events])!=row['fingerprint']:return None
        return '【VIOLET AI OFFICE】\n'+('あと1時間で予定があります。' if row['kind']=='hour' else '明日の予定をお知らせします。')+'\n'+'\n'.join(datetime.fromisoformat(r['start_at']).astimezone(JST).strftime('%H:%M')+('（継続中）' if row['kind']=='daily' and datetime.fromisoformat(r['start_at']).astimezone(JST).date().isoformat()<row['target_day'] else '')+' '+r['title']+(' / '+r['person'] if r['person'] else '')+(' / 場所：'+r['location'] if r['location'] else '') for r in events)
    def tick(self,current=None,fail_mock=False,owner=None):
        """No external provider. Atomic claim/result makes concurrent mock workers idempotent."""
        current=datetime.fromisoformat(stamp(current or now()));prepared=processed=0
        def op():
            nonlocal prepared,processed
            owners=self.db.execute("SELECT s.owner FROM sc_settings s JOIN office_users u ON u.username=s.owner WHERE u.role='representative' AND s.enabled=1 AND s.consent=1 AND (? IS NULL OR s.owner=?)",(owner,owner)).fetchall()
            for r in owners:
                p=Principal('representative',r['owner']);prepared+=self._schedule(p,current)
                for n in self.db.execute("SELECT * FROM sc_notifications WHERE owner=? AND state IN ('pending','retry_wait') AND due_at<=? AND (next_at IS NULL OR next_at<=?) ORDER BY due_at LIMIT 100",(p.identity,stamp(current),stamp(current))).fetchall():
                    body=self._payload(p,n)
                    if not body:self.db.execute("UPDATE sc_notifications SET state='superseded' WHERE id=?",(n['id'],));continue
                    # Suppress late jobs instead of falsely saying 'one hour' or 'tomorrow'.
                    if current-datetime.fromisoformat(n['due_at'])>timedelta(minutes=5):self.db.execute("UPDATE sc_notifications SET state='missed' WHERE id=?",(n['id'],));continue
                    attempts=n['attempts']+1;state='failed' if fail_mock and attempts>=3 else 'retry_wait' if fail_mock else 'mock_sent'
                    self.db.execute('UPDATE sc_notifications SET state=?,attempts=?,next_at=?,sent_at=?,error_code=? WHERE id=?',(state,attempts,stamp(current+timedelta(seconds=30)) if state=='retry_wait' else None,stamp(current) if state=='mock_sent' else None,'mock_failure' if fail_mock else None,n['id']));processed+=1
            self.db.execute('INSERT INTO sc_ticks(executed_at,prepared,processed) VALUES(?,?,?)',(stamp(current),prepared,processed))
            return {'prepared':prepared,'processed':processed,'provider':'mock','external_sent':False}
        return self.store._transaction(op)
    def notifications(self,p):
        self.access(p);return [dict(r) for r in self.db.execute('SELECT * FROM sc_notifications WHERE owner=? ORDER BY due_at DESC LIMIT 100',(p.identity,))]
