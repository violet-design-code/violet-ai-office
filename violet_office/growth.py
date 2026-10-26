"""Bounded local business planning and reports. No LLM, network or publication.

Machine handoffs use growth_edges (artifact availability). Store dependencies
continue to mean passed independent review and are not bypassed or auto-approved.
"""
import calendar
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from zoneinfo import ZoneInfo

from .store import now
from .content import ContentOffice,CHECKS,encode,digest,safe_input

JST=ZoneInfo('Asia/Tokyo')
METRICS={'line_registrations':'LINE登録','inquiries':'問い合わせ','referrals':'紹介','appointments':'面談予約',
         'contracts':'契約','sns_posts':'SNS投稿','sns_reactions':'SNS反応','sns_visits':'SNS流入','appointment_requests':'面談希望'}
GOALS=tuple(list(METRICS)[:6])+('ai_budget_microusd','owner_minutes')
CADENCES={'daily':'日次','weekly':'週次','monthly':'月次'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS growth_goals (
 month TEXT PRIMARY KEY, values_json TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS growth_audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, ref TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS growth_measurements (
 id TEXT PRIMARY KEY, entry_key TEXT UNIQUE NOT NULL, metric TEXT NOT NULL, day TEXT NOT NULL,
 value INTEGER NOT NULL CHECK(value>=0), quality TEXT NOT NULL, mode TEXT NOT NULL,
 source TEXT NOT NULL, evidence TEXT NOT NULL, revision INTEGER NOT NULL,
 UNIQUE(metric,day,quality,mode,revision));
CREATE TABLE IF NOT EXISTS growth_time (
 id TEXT PRIMARY KEY, entry_key TEXT UNIQUE NOT NULL, day TEXT NOT NULL, minutes INTEGER NOT NULL,
 quality TEXT NOT NULL, mode TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS growth_schedules (
 id TEXT PRIMARY KEY, schedule_key TEXT UNIQUE NOT NULL, cadence TEXT NOT NULL, period_mode TEXT NOT NULL,
 anchor TEXT NOT NULL, next_due TEXT NOT NULL, prepare_draft INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'paused', created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS growth_cadence ON growth_schedules(cadence);
CREATE TABLE IF NOT EXISTS growth_schedule_versions (schedule_id TEXT PRIMARY KEY REFERENCES growth_schedules(id), revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS growth_runs (
 id TEXT PRIMARY KEY, run_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 schedule_id TEXT REFERENCES growth_schedules(id), cadence TEXT NOT NULL,
 period_key TEXT NOT NULL, context TEXT NOT NULL, status TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, lease TEXT, deadline TEXT, error_code TEXT,
 report TEXT, decision TEXT NOT NULL DEFAULT 'approval_pending', created_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS growth_work (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES growth_runs(id), step TEXT NOT NULL,
 agent TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), artifact_id TEXT REFERENCES artifacts(id),
 state TEXT NOT NULL, due_at TEXT NOT NULL, output_digest TEXT NOT NULL, UNIQUE(run_id,step));
CREATE TABLE IF NOT EXISTS growth_edges (
 work_id TEXT NOT NULL REFERENCES growth_work(id), prerequisite TEXT NOT NULL REFERENCES growth_work(id),
 PRIMARY KEY(work_id,prerequisite), CHECK(work_id!=prerequisite));
CREATE TABLE IF NOT EXISTS growth_attempts (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES growth_runs(id), attempt INTEGER NOT NULL,
 outcome TEXT NOT NULL, code TEXT, started_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS growth_proposals (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), run_id TEXT NOT NULL REFERENCES growth_runs(id),
 priority INTEGER NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS growth_decisions (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES growth_runs(id), decision TEXT NOT NULL,
 actor TEXT NOT NULL, report_digest TEXT NOT NULL, created_at TEXT NOT NULL);
'''


class LocalRunError(ValueError):
    pass


class PausedRun(LocalRunError):
    pass


class TimedOutRun(LocalRunError):
    pass


def instant(value):
    result=datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(result,datetime) or result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def month_key(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',value):
        raise ValueError('Invalid month')
    date.fromisoformat(value+'-01')
    return value


def usd(value):
    try:
        n=Decimal(str(value))
        if not n.is_finite() or not 0<=n<=1000000 or n*1000000!=(n*1000000).to_integral_value():
            raise ValueError('Invalid USD')
        return int(n*1000000)
    except InvalidOperation:
        raise ValueError('Invalid USD')


def window(cadence,at,mode='current'):
    if cadence not in CADENCES or mode not in ('current','previous'):
        raise ValueError('Invalid cadence')
    local=instant(at).astimezone(JST)
    anchor=local.date()
    if mode=='previous':
        anchor=anchor-timedelta(days=1 if cadence=='daily' else 7) if cadence!='monthly' else anchor.replace(day=1)-timedelta(days=1)
    if cadence=='daily':
        start=anchor
        end=start+timedelta(days=1)
        key=start.isoformat()
    elif cadence=='weekly':
        start=anchor-timedelta(days=anchor.weekday())
        end=start+timedelta(days=7)
        key=f'{start.isocalendar().year}-W{start.isocalendar().week:02}'
    else:
        start=anchor.replace(day=1)
        end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
        key=start.strftime('%Y-%m')
    cutoff=min(end,local.date()+timedelta(days=1))
    return {'cadence':cadence,'period_key':key,'start':start.isoformat(),'end_exclusive':end.isoformat(),
            'coverage_end_exclusive':cutoff.isoformat(),'as_of':local.isoformat(),'mode':mode}


def following(cadence,anchor,at):
    anchor=instant(anchor).astimezone(JST)
    at=instant(at).astimezone(JST)
    candidate=at.replace(hour=anchor.hour,minute=anchor.minute,second=anchor.second,microsecond=0)
    if cadence=='weekly':
        candidate+=timedelta(days=anchor.weekday()-at.weekday())
    elif cadence=='monthly':
        candidate=candidate.replace(day=min(anchor.day,calendar.monthrange(at.year,at.month)[1]))
    if candidate<=at:
        if cadence=='daily':
            candidate+=timedelta(days=1)
        elif cadence=='weekly':
            candidate+=timedelta(days=7)
        else:
            year,month=(candidate.year+1,1) if candidate.month==12 else (candidate.year,candidate.month+1)
            candidate=candidate.replace(year=year,month=month,day=min(anchor.day,calendar.monthrange(year,month)[1]))
    return candidate.astimezone(timezone.utc).isoformat()


class Growth:
    max_steps=6
    max_attempts=3
    timeout_seconds=10

    def __init__(self,store):
        self.store=store
        self.db=store.db
        self.content=ContentOffice(store)
        self.db.executescript(SCHEMA)
        self._clock=time.monotonic

    def audit(self,actor,action,ref,detail=None):
        self.db.execute('INSERT INTO growth_audit(actor,action,ref,detail,created_at) VALUES (?,?,?,?,?)',
                        (actor,action,ref,encode(detail or {}),now().isoformat()))

    def goals(self,month):
        month_key(month)
        row=self.db.execute('SELECT * FROM growth_goals WHERE month=?',(month,)).fetchone()
        return {'month':month,'values':json.loads(row['values_json']) if row else {k:None for k in GOALS},'revision':row['revision'] if row else 0}

    def set_goals(self,month,values,actor):
        month_key(month)
        if set(values)!=set(GOALS) or not actor:
            raise ValueError('All goal fields must be explicit, nullable values')
        for key,value in values.items():
            maximum=calendar.monthrange(int(month[:4]),int(month[5:]))[1]*1440 if key=='owner_minutes' else 1000000000000 if key=='ai_budget_microusd' else 1000000
            if value is not None and (type(value) is not int or not 0<=value<=maximum):
                raise ValueError('Invalid goal')
        def op():
            old=self.goals(month)
            self.db.execute('INSERT INTO growth_goals VALUES (?,?,?,?) ON CONFLICT(month) DO UPDATE SET values_json=excluded.values_json,revision=excluded.revision,updated_at=excluded.updated_at',
                            (month,encode(values),old['revision']+1,now().isoformat()))
            if values['ai_budget_microusd'] is not None:
                if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='office_budgets'").fetchone():
                    raise ValueError('Office accounting initialization required')
                self.db.execute('INSERT INTO office_budgets VALUES (?,?) ON CONFLICT(month) DO UPDATE SET limit_microusd=excluded.limit_microusd',(month,values['ai_budget_microusd']))
            self.audit(actor,'goals_set',month,{'before':old,'after':values})
        self.store._transaction(op)

    def record_metric(self,key,metric,day,value,quality,mode,source,evidence,actor,replace_id=None):
        day=date.fromisoformat(day).isoformat()
        if quality=='measured' and mode=='manual' and date.fromisoformat(day)>now().astimezone(JST).date():
            raise ValueError('Future data cannot be measured')
        if metric not in METRICS or quality not in ('measured','estimated') or mode not in ('manual','mock') or type(value) is not int or not 0<=value<=1000000:
            raise ValueError('Invalid measurement')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',source):
            raise ValueError('Invalid source reference')
        safe_input(evidence)
        def op():
            old=self.db.execute('SELECT * FROM growth_measurements WHERE entry_key=?',(key,)).fetchone()
            values=(metric,day,value,quality,mode,source,evidence)
            if old:
                if tuple(old[k] for k in ('metric','day','value','quality','mode','source','evidence'))!=values:
                    raise ValueError('Measurement key conflict')
                return old['id']
            latest=self.db.execute('SELECT * FROM growth_measurements WHERE metric=? AND day=? AND quality=? AND mode=? ORDER BY revision DESC LIMIT 1',(metric,day,quality,mode)).fetchone()
            if latest and (not replace_id or latest['id']!=replace_id):
                raise ValueError('One authoritative aggregate per metric/day; explicit latest correction required')
            if replace_id and not latest:
                raise ValueError('Unknown correction target')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO growth_measurements VALUES (?,?,?,?,?,?,?,?,?,?)',(identifier,key,*values,(latest['revision']+1) if latest else 1))
            self.audit(actor,'measurement_recorded',identifier,{'metric':metric,'quality':quality,'mode':mode,'replaces':replace_id})
            return identifier
        return self.store._transaction(op)

    def record_time(self,key,day,minutes,quality,mode,evidence,actor):
        day=date.fromisoformat(day).isoformat()
        if quality=='measured' and mode=='manual' and date.fromisoformat(day)>now().astimezone(JST).date():
            raise ValueError('Future time cannot be measured')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key) or type(minutes) is not int or not 0<=minutes<=1440 or quality not in ('measured','estimated') or mode not in ('manual','mock'):
            raise ValueError('Invalid time entry')
        safe_input(evidence)
        def op():
            old=self.db.execute('SELECT * FROM growth_time WHERE entry_key=?',(key,)).fetchone()
            if old:
                if tuple(old[k] for k in ('day','minutes','quality','mode','evidence'))!=(day,minutes,quality,mode,evidence):
                    raise ValueError('Time entry conflict')
                return old['id']
            # Daily totals, not overlapping time intervals.
            existing=self.db.execute('SELECT COALESCE(SUM(minutes),0) FROM growth_time WHERE day=? AND quality=? AND mode=?',(day,quality,mode)).fetchone()[0]
            if existing+minutes>1440:
                raise ValueError('Daily time exceeds 24 hours')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO growth_time VALUES (?,?,?,?,?,?,?)',(identifier,key,day,minutes,quality,mode,evidence))
            self.audit(actor,'owner_time_recorded',identifier,{'quality':quality,'mode':mode})
            return identifier
        return self.store._transaction(op)

    def metrics(self,start,end):
        start_day,end_day=date.fromisoformat(start),date.fromisoformat(end)
        if not 0<(end_day-start_day).days<=366:
            raise ValueError('Invalid measurement window')
        days=(end_day-start_day).days
        result={}
        for metric,title in METRICS.items():
            rows=self.db.execute('''SELECT m.* FROM growth_measurements m WHERE metric=? AND day>=? AND day<?
                AND revision=(SELECT MAX(n.revision) FROM growth_measurements n WHERE n.metric=m.metric AND n.day=m.day AND n.quality=m.quality AND n.mode=m.mode)''',(metric,start,end)).fetchall()
            measured=[r for r in rows if r['mode']=='manual' and r['quality']=='measured']
            estimates=[r for r in rows if r['mode']=='manual' and r['quality']=='estimated']
            value=sum(r['value'] for r in measured) if measured else None
            result[metric]={'title':title,'value':value,'estimated':sum(r['value'] for r in estimates) if estimates else None,
                'coverage_days':len(measured),'expected_days':days,'complete':len(measured)==days,
                'status':'手動実測申告' if len(measured)==days else '部分計測' if measured else '未計測',
                'source_ids':[r['id'] for r in measured], 'source_mode':'manual','mock_excluded':True}
        return result

    def snapshot(self,cadence,at,mode):
        period=window(cadence,at,mode)
        start,end=period['start'],period['coverage_end_exclusive']
        kpis=self.metrics(start,end)
        ref=date.fromisoformat(end)-timedelta(days=1)
        month=ref.strftime('%Y-%m')
        goals=self.goals(month)
        monthly=self.metrics(month+'-01',end)
        progress={}
        for key in list(METRICS)[:6]:
            target=goals['values'][key]
            measured=monthly[key]
            valid=target is not None and measured['complete']
            progress[key]={'goal':target,'actual':measured['value'],'status':measured['status'],
                'gap':target-measured['value'] if valid else None,
                'achievement_percent':round(measured['value']/target*100,2) if valid and target>0 else None}
        conversions={}
        for name,numerator,denominator in [('問い合わせ→面談予約','appointments','inquiries'),('面談予約→契約','contracts','appointments'),('SNS流入→LINE登録','line_registrations','sns_visits')]:
            n,d=kpis[numerator],kpis[denominator]
            valid=n['complete'] and d['complete'] and d['value']>0
            conversions[name]={'percent':None,'period_count_ratio_percent':round(n['value']/d['value']*100,2) if valid else None,
                'note':'参考の期間件数比。顧客単位の転換率は未計測で、因果関係を示さない。' if valid else '分母/分子未計測・部分計測・分母0のため未計測'}
        costs=[]
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='office_costs'").fetchone():
            costs=self.db.execute("SELECT * FROM office_costs WHERE incurred_date>=? AND incurred_date<? AND mode='manual'",(start,end)).fetchall()
        expense={kind:sum(r['amount'] for r in costs if r['kind']==kind) if any(r['kind']==kind for r in costs) else None for kind in ('confirmed','estimated')}
        times=self.db.execute("SELECT * FROM growth_time WHERE day>=? AND day<? AND mode='manual'",(month+'-01',end)).fetchall()
        owner={quality:sum(r['minutes'] for r in times if r['quality']==quality) if any(r['quality']==quality for r in times) else None for quality in ('measured','estimated')}
        before=datetime.combine(date.fromisoformat(start),datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
        after=datetime.combine(date.fromisoformat(end),datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
        local={'completed_task_ids':[r[0] for r in self.db.execute("SELECT DISTINCT e.task_id FROM events e JOIN tasks t ON t.id=e.task_id WHERE t.status='completed' AND t.action='internal' AND e.event='review:pass' AND e.created_at>=? AND e.created_at<? LIMIT 1000",(before,after))],
            'running':[r[0] for r in self.db.execute("SELECT id FROM tasks WHERE status='running' LIMIT 1000")],
            'approval_pending':sum(self.db.execute(query).fetchone()[0] for query in ("SELECT COUNT(*) FROM content_versions WHERE status='approval_pending'","SELECT COUNT(*) FROM tasks WHERE status='approval_pending'","SELECT COUNT(*) FROM ix_operations WHERE status='approval_pending'","SELECT COUNT(*) FROM growth_runs WHERE status='succeeded_local' AND decision='approval_pending'")),
            'errors':[r[0] for r in self.db.execute("SELECT DISTINCT task_id FROM events WHERE event LIKE 'run_failed:%' AND created_at>=? AND created_at<? LIMIT 1000",(before,after))],
            'local_report_failures':self.db.execute("SELECT COUNT(*) FROM growth_attempts WHERE outcome='failed' AND finished_at>=? AND finished_at<?",(before,after)).fetchone()[0],
            'mock_api_failures':self.db.execute("SELECT COUNT(*) FROM ix_attempts WHERE outcome NOT IN ('success','running') AND created_at>=? AND created_at<?",(before,after)).fetchone()[0]}
        local['completed_task_count']=self.db.execute("SELECT COUNT(DISTINCT e.task_id) FROM events e JOIN tasks t ON t.id=e.task_id WHERE t.status='completed' AND t.action='internal' AND e.event='review:pass' AND e.created_at>=? AND e.created_at<?",(before,after)).fetchone()[0]
        local['running_task_count']=self.db.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
        local['error_task_count']=self.db.execute("SELECT COUNT(DISTINCT task_id) FROM events WHERE event LIKE 'run_failed:%' AND created_at>=? AND created_at<?",(before,after)).fetchone()[0]
        local['reference_ids_limit']=1000
        return {'period':period,'kpis':kpis,'monthly_goals':goals,'monthly_progress':progress,'conversions':conversions,
            'cost_microusd':expense,'cost_source':'手動記帳のみ。API請求自動取得は未接続、モックを除外。',
            'owner_month_minutes':owner,'local_tasks':local,
            'attribution':'施策別の流入・顧客コホートが未接続のため、成功施策/因果関係は未評価。'}

    def plan(self,context):
        goals=context['monthly_goals']['values']
        progress=context['monthly_progress']
        items=[{'agent':'AI27','priority':1,'work':'実測データの根拠と施策別の計測方法を整理','reason':'未計測/部分計測を0と扱わない','approval_required':True}]
        mappings={'sns_posts':('AI08','SNS投稿計画と原稿案を準備'),'line_registrations':('AI21','LINE登録・同意説明と配信原稿を見直す'),
            'referrals':('AI16','任意に共有できる紹介文案を改善'),'inquiries':('AI17','よくある質問への回答案を整理'),
            'appointments':('AI18','希望者だけの面談予約導線を改善'),'contracts':('AI19','確認済み資料に基づく説明内容を点検')}
        for metric,(agent,work) in mappings.items():
            if goals[metric] is not None and goals[metric]>0:
                gap=progress[metric]['gap']
                items.append({'agent':agent,'priority':2 if gap is None else 1 if gap>0 else 3,'work':work,
                    'reason':'目標設定あり。実績未確定のため効果は未評価。' if gap is None else f'手動実測申告に基づく目標との差 {gap}。因果関係は未確定。','approval_required':True})
        capacity=goals['owner_minutes']
        used=context['owner_month_minutes']['measured']
        capacity_reached=capacity==0 or (capacity is not None and used is not None and used>=capacity)
        next_start=date.fromisoformat(context['period']['end_exclusive'])
        cadence=context['period']['cadence']
        next_end=next_start+timedelta(days=1 if cadence=='daily' else 7) if cadence!='monthly' else (next_start.replace(day=28)+timedelta(days=4)).replace(day=1)
        return {'next_period':{'start':next_start.isoformat(),'end_exclusive':next_end.isoformat()},'items':items[:7],'targets_confirmed_by_system':False,'owner_capacity_reached':capacity_reached,
            'budget_goal_microusd':goals['ai_budget_microusd'],'paid_api_allowed':False,
            'hypotheses':['導線・原稿・反応を計測して比較する。施策の効果を断定しない。'],
            'experiment':'代表承認後に対象・期間・指標を定めて比較し、選定偏りや季節要因を記録。実際の連絡・公開は別の承認と接続が必要。',
            'next_priority':'代表の稼働上限に到達したため新規の対外業務案実行を止め、負担と未完了を確認。' if capacity_reached else '未設定目標・未計測データ・承認待ちを先に確認。'}

    def quality(self,context,plan,draft):
        for value in context['kpis'].values():
            if value['mock_excluded'] is not True or not 0<=value['coverage_days']<=value['expected_days'] or value['complete']!=(value['coverage_days']==value['expected_days']):
                raise LocalRunError('Invalid measurement labels')
            if len(value['source_ids'])!=value['coverage_days'] or value['value'] is not None and (type(value['value']) is not int or value['value']<0):
                raise LocalRunError('Invalid measurement evidence')
        if plan['paid_api_allowed'] is not False or len(plan['items'])>7:
            raise LocalRunError('Invalid local plan')
        for item in plan['items']:
            if item['agent'] not in self.store.agents or item['approval_required'] is not True:
                raise LocalRunError('Invalid plan permissions')
        missing=CHECKS[:]
        blockers=[]
        if draft:
            row=self.content._latest(draft)
            audit=self.db.execute('SELECT * FROM content_audits WHERE content_id=?',(draft,)).fetchone()
            if not audit or audit['digest']!=row['digest']:
                raise LocalRunError('Draft audit mismatch')
            checks=json.loads(audit['checks'])
            missing=[key for key in CHECKS if not checks.get(key)]
            blockers=json.loads(audit['blockers'])
        return {'method':'実測ラベル・根拠参照・権限・監査ハッシュのローカル検査',
            'facts':'外部事実の照合なし','human_review_required':True,'legal_compliance':'保証しない',
            'actual_api_calls':0,'draft_id':draft,'report_labels_checked':True,'human_checks_missing':missing,'content_blockers':blockers}

    def draft_allowed(self,context):
        month=now().astimezone(JST).strftime('%Y-%m')
        cap=self.goals(month)['values']['owner_minutes']
        logged=self.db.execute("SELECT COALESCE(SUM(minutes),0) FROM growth_time WHERE substr(day,1,7)=? AND mode='manual' AND quality='measured'",(month,)).fetchone()[0]
        return cap is None or logged<cap

    def create_schedule(self,key,cadence,first_due,mode,prepare_draft,actor):
        if cadence not in CADENCES or mode not in ('current','previous') or type(prepare_draft) is not bool or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key):
            raise ValueError('Invalid schedule')
        due=instant(first_due).isoformat()
        def op():
            old=self.db.execute('SELECT * FROM growth_schedules WHERE schedule_key=?',(key,)).fetchone()
            if old:
                if (old['cadence'],old['anchor'],old['period_mode'],bool(old['prepare_draft']))!=(cadence,due,mode,prepare_draft):
                    raise ValueError('Schedule key conflict')
                return old['id']
            if self.db.execute('SELECT 1 FROM growth_schedules WHERE cadence=?',(cadence,)).fetchone():
                raise ValueError('Cadence already configured')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO growth_schedules(id,schedule_key,cadence,period_mode,anchor,next_due,prepare_draft,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (identifier,key,cadence,mode,due,due,int(prepare_draft),now().isoformat()))
            self.db.execute('INSERT INTO growth_schedule_versions VALUES (?,1)',(identifier,))
            self.audit(actor,'schedule_created_paused',identifier)
            return identifier
        return self.store._transaction(op)

    def schedule_state(self,identifier,enabled,actor):
        if type(enabled) is not bool:
            raise ValueError('Explicit boolean required')
        def op():
            if self.db.execute('UPDATE growth_schedules SET enabled=?,state=? WHERE id=?',(int(enabled),'enabled_local' if enabled else 'paused',identifier)).rowcount!=1:
                raise ValueError('Unknown schedule')
            self.audit(actor,'schedule_enabled_local' if enabled else 'schedule_paused',identifier)
        self.store._transaction(op)

    def edit_schedule(self,identifier,first_due,mode,prepare_draft,actor):
        due=instant(first_due).isoformat()
        if mode not in ('current','previous') or type(prepare_draft) is not bool:
            raise ValueError('Invalid schedule settings')
        def op():
            row=self.db.execute('SELECT * FROM growth_schedules WHERE id=?',(identifier,)).fetchone()
            if not row or row['enabled']:
                raise ValueError('Pause schedule before editing')
            if (row['anchor'],row['period_mode'],bool(row['prepare_draft']))==(due,mode,prepare_draft):
                return
            self.db.execute("UPDATE growth_schedules SET anchor=?,next_due=?,period_mode=?,prepare_draft=?,state='paused' WHERE id=?",(due,due,mode,int(prepare_draft),identifier))
            self.db.execute('INSERT INTO growth_schedule_versions VALUES (?,2) ON CONFLICT(schedule_id) DO UPDATE SET revision=revision+1',(identifier,))
            self.audit(actor,'schedule_reconfigured',identifier,{'first_due':due,'period_mode':mode,'prepare_draft':prepare_draft})
        self.store._transaction(op)

    def _lease(self,run,lease):
        row=self.db.execute('SELECT * FROM growth_runs WHERE id=?',(run,)).fetchone()
        if not row or row['status']!='running' or row['lease']!=lease:
            raise LocalRunError('Lease unavailable')
        if row['deadline']<=now().isoformat():
            raise TimedOutRun('Local deadline reached')
        if row['schedule_id']:
            active=self.db.execute('SELECT s.enabled,COALESCE(v.revision,1) AS revision FROM growth_schedules s LEFT JOIN growth_schedule_versions v ON v.schedule_id=s.id WHERE s.id=?',(row['schedule_id'],)).fetchone()
            if not active or not active['enabled'] or active['revision']!=json.loads(row['context']).get('schedule_revision',1):
                raise PausedRun('Schedule paused')
        return row

    def _work(self,run,lease,step,agent,output,prerequisites,due):
        def op():
            self._lease(run,lease)
            old=self.db.execute('SELECT * FROM growth_work WHERE run_id=? AND step=?',(run,step)).fetchone()
            if old and old['state']=='succeeded_local':
                cached=self.db.execute('SELECT content FROM artifacts WHERE id=?',(old['artifact_id'],)).fetchone()
                if not cached or digest(json.loads(cached[0]))!=old['output_digest']:
                    raise LocalRunError('Cached artifact changed')
                return dict(old)
            for prerequisite in prerequisites:
                row=self.db.execute('SELECT * FROM growth_work WHERE id=? AND run_id=?',(prerequisite,run)).fetchone()
                if not row or row['state']!='succeeded_local' or not row['artifact_id']:
                    raise LocalRunError('Missing machine handoff')
            task=self.store.create_task(f'growth:{run}:{step}',agent,{'run_id':run,'step':step,'mode':'local_rules_v1'},action='sns_post' if agent=='AI09' else 'customer_contact' if agent in ('AI16','AI18') else 'management_decision' if agent=='AI01' else 'internal',max_attempts=3,timeout_seconds=10)
            execution=self.store.claim(task,0)
            self._lease(run,lease)
            from .learning import Learning,task_kind
            Learning(self.store,initialize=False).runtime(agent,task_kind('internal',agent),task)
            artifact=self.store.finish(execution,encode(output))
            identifier=uuid4().hex
            self.db.execute('INSERT INTO growth_work VALUES (?,?,?,?,?,?,?,?,?)',(identifier,run,step,agent,task,artifact,'succeeded_local',due,digest(output)))
            self.db.executemany('INSERT INTO growth_edges VALUES (?,?)',[(identifier,p) for p in prerequisites])
            return dict(self.db.execute('SELECT * FROM growth_work WHERE id=?',(identifier,)).fetchone())
        return self.store._transaction(op)

    def run(self,key,cadence,at=None,mode='current',prepare_draft=False,actor='local_operator',schedule_id=None,intent='report',instruction=None,schedule_revision=None):
        at=instant(at or now())
        if intent not in ('report','planning','referral','booking_improvement','goal_review','cost_review'):
            raise ValueError('Unknown local intent')
        if instruction is not None:
            safe_input(instruction)
        if not re.fullmatch(r'[A-Za-z0-9_:-]{1,180}',key) or type(prepare_draft) is not bool:
            raise ValueError('Invalid run key')
        period=window(cadence,at,mode)
        fingerprint=digest([cadence,period,prepare_draft,schedule_id])
        started=self._clock()
        def acquire():
            if schedule_id:
                config=self.db.execute('SELECT s.*,COALESCE(v.revision,1) AS revision FROM growth_schedules s LEFT JOIN growth_schedule_versions v ON v.schedule_id=s.id WHERE s.id=?',(schedule_id,)).fetchone()
                if not config or not config['enabled'] or config['revision']!=schedule_revision:
                    raise PausedRun('Schedule changed before acquisition')
            row=self.db.execute('SELECT * FROM growth_runs WHERE run_key=?',(key,)).fetchone()
            if row:
                # Canonical period keys allow scheduler ticks to drift within one period.
                if row['cadence']!=cadence or row['period_key']!=period['period_key'] or json.loads(row['context'])['prepare_draft']!=prepare_draft or row['schedule_id']!=schedule_id or json.loads(row['context']).get('intent','report')!=intent or json.loads(row['context']).get('instruction')!=instruction or json.loads(row['context'])['period']['mode']!=mode:
                    raise ValueError('Run key conflict')
                if row['status']=='succeeded_local':
                    return row['id'],None
                if row['status']=='running':
                    if row['deadline']>now().isoformat():
                        return row['id'],None
                    self.db.execute("UPDATE growth_runs SET status='paused',error_code='lease_expired',lease=NULL WHERE id=?",(row['id'],))
                    self.db.execute("UPDATE growth_attempts SET outcome='interrupted',code='lease_expired',finished_at=? WHERE run_id=? AND outcome='running'",(now().isoformat(),row['id']))
                    if schedule_id:
                        self.db.execute("UPDATE growth_schedules SET enabled=0,state='auto_paused' WHERE id=?",(schedule_id,))
                    return row['id'],None
                if row['attempts']>=self.max_attempts:
                    if schedule_id:
                        self.db.execute("UPDATE growth_schedules SET enabled=0,state='attempt_limit' WHERE id=?",(schedule_id,))
                    self.audit(actor,'attempt_limit_reached',row['id'])
                    return row['id'],None
                identifier=row['id']
            else:
                context=self.snapshot(cadence,at,mode)
                context['prepare_draft']=prepare_draft
                context['schedule_revision']=schedule_revision
                context['intent']=intent
                context['instruction']=instruction
                identifier=uuid4().hex
                self.db.execute('INSERT INTO growth_runs(id,run_key,fingerprint,schedule_id,cadence,period_key,context,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (identifier,key,fingerprint,schedule_id,cadence,period['period_key'],encode(context),'pending',now().isoformat()))
            lease=uuid4().hex
            self.db.execute("UPDATE growth_runs SET status='running',attempts=attempts+1,lease=?,deadline=?,error_code=NULL WHERE id=?",(lease,(now()+timedelta(seconds=self.timeout_seconds)).isoformat(),identifier))
            attempt=self.db.execute('SELECT attempts FROM growth_runs WHERE id=?',(identifier,)).fetchone()[0]
            self.db.execute('INSERT INTO growth_attempts VALUES (?,?,?,?,?,?,?)',(uuid4().hex,identifier,attempt,'running',None,now().isoformat(),None))
            self.audit(actor,'local_run_started',identifier,{'mode':'local_rules_v1','actual_api_calls':0})
            return identifier,lease
        identifier,lease=self.store._transaction(acquire)
        if lease is None:
            return identifier
        try:
            context=json.loads(self.db.execute('SELECT context FROM growth_runs WHERE id=?',(identifier,)).fetchone()[0])
            due=datetime.combine(date.fromisoformat(context['period']['end_exclusive']),datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
            steps=[]
            def work(name,agent,output):
                if len(steps)>=self.max_steps or self._clock()-started>self.timeout_seconds:
                    raise TimedOutRun('Local work limit')
                item=self._work(identifier,lease,name,agent,output,[steps[-1]['id']] if steps else [],due)
                steps.append(item)
                return item
            work('analysis','AI27',context)
            plan=self.plan(context)
            work('plan','AI01',plan)
            draft=None
            if intent=='referral':
                referral={'紹介依頼案':'関心がありそうな成人の知人へ、ご自身が希望する場合だけ案内を共有できます。相手の連絡先をこちらへ渡す必要はありません。',
                    '第三者向け案内':'バイオレットの活動やベネシードディーラーについて、知りたい場合だけご案内をご確認ください。登録・面談は任意です。条件は正規の公式資料確認後に説明します。',
                    'status':'下書き。公式規約・根拠・法令を人が確認するまで発信禁止。'}
                from .learning import Learning,apply_style
                learning=Learning(self.store,initialize=False).runtime('AI16','referral','growth:'+identifier+':referral')
                for field in ('紹介依頼案','第三者向け案内'):
                    rendered=apply_style({'body':referral[field],'cta':''},learning['styles'])
                    referral[field]=rendered['body']+rendered['cta']
                item=work('referral','AI16',referral)
                def make_referral():
                    self._lease(identifier,lease)
                    existing=self.db.execute('SELECT id FROM campaigns WHERE campaign_key=?',('growth-'+identifier,)).fetchone()
                    if existing:
                        return self.db.execute('SELECT id FROM content_versions WHERE campaign_id=? ORDER BY version DESC LIMIT 1',(existing[0],)).fetchone()[0]
                    campaign=uuid4().hex
                    self.db.execute('INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?)',(campaign,'growth-'+identifier,digest(referral),'ローカル紹介文案',1,'紹介案内',context['period']['start'],'offline_referral_template_v1',now().isoformat()))
                    content=self.content._insert(campaign,'referral','referral',referral,task=item['task_id'])
                    self.content.audit(content)
                    return content
                draft=self.store._transaction(make_referral)
            elif intent=='booking_improvement':
                work('booking_proposal','AI18',{'hypothesis':'希望者だけに目的と面談候補の選び方を明示することで、迷いが減る可能性。効果は未確認。',
                    'flow':['本人の希望','目的と同意','候補日時','代表確認','本人と予約確定'],
                    'test':'代表承認後に同意範囲内で迷いの原因と計測指標を確認。予約APIと実顧客への連絡は未実行。'})
            elif intent=='planning':
                work('marketing_plan','AI08',{'plan':plan['items'],'targets':context['monthly_goals'],'execution':'代表承認待ち'})
            if prepare_draft and intent=='report' and not plan['owner_capacity_reached'] and self.draft_allowed(context):
                work('content_plan','AI08',{'theme':'暮らしで大切にしたいこと','mode':'固定テンプレート案','target':None})
                body={'title':'ご自身が知りたいことを大切に','body':'バイオレットからの情報案内です。健康・美容・新しい働き方など、関心は人それぞれ。希望する情報を、ご自身の意思で選んでください。',
                      'hashtags':['#バイオレット','#暮らしを考える'],'cta':'案内先と利用目的を代表確認後に設定。登録・面談は希望者のみ。','platform':'Instagram','source':'固定テンプレート・未公開'}
                from .learning import Learning,apply_style
                learning=Learning(self.store,initialize=False).runtime('AI09','sns','growth:'+identifier+':copy')
                body=apply_style(body,learning['styles'])
                item=work('copy','AI09',body)
                def create_draft():
                    self._lease(identifier,lease)
                    if not self.draft_allowed(context):
                        raise LocalRunError('Representative capacity changed')
                    old=self.db.execute('SELECT id FROM campaigns WHERE campaign_key=?',('growth-'+identifier,)).fetchone()
                    if old:
                        return self.db.execute('SELECT id FROM content_versions WHERE campaign_id=? ORDER BY version DESC LIMIT 1',(old[0],)).fetchone()[0]
                    campaign=uuid4().hex
                    self.db.execute('INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?,?)',(campaign,'growth-'+identifier,digest(body),'定期ローカル処理の単一原稿案',1,'Instagram',context['period']['start'],'offline_single_template_v1',now().isoformat()))
                    content=self.content._insert(campaign,'scheduled-draft','sns',body,context['period']['start'],task=item['task_id'])
                    self.content.audit(content)
                    return content
                draft=self.store._transaction(create_draft)
            work('quality','AI28',self.quality(context,plan,draft))
            proposal_due=datetime.combine(date.fromisoformat(plan['next_period']['end_exclusive']),datetime.min.time(),JST).astimezone(timezone.utc).isoformat()
            def propose():
                self._lease(identifier,lease)
                proposed=[]
                for index,item in enumerate(plan['items']):
                    task=self.store.create_task(f'growth-proposal:{identifier}:{index}',item['agent'],
                        {'instruction':item['work'],'reason':item['reason'],'run_id':identifier,'due_at':proposal_due,'execution':'代表承認後の手動作業のみ'},
                        action='management_decision')
                    self.db.execute('INSERT OR IGNORE INTO growth_proposals VALUES (?,?,?,?,?)',(task,identifier,item['priority'],proposal_due,'approval_pending'))
                    proposed.append({'task_id':task,'agent':item['agent'],'priority':item['priority'],'due_at':proposal_due,'status':'approval_pending'})
                return proposed
            proposal_tasks=self.store._transaction(propose)
            report={'runtime_limits':{'current_owner_capacity_allows_draft':self.draft_allowed(context),'paid_api_allowed':False,'actual_api_calls':0},'proposal_tasks':proposal_tasks,'title':CADENCES[cadence]+'業務報告','mode':'local_rules_v1','context':context,'plan':plan,
                'draft_content_id':draft,'actions_executed':False,'status_ja':'代表承認待ち・改善案未実行',
                'completed_local_steps':[{'agent':s['agent'],'artifact_id':s['artifact_id'],'task_id':s['task_id']} for s in steps],
                'notice':'ローカル計算・固定テンプレート。AIモデル/外部API未接続。機械的引継ぎと人のレビューを区別。'}
            final_work=work('report','AI01',report)
            report=json.loads(self.db.execute('SELECT content FROM artifacts WHERE id=?',(final_work['artifact_id'],)).fetchone()[0])
            def finish():
                self._lease(identifier,lease)
                self.db.execute("UPDATE growth_runs SET status='succeeded_local',report=?,lease=NULL,finished_at=? WHERE id=?",(encode(report),now().isoformat(),identifier))
                self.db.execute("UPDATE growth_attempts SET outcome='succeeded_local',finished_at=? WHERE run_id=? AND outcome='running'",(now().isoformat(),identifier))
                self.audit(actor,'local_report_approval_pending',identifier)
            self.store._transaction(finish)
        except Exception as error:
            stopped=isinstance(error,PausedRun)
            code='operator_paused' if stopped else 'time_limit' if isinstance(error,TimedOutRun) else 'local_failure'
            def failed():
                row=self.db.execute('SELECT lease FROM growth_runs WHERE id=?',(identifier,)).fetchone()
                if row and row[0]==lease:
                    self.db.execute('UPDATE growth_runs SET status=?,error_code=?,lease=NULL WHERE id=?',('paused' if stopped else 'failed',code,identifier))
                    self.db.execute("UPDATE growth_attempts SET outcome=?,code=?,finished_at=? WHERE run_id=? AND outcome='running'",('interrupted' if stopped else 'failed',code,now().isoformat(),identifier))
                    if schedule_id and not stopped:
                        self.db.execute("UPDATE growth_schedules SET enabled=0,state='auto_paused' WHERE id=?",(schedule_id,))
                    self.audit(actor,'operator_paused' if stopped else 'local_failure_auto_stop',identifier,{'code':code})
            self.store._transaction(failed)
        return identifier

    def tick(self,at=None,actor='local_scheduler'):
        at=instant(at or now())
        schedules=self.db.execute('SELECT s.*,COALESCE(v.revision,1) AS revision FROM growth_schedules s LEFT JOIN growth_schedule_versions v ON v.schedule_id=s.id WHERE s.enabled=1 AND s.next_due<=? ORDER BY s.next_due LIMIT 3',(at.isoformat(),)).fetchall()
        results=[]
        for schedule in schedules:
            period=window(schedule['cadence'],at,schedule['period_mode'])
            key='schedule:'+schedule['id']+':v'+str(schedule['revision'])+':'+period['period_key']
            try:
                identifier=self.run(key,schedule['cadence'],at,schedule['period_mode'],bool(schedule['prepare_draft']),actor,schedule['id'],schedule_revision=schedule['revision'])
            except Exception:
                def prepare_failure():
                    self.db.execute("UPDATE growth_schedules SET enabled=0,state='auto_paused' WHERE id=?",(schedule['id'],))
                    self.audit(actor,'schedule_prepare_failure',schedule['id'],{'code':'prepare_failed'})
                self.store._transaction(prepare_failure)
                continue
            def advance():
                row=self.db.execute('SELECT status FROM growth_runs WHERE id=?',(identifier,)).fetchone()
                if row['status']=='succeeded_local':
                    self.db.execute('UPDATE growth_schedules SET next_due=? WHERE id=? AND enabled=1',(following(schedule['cadence'],schedule['anchor'],at),schedule['id']))
            self.store._transaction(advance)
            results.append(identifier)
        self.audit(actor,'scheduler_tick','local',{'runs':len(results),'at':at.isoformat(),'actual_api_calls':0})
        return results

    def decide(self,identifier,decision,actor):
        if decision not in ('approve','changes_requested','reject'):
            raise ValueError('Invalid decision')
        def op():
            row=self.db.execute('SELECT * FROM growth_runs WHERE id=?',(identifier,)).fetchone()
            if not row or row['status']!='succeeded_local' or row['decision']!='approval_pending':
                raise ValueError('Report not awaiting approval')
            final=self.db.execute("SELECT a.content,w.output_digest FROM growth_work w JOIN artifacts a ON a.id=w.artifact_id WHERE w.run_id=? AND w.step='report'",(identifier,)).fetchone()
            if not final or digest(json.loads(final[0]))!=final[1] or digest(json.loads(row['report']))!=final[1]:
                raise ValueError('Report artifact changed')
            self.db.execute('INSERT INTO growth_decisions(run_id,decision,actor,report_digest,created_at) VALUES (?,?,?,?,?)',(identifier,decision,actor,digest(json.loads(row['report'])),now().isoformat()))
            self.db.execute('UPDATE growth_proposals SET status=? WHERE run_id=?',('approved_local' if decision=='approve' else decision,identifier))
            self.db.execute('UPDATE growth_runs SET decision=? WHERE id=?',(decision,identifier))
            self.audit(actor,'report_decision',identifier,{'decision':decision,'actions_executed':False})
        self.store._transaction(op)

    def instruction(self,key,text,actor,at=None):
        safe_input(text)
        if '紹介' in text:
            intent='referral'
        elif '面談' in text and ('改善' in text or '増や' in text):
            intent='booking_improvement'
        elif '計画' in text:
            intent='planning'
        elif '目標' in text or '達成' in text:
            intent='goal_review'
        elif '料金' in text or '費用' in text:
            intent='cost_review'
        elif '報告' in text or 'レポート' in text:
            intent='report'
        else:
            raise ValueError('Supported local instruction not matched')
        cadence='monthly' if '今月' in text or '月次' in text else 'weekly' if '来週' in text or '週次' in text else 'daily'
        return self.run(key,cadence,at,actor=actor,intent=intent,instruction=text)
