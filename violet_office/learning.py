"""Approved, scoped knowledge and local improvement evidence, not model training.

Free text is always data. Only fixed style flags can affect local templates.
No customer tables are imported, no tools/settings/permissions can be changed.
"""
import json
from contextvars import ContextVar
import re
from datetime import datetime,date,timedelta,timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from .content import ContentOffice,encode,digest,safe_input
from .store import now
from .integrations.adapters import require

KINDS={'general','sns','line','referral','booking','report','planning','support'}
CATEGORIES={'policy':'事業方針','voice':'ブランド表現','sns':'承認済みSNS','line':'承認済みLINE','referral':'紹介文',
    'booking':'予約手順','faq':'FAQ','official':'公式資料・規約','guidance':'代表の指導','cases':'成功・失敗事例'}
STYLES={'friendly':'親しみやすく','plain_language':'専門用語を減らす','soft_cta':'任意の案内を強調','evidence_note':'資料確認を促す'}
FEEDBACK={'good':'良い','revise':'修正が必要','reject':'不採用','standard':'今後の標準候補'}
IMPROVEMENTS={'style':'文章・プロンプト案','procedure':'手順','allocation':'担当割当','quality':'品質','cost':'費用','errors':'エラー防止','funnel':'集客・紹介・面談導線'}
_CHECKING=ContextVar('knowledge_validation_stack',default=())
SCHEMA='''
CREATE TABLE IF NOT EXISTS learning_work (
 artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 agent TEXT NOT NULL, duty TEXT NOT NULL, task_kind TEXT NOT NULL, executed_at TEXT, indexed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_feedback (
 id TEXT PRIMARY KEY, entry_key TEXT UNIQUE NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
 target_digest TEXT NOT NULL, agent TEXT NOT NULL, task_kind TEXT NOT NULL, choice TEXT NOT NULL,
 comment TEXT NOT NULL, styles TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_knowledge (
 id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, version INTEGER NOT NULL, category TEXT NOT NULL,
 task_kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, source_kind TEXT NOT NULL,
 source_ref TEXT NOT NULL, source_digest TEXT NOT NULL, scope TEXT NOT NULL, expires_at TEXT NOT NULL,
 status TEXT NOT NULL, attestation TEXT NOT NULL, item_digest TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(logical_id,version));
CREATE TABLE IF NOT EXISTS learning_audit (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, ref TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_usages (
 id TEXT PRIMARY KEY, consumer TEXT NOT NULL, agent TEXT NOT NULL, task_kind TEXT NOT NULL,
 knowledge_id TEXT NOT NULL REFERENCES learning_knowledge(id), version INTEGER NOT NULL,
 item_digest TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(consumer,agent,knowledge_id));
CREATE TABLE IF NOT EXISTS learning_ratings (
 id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT NOT NULL, target_digest TEXT NOT NULL,
 agent TEXT NOT NULL, task_kind TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 5),
 rubric TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(target_kind,target_id,rubric));
CREATE TABLE IF NOT EXISTS learning_evaluations (
 id TEXT PRIMARY KEY, task_kind TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL,
 result TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_proposals (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, task_kind TEXT NOT NULL, before_styles TEXT NOT NULL,
 after_styles TEXT NOT NULL, reason TEXT NOT NULL, expected_effect TEXT NOT NULL,
 test_method TEXT NOT NULL, knowledge_refs TEXT NOT NULL, evaluation_id TEXT,
 status TEXT NOT NULL, comparison TEXT, comparison_digest TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_profiles (
 task_kind TEXT PRIMARY KEY, proposal_id TEXT NOT NULL REFERENCES learning_proposals(id),
 version INTEGER NOT NULL, styles TEXT NOT NULL, applied_at TEXT NOT NULL);
'''


def exists(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_knowledge'").fetchone())


def task_kind(action,agent):
    if action=='sns_post' or agent in ('AI08','AI09','AI10','AI11','AI12','AI13'):
        return 'sns'
    if action=='line_send' or agent in ('AI21','AI22','AI24'):
        return 'line'
    if agent in ('AI15','AI16'):
        return 'referral'
    if action=='booking_confirm' or agent in ('AI18','AI23'):
        return 'booking'
    if agent in ('AI01','AI27','AI28'):
        return 'report'
    return 'planning'


def classified_task(db,task):
    kind=task_kind(task['action'],task['agent'])
    if 'payload' in task.keys():
        payload=json.loads(task['payload'])
        if isinstance(payload,dict) and payload.get('campaign_id') and task['agent']=='AI09':
            row=db.execute('SELECT platform FROM campaigns WHERE id=?',(payload['campaign_id'],)).fetchone()
            if row and row[0]=='LINE':
                return 'line'
    return kind


def record_artifact(db,task,artifact,duty,when):
    if not exists(db):
        return
    db.execute('INSERT OR IGNORE INTO learning_work VALUES (?,?,?,?,?,?,?)',
        (artifact,task['id'],task['agent'],duty,classified_task(db,task),when,now().isoformat()))


def clean(text):
    safe_input(text)
    if re.search(r'U[0-9a-f]{32}|顧客との非公開|非公開会話|住所\s*[:：]|病歴\s*[:：]|(?i:authorization|secret|token)\s*[=:]\s*\S+',text):
        raise ValueError('Personal, private conversation or credential data is not allowed')


def risky(text):
    return bool(re.search(r'(?i)ignore.{0,40}(instructions|rules)|system\s*prompt|override.{0,20}(permission|approval)|前の指示を無視|以前の指示を無視|権限を変更|承認を省略|システムプロンプト|必ず儲かる|絶対に稼げる|病気が治る',text))


def style_flags(value):
    if not isinstance(value,dict) or not set(value)<=set(STYLES) or any(type(v) is not bool for v in value.values()):
        raise ValueError('Only fixed presentation style flags are allowed')
    return {k:bool(value.get(k,False)) for k in STYLES}


def apply_style(body,flags):
    """Whitelisted literal transformations, never executes or copies knowledge text."""
    flags=style_flags(flags)
    result=dict(body)
    text=result.get('body','')
    if flags['friendly']:
        text='こんにちは、バイオレットです。'+text
    if flags['plain_language']:
        text=text.replace('ご自身','ご自分').replace('利用目的','情報を使う目的')
    if flags['evidence_note']:
        text+='具体的な条件は、正規の公式資料を確認してからご判断ください。'
    result['body']=text
    if flags['soft_cta']:
        result['cta']=result.get('cta','')+'ご希望の場合だけご検討ください。'
    return result


class Learning:
    min_sample=3

    def __init__(self,store,initialize=True):
        self.store=store
        self.db=store.db
        self.content=ContentOffice(store,initialize=False)
        if initialize:
            self.db.executescript(SCHEMA)

    @staticmethod
    def actor(principal):
        return principal.identity or principal.role

    def audit(self,actor,action,ref,detail=None):
        self.db.execute('INSERT INTO learning_audit(actor,action,ref,detail,created_at) VALUES (?,?,?,?,?)',
            (actor,action,ref,encode(detail or {}),now().isoformat()))

    def target(self,kind,ref):
        if kind=='artifact':
            row=self.db.execute('SELECT a.*,t.status,t.action,t.payload,t.agent FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE a.id=?',(ref,)).fetchone()
            if not row:
                raise ValueError('Unknown artifact')
            return row['author'],classified_task(self.db,row),digest(row['content'])
        if kind=='content':
            row=self.db.execute('SELECT c.*,t.agent,t.action FROM content_versions c LEFT JOIN tasks t ON t.id=c.task_id WHERE c.id=?',(ref,)).fetchone()
            if not row:
                raise ValueError('Unknown content')
            if digest(json.loads(row['body']))!=row['digest']:
                raise ValueError('Content body changed')
            return row['agent'] or 'AI09',('line' if row['kind']=='sns' and json.loads(row['body']).get('platform')=='LINE' else {'sns':'sns','line':'line','referral':'referral'}.get(row['kind'],'planning')),row['digest']
        if kind=='report':
            row=self.db.execute('SELECT report FROM growth_runs WHERE id=? AND report IS NOT NULL',(ref,)).fetchone()
            if not row:
                raise ValueError('Unknown report')
            return 'AI01','report',digest(json.loads(row['report']))
        raise ValueError('Private customer data sources are not supported')

    def feedback(self,principal,key,kind,ref,choice,comment,styles,attested):
        require(principal,True)
        clean(comment)
        flags=style_flags(styles)
        if not attested or choice not in FEEDBACK or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key):
            raise ValueError('Nonpersonal attestation and valid feedback required')
        def op():
            agent,work_kind,target_hash=self.target(kind,ref)
            old=self.db.execute('SELECT * FROM learning_feedback WHERE entry_key=?',(key,)).fetchone()
            if old:
                if (old['target_kind'],old['target_id'],old['target_digest'],old['choice'],old['comment'],old['styles'])!=(kind,ref,target_hash,choice,comment,encode(flags)):
                    raise ValueError('Feedback key conflict')
                return old['id']
            identifier=uuid4().hex
            self.db.execute('INSERT INTO learning_feedback VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,key,kind,ref,target_hash,agent,work_kind,choice,comment,encode(flags),'quarantined' if risky(comment) else 'approval_pending',now().isoformat()))
            self.audit(self.actor(principal),'feedback_recorded',identifier,{'choice':choice})
            return identifier
        return self.store._transaction(op)

    def decide_feedback(self,principal,identifier,decision):
        require(principal,True)
        if decision not in ('approve','reject','revoke'):
            raise ValueError('Invalid decision')
        def op():
            row=self.db.execute('SELECT * FROM learning_feedback WHERE id=?',(identifier,)).fetchone()
            if not row or row['status']!=('approved' if decision=='revoke' else 'approval_pending'):
                raise ValueError('Feedback not eligible')
            if self.target(row['target_kind'],row['target_id'])[2]!=row['target_digest']:
                raise ValueError('Target changed')
            self.db.execute('UPDATE learning_feedback SET status=? WHERE id=?',({'approve':'approved','reject':'rejected','revoke':'revoked'}[decision],identifier))
            self.audit(self.actor(principal),'feedback_'+decision,identifier)
        self.store._transaction(op)

    def _source_digest(self,kind,ref,require_approved=False):
        if kind=='manual':
            clean(ref)
            return digest(['manual_public_or_nonpersonal',ref])
        if kind in ('artifact','content'):
            value=self.target(kind,ref)[2]
            if require_approved:
                if kind=='content':
                    self.content.verify_approved(ref)
                else:
                    row=self.db.execute('SELECT t.status,t.action FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE a.id=?',(ref,)).fetchone()
                    if row['status'] not in ('completed','manual_execution_pending'):
                        raise ValueError('Unapproved artifact')
            return value
        if kind=='feedback':
            row=self.db.execute('SELECT * FROM learning_feedback WHERE id=?',(ref,)).fetchone()
            if not row or require_approved and row['status']!='approved':
                raise ValueError('Unapproved feedback')
            if self.target(row['target_kind'],row['target_id'])[2]!=row['target_digest']:
                raise ValueError('Feedback target changed')
            return digest([row['comment'],row['styles'],row['target_digest']])
        raise ValueError('Unsupported source; customer conversations cannot be imported')

    @staticmethod
    def _item_digest(row):
        return digest([row[k] for k in ('logical_id','version','category','task_kind','title','body','source_kind','source_ref','source_digest','scope','expires_at','attestation')])

    def knowledge(self,principal,category,kind,title,body,source_kind,source_ref,scope,expires,attested,previous=None):
        require(principal,True)
        for text in (title,body):
            clean(text)
        if source_kind=='manual':
            clean(source_ref)
        elif not isinstance(source_ref,str) or not re.fullmatch(r'[a-f0-9]{32}',source_ref):
            raise ValueError('Opaque source ID required')
        expiry=datetime.fromisoformat(expires)
        if expiry.tzinfo is None or not now()<expiry.astimezone(timezone.utc)<=now()+timedelta(days=366):
            raise ValueError('Explicit future expiry within one year required')
        if category not in CATEGORIES or kind not in KINDS or not attested or not scope or not set(scope)<=set(self.store.agents) or len(scope)!=len(set(scope)):
            raise ValueError('Classification, explicit scope and nonpersonal attestation required')
        if len(title)>120 or len(body)>8000:
            raise ValueError('Knowledge too long')
        def op():
            source_hash=self._source_digest(source_kind,source_ref)
            if source_kind=='feedback':
                source=self.db.execute('SELECT comment FROM learning_feedback WHERE id=?',(source_ref,)).fetchone()
                if body!=source['comment']:
                    raise ValueError('Feedback promotion must contain only the reviewed guidance comment')
            logical=uuid4().hex
            version=1
            if previous:
                old=self.db.execute('SELECT * FROM learning_knowledge WHERE id=?',(previous,)).fetchone()
                if not old or old['version']!=self.db.execute('SELECT MAX(version) FROM learning_knowledge WHERE logical_id=?',(old['logical_id'],)).fetchone()[0]:
                    raise ValueError('Latest revision required')
                logical,version=old['logical_id'],old['version']+1
            identifier=uuid4().hex
            values=dict(logical_id=logical,version=version,category=category,task_kind=kind,title=title,body=body,source_kind=source_kind,source_ref=source_ref,source_digest=source_hash,
                scope=encode(sorted(scope)),expires_at=expiry.astimezone(timezone.utc).isoformat(),attestation='public_or_nonpersonal')
            self.db.execute('INSERT INTO learning_knowledge VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,logical,version,category,kind,title,body,source_kind,source_ref,source_hash,values['scope'],values['expires_at'],
                 'quarantined' if risky(body+' '+title) else 'approval_pending',values['attestation'],self._item_digest(values),now().isoformat()))
            self.audit(self.actor(principal),'knowledge_candidate_created',identifier,{'version':version})
            return identifier
        return self.store._transaction(op)

    def valid(self,row):
        stack=_CHECKING.get()
        if row['id'] in stack or len(stack)>=12:
            return False
        token=_CHECKING.set(stack+(row['id'],))
        try:
            return self._valid(row)
        finally:
            _CHECKING.reset(token)

    def _valid(self,row):
        if row['status']!='approved' or row['expires_at']<=now().isoformat() or self._item_digest(row)!=row['item_digest']:
            return False
        if row['version']!=self.db.execute('SELECT MAX(version) FROM learning_knowledge WHERE logical_id=?',(row['logical_id'],)).fetchone()[0]:
            return False
        try:
            return self._source_digest(row['source_kind'],row['source_ref'],True)==row['source_digest']
        except (ValueError,KeyError,TypeError):
            return False

    def decide_knowledge(self,principal,identifier,decision):
        require(principal,True)
        if decision not in ('approve','reject','revoke'):
            raise ValueError('Invalid decision')
        def op():
            row=self.db.execute('SELECT * FROM learning_knowledge WHERE id=?',(identifier,)).fetchone()
            if not row or row['status']!=('approved' if decision=='revoke' else 'approval_pending'):
                raise ValueError('Knowledge not eligible')
            if decision=='approve':
                if row['expires_at']<=now().isoformat() or self._item_digest(row)!=row['item_digest'] or self._source_digest(row['source_kind'],row['source_ref'],True)!=row['source_digest']:
                    raise ValueError('Source, expiry or item changed')
                if row['version']!=self.db.execute('SELECT MAX(version) FROM learning_knowledge WHERE logical_id=?',(row['logical_id'],)).fetchone()[0]:
                    raise ValueError('Superseded version')
            self.db.execute('UPDATE learning_knowledge SET status=? WHERE id=?',({'approve':'approved','reject':'rejected','revoke':'revoked'}[decision],identifier))
            self.audit(self.actor(principal),'knowledge_'+decision,identifier,{'version':row['version']})
        self.store._transaction(op)

    def search_worker(self,agent,kind,query='',limit=20):
        if agent not in self.store.agents or kind not in KINDS or not 1<=limit<=20 or len(query)>200:
            raise ValueError('Invalid scoped search')
        escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        rows=self.db.execute("SELECT * FROM learning_knowledge WHERE status='approved' AND task_kind IN (?, 'general') AND expires_at>? AND instr(scope,?)>0 AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\') ORDER BY created_at DESC LIMIT 200",(kind,now().isoformat(),encode(agent),'%'+escaped+'%','%'+escaped+'%')).fetchall()
        result=[]
        for row in rows:
            if agent in json.loads(row['scope']) and self.valid(row):
                result.append(dict(row))
                if len(result)>=limit:
                    break
        return result

    def search(self,principal,agent,kind,query=''):
        require(principal,True)
        return self.search_worker(agent,kind,query)

    def promote_feedback(self,principal,identifier,title,scope,expires):
        require(principal,True)
        row=self.db.execute("SELECT * FROM learning_feedback WHERE id=? AND status='approved'",(identifier,)).fetchone()
        if not row:
            raise ValueError('Approved feedback required')
        return self.knowledge(principal,'guidance',row['task_kind'],title,row['comment'],'feedback',identifier,scope,expires,True)

    def rate(self,principal,kind,ref,score,rubric):
        require(principal,True)
        clean(rubric)
        if type(score) is not int or not 0<=score<=5 or len(rubric)>200:
            raise ValueError('Score 0-5 and rubric required')
        def op():
            agent,work_kind,target_hash=self.target(kind,ref)
            if self.db.execute('SELECT 1 FROM learning_ratings WHERE target_kind=? AND target_id=? AND rubric=?',(kind,ref,rubric)).fetchone():
                raise ValueError('This target/rubric already has a preserved rating')
            identifier=uuid4().hex
            self.db.execute('INSERT INTO learning_ratings VALUES (?,?,?,?,?,?,?,?,?,?)',(identifier,kind,ref,target_hash,agent,work_kind,score,rubric,self.actor(principal),now().isoformat()))
            self.audit(self.actor(principal),'quality_rating_recorded',identifier)
            return identifier
        return self.store._transaction(op)

    def index_history(self,principal):
        require(principal,True)
        def op():
            rows=self.db.execute("SELECT a.id,a.task_id,t.agent,t.action,t.payload,MIN(e.created_at) AS executed_at FROM artifacts a JOIN tasks t ON t.id=a.task_id LEFT JOIN events e ON e.task_id=t.id AND e.event='artifact_created' LEFT JOIN learning_work w ON w.artifact_id=a.id WHERE w.artifact_id IS NULL GROUP BY a.id LIMIT 500").fetchall()
            for row in rows:
                record_artifact(self.db,{'id':row['task_id'],'agent':row['agent'],'action':row['action'],'payload':row['payload']},row['id'],self.store.agents[row['agent']]['role'],row['executed_at'])
            self.audit(self.actor(principal),'history_metadata_indexed','local',{'count':len(rows)})
            return len(rows)
        return self.store._transaction(op)

    def evaluate(self,principal,kind,start,end):
        require(principal,True)
        start_day,end_day=date.fromisoformat(start),date.fromisoformat(end)
        if kind not in KINDS|{'all'} or not 0<(end_day-start_day).days<=366:
            raise ValueError('Bounded evaluation period required')
        a=datetime.combine(start_day,datetime.min.time(),ZoneInfo('Asia/Tokyo')).astimezone(timezone.utc).isoformat()
        b=datetime.combine(end_day,datetime.min.time(),ZoneInfo('Asia/Tokyo')).astimezone(timezone.utc).isoformat()
        tasks=self.db.execute("SELECT t.*,MIN(e.created_at) AS created FROM tasks t JOIN events e ON e.task_id=t.id AND e.event='created' GROUP BY t.id HAVING MIN(e.created_at)>=? AND MIN(e.created_at)<?",(a,b)).fetchall()
        agents=[]
        for agent,config in self.store.agents.items():
            selected=[t for t in tasks if t['agent']==agent and (kind=='all' or classified_task(self.db,t)==kind)]
            ids={t['id'] for t in selected}
            n=len(selected)
            artifacts=self.db.execute('SELECT * FROM artifacts WHERE author=?',(agent,)).fetchall()
            artifact_ids={r['id'] for r in artifacts if r['task_id'] in ids}
            reviews=[r for r in self.db.execute('SELECT * FROM reviews') if r['artifact_id'] in artifact_ids]
            decisions=[r for r in self.db.execute('SELECT * FROM approvals') if r['task_id'] in ids]
            runs=[r for r in self.db.execute("SELECT * FROM runs WHERE status IN ('completed','failed')") if r['task_id'] in ids]
            content_cohort={r[0] for r in self.db.execute('SELECT id,task_id FROM content_versions') if r[1] in ids}
            report_cohort={r[0] for r in self.db.execute("SELECT run_id,task_id FROM growth_work WHERE step='report'") if r[1] in ids}
            belongs=lambda item:(item['target_kind']=='artifact' and item['target_id'] in artifact_ids) or (item['target_kind']=='content' and item['target_id'] in content_cohort) or (item['target_kind']=='report' and item['target_id'] in report_cohort)
            ratings=[r for r in self.db.execute('SELECT * FROM learning_ratings WHERE agent=?',(agent,)) if belongs(r) and (kind=='all' or r['task_kind']==kind)]
            valid_ratings=[]
            for rating in ratings:
                try:
                    if self.target(rating['target_kind'],rating['target_id'])[2]==rating['target_digest']:
                        valid_ratings.append(rating)
                except ValueError:
                    pass
            ratings=valid_ratings
            content_rows=[r for r in self.db.execute('SELECT * FROM content_versions') if r['task_id'] in ids]
            content_ids={r['id'] for r in content_rows}
            content_decisions=[r for r in self.db.execute("SELECT content_id,event FROM content_events WHERE event IN ('approved','rejected')") if r['content_id'] in content_ids]
            machine_checks=[r for r in self.db.execute('SELECT content_id,blockers FROM content_audits') if r['content_id'] in content_ids]
            durations=[]
            for task in selected:
                for run in runs:
                    if run['task_id']!=task['id'] or run['status']!='completed':
                        continue
                    finished=self.db.execute("SELECT MIN(created_at) FROM events WHERE task_id=? AND event='artifact_created'",(task['id'],)).fetchone()[0]
                    if finished:
                        began=datetime.fromisoformat(run['deadline'])-timedelta(seconds=task['timeout_seconds'])
                        elapsed=(datetime.fromisoformat(finished)-began).total_seconds()
                        if elapsed>=0:
                            durations.append(elapsed)
            costs=[r for r in self.db.execute("SELECT * FROM office_costs WHERE agent=? AND kind='confirmed' AND mode='manual'",(agent,)) if r['task_id'] in ids] if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='office_costs'").fetchone() else []
            charged={r['task_id'] for r in costs}
            ratio=lambda numerator,denominator:round(numerator/denominator*100,2) if denominator>=self.min_sample else None
            revisions=sum(r['choice']=='revise' and r['status']=='approved' and (kind=='all' or r['task_kind']==kind) for r in self.db.execute('SELECT * FROM learning_feedback WHERE agent=?',(agent,)) if belongs(r))
            rated_targets={}
            for rating in ratings:
                rated_targets.setdefault((rating['target_kind'],rating['target_id']),[]).append(rating['score'])
            agents.append({'agent':agent,'role':config['role'],'task_kind':kind,'sample_size':n,'status':'記述統計のみ・能力順位ではない' if n>=self.min_sample else '評価不能（対象3件未満）',
                'task_completion_percent':ratio(sum(t['status']=='completed' for t in selected),n),
                'quality_pass_percent':ratio(sum(r['verdict']=='pass' for r in reviews),len(reviews)),
                'representative_approval_percent':ratio(sum(r['decision']=='approve' for r in decisions),len(decisions)),
                'content_approval_percent':ratio(sum(r['event']=='approved' for r in content_decisions),len(content_decisions)),
                'machine_check_percent':ratio(sum(not json.loads(r['blockers']) for r in machine_checks),len(machine_checks)),
                'error_percent':ratio(sum(r['status']=='failed' for r in runs),len(runs)),
                'content_revision_count':sum(r['event']=='superseded' for r in self.db.execute('SELECT content_id,event FROM content_events') if r['content_id'] in content_ids),'approved_revision_feedback':revisions,'average_local_seconds':round(sum(durations)/len(durations),4) if len(durations)>=self.min_sample else None,
                'confirmed_cost_per_charged_task_microusd':round(sum(r['amount'] for r in costs)/len(charged),2) if len(charged)>=self.min_sample else None,
                'quality_score':round(sum(sum(values)/len(values) for values in rated_targets.values())/len(rated_targets),2) if len(rated_targets)>=self.min_sample else None,
                'denominators':{'tasks':n,'reviews':len(reviews),'content_decisions':len(content_decisions),'machine_checks':len(machine_checks),'approval_decisions':len(decisions),'attempts':len(runs),'durations':len(durations),'rated_items':len(rated_targets),'charged_tasks':len(charged)},
                'business_outcomes':None,'notes':'完了率は登録タスク状態比で未着手も含む。レビュー・承認・料金の各母数は別。経過時間はローカル記録由来でAPI/人作業時間ではない。成果や因果関係を推定しない。'})
        result={'task_kind':kind,'start':start,'end_exclusive':end,'minimum_sample':self.min_sample,'agents':agents,'ranking':False,'business_data_source':'顧客コホート・実API未接続','mode':'local_descriptive_statistics'}
        identifier=uuid4().hex
        self.db.execute('INSERT INTO learning_evaluations VALUES (?,?,?,?,?,?)',(identifier,kind,start,end,encode(result),now().isoformat()))
        self.audit(self.actor(principal),'evaluation_recorded',identifier)
        return identifier,result

    def propose(self,principal,kind,knowledge_ids,evaluation_id=None):
        require(principal,True)
        if kind not in KINDS or len(knowledge_ids)>20 or len(knowledge_ids)!=len(set(knowledge_ids)):
            raise ValueError('Bounded task kind and sources required')
        producer={'sns':'AI09','line':'AI22','referral':'AI16','report':'AI01','planning':'AI01','booking':'AI18','support':'AI21','general':'AI01'}[kind]
        refs=[]
        flags=style_flags({})
        for identifier in knowledge_ids:
            row=self.db.execute('SELECT * FROM learning_knowledge WHERE id=?',(identifier,)).fetchone()
            if not row or not self.valid(row) or producer not in json.loads(row['scope']) or row['task_kind'] not in ('general',kind):
                raise ValueError('Approved scoped current knowledge required')
            refs.append({'id':row['id'],'version':row['version'],'digest':row['item_digest']})
            if row['source_kind']=='feedback':
                feedback=self.db.execute('SELECT styles FROM learning_feedback WHERE id=?',(row['source_ref'],)).fetchone()
                chosen=style_flags(json.loads(feedback[0]))
                flags={key:flags[key] or chosen[key] for key in STYLES}
        if evaluation_id and not self.db.execute('SELECT 1 FROM learning_evaluations WHERE id=?',(evaluation_id,)).fetchone():
            raise ValueError('Unknown evaluation')
        evaluation=json.loads(self.db.execute('SELECT result FROM learning_evaluations WHERE id=?',(evaluation_id,)).fetchone()[0]) if evaluation_id else None
        if evaluation and evaluation['task_kind'] not in ('all',kind):
            raise ValueError('Evaluation must match the proposed work kind')
        measured=next((entry for entry in evaluation['agents'] if entry['agent']==producer),None) if evaluation else None
        context=('記録タスク '+str(measured['sample_size'])+'件、人の品質評点 '+str(measured['quality_score'] if measured['quality_score'] is not None else '評価不能')+'。登録期間の同種業務の記述統計で、能力や因果関係を断定しない。') if measured else '評価記録の指定なし。業務成果は未評価。'
        current=self.runtime(producer,kind,'proposal-preview',record=False)['styles']
        proposals=[]
        for category,title in IMPROVEMENTS.items():
            if category=='style' and not refs:
                continue
            identifier=uuid4().hex
            reason='承認済みの指導を参照した表現候補。効果は未検証。' if category=='style' else '評価母数・根拠・業務上の制約を人が確認するための検討案。'
            reason=context+' '+reason
            expected='改善する可能性のある仮説。業務成果・費用削減は未計測。'
            method='固定テンプレートの前後をローカル比較し、代表が判断する。実顧客A/Bテストは未実施。' if category=='style' else '別のテスト環境で対象・指標・費用・安全条件を決める。自動適用は未実装。'
            descriptions={
                'procedure':('現行の作業手順','根拠・レビュー・承認の確認箇所と待機理由を明示して比較する。'),
                'allocation':('既存ルールによる担当選択','同種業務の母数と負荷を確認し、別環境で割当案を比較する。権限は変更しない。'),
                'quality':('機械チェックと人の確認','出典箇所と未確認項目を明示し、評価基準別の結果を比較する。'),
                'cost':('手動記帳・未計測の料金','まず請求と推定を照合し、必要な場合だけ重複処理削減を検討。上限は変更しない。'),
                'errors':('固定エラーコードと再試行上限','頻出コードを調査し再現テストを追加。上限と結果不明時の停止は維持する。'),
                'funnel':('実測と顧客コホートが未接続の導線','本人希望・同意・承認を維持し、計測方法と検証期間を人が決める。'),
            }
            before=encode(current) if category=='style' else encode({'description':descriptions[category][0]})
            after=encode(flags) if category=='style' else encode({'description':descriptions[category][1]})
            self.db.execute('INSERT INTO learning_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identifier,category,kind,before,after,reason,expected,method,encode(refs),evaluation_id,
                 'draft' if category=='style' and kind in ('sns','line','referral') else 'manual_only',None,None,now().isoformat()))
            self.audit(self.actor(principal),'improvement_proposed',identifier,{'category':category})
            proposals.append(identifier)
        return proposals

    def _refs_valid(self,refs,agent,kind):
        for ref in refs:
            row=self.db.execute('SELECT * FROM learning_knowledge WHERE id=?',(ref['id'],)).fetchone()
            if not row or not self.valid(row) or row['version']!=ref['version'] or row['item_digest']!=ref['digest'] or agent not in json.loads(row['scope']) or row['task_kind'] not in ('general',kind):
                return False
        return bool(refs)

    def compare(self,principal,identifier):
        require(principal,True)
        def op():
            row=self.db.execute("SELECT * FROM learning_proposals WHERE id=? AND category='style' AND status='draft'",(identifier,)).fetchone()
            if not row or row['task_kind'] not in ('sns','line','referral'):
                raise ValueError('Only supported local template styles can be tested')
            baseline={'body':'バイオレットからの情報案内です。ご自身が知りたい内容と利用目的をご確認ください。','cta':'希望する場合だけご検討ください。'}
            before=apply_style(baseline,json.loads(row['before_styles']))
            after=apply_style(baseline,json.loads(row['after_styles']))
            passed=not risky(encode(after)) and '希望' in after['cta']
            result={'environment':'isolated_local_template_comparison','before':before,'after':after,'checks':{'fixed_flags_only':True,'optional_contact_preserved':passed,'no_external_calls':True},
                'passed':passed,'business_effect':None,'cost_savings':None,'legal_compliance':'未検証・人の判断が必要'}
            binding=digest([row['before_styles'],row['after_styles'],row['knowledge_refs'],result])
            self.db.execute('UPDATE learning_proposals SET comparison=?,comparison_digest=?,status=? WHERE id=?',(encode(result),binding,'approval_pending' if passed else 'test_failed',identifier))
            self.audit(self.actor(principal),'local_comparison_recorded',identifier)
            return result
        return self.store._transaction(op)

    def _proposal_valid(self,row,agent):
        if not row['comparison']:
            return False
        result=json.loads(row['comparison'])
        return result.get('passed') is True and row['comparison_digest']==digest([row['before_styles'],row['after_styles'],row['knowledge_refs'],result]) and self._refs_valid(json.loads(row['knowledge_refs']),agent,row['task_kind'])

    def decide_proposal(self,principal,identifier,decision):
        require(principal,True)
        if decision not in ('approve','reject'):
            raise ValueError('Invalid decision')
        def op():
            row=self.db.execute("SELECT * FROM learning_proposals WHERE id=? AND status='approval_pending' AND category='style'",(identifier,)).fetchone()
            producer={'sns':'AI09','line':'AI22','referral':'AI16','report':'AI01','planning':'AI01','booking':'AI18','support':'AI21','general':'AI01'}.get(row['task_kind']) if row else None
            if not row or decision=='approve' and not self._proposal_valid(row,producer):
                raise ValueError('Current sources and successful local comparison required')
            self.db.execute('UPDATE learning_proposals SET status=? WHERE id=?',('approved' if decision=='approve' else 'rejected',identifier))
            self.audit(self.actor(principal),'improvement_'+decision,identifier,{'comparison_digest':row['comparison_digest']})
        self.store._transaction(op)

    def apply(self,principal,identifier):
        require(principal,True)
        def op():
            row=self.db.execute("SELECT * FROM learning_proposals WHERE id=? AND status='approved' AND category='style'",(identifier,)).fetchone()
            if not row or row['task_kind'] not in ('sns','line','referral'):
                raise ValueError('Tested and explicitly approved supported style required')
            producer={'sns':'AI09','line':'AI22','referral':'AI16','report':'AI01','planning':'AI01','booking':'AI18','support':'AI21','general':'AI01'}[row['task_kind']]
            if not self._proposal_valid(row,producer):
                raise ValueError('Expired, revoked or changed sources')
            current=self.runtime(producer,row['task_kind'],'apply-check',record=False)['styles']
            if current!=style_flags(json.loads(row['before_styles'])):
                raise ValueError('Baseline changed; compare a new proposal')
            version=self.db.execute('SELECT version FROM learning_profiles WHERE task_kind=?',(row['task_kind'],)).fetchone()
            self.db.execute('INSERT INTO learning_profiles VALUES (?,?,?,?,?) ON CONFLICT(task_kind) DO UPDATE SET proposal_id=excluded.proposal_id,version=excluded.version,styles=excluded.styles,applied_at=excluded.applied_at',
                (row['task_kind'],identifier,(version[0]+1) if version else 1,encode(style_flags(json.loads(row['after_styles']))),now().isoformat()))
            self.db.execute("UPDATE learning_proposals SET status='applied' WHERE id=?",(identifier,))
            self.audit(self.actor(principal),'local_style_applied',identifier,{'permissions_changed':False,'actual_api_calls':0})
        self.store._transaction(op)

    def runtime(self,agent,kind,consumer,record=True):
        if not exists(self.db):
            return {'styles':style_flags({}),'references':[],'profile':None}
        documents=self.search_worker(agent,kind)
        refs={r['id']:{'id':r['id'],'version':r['version'],'digest':r['item_digest']} for r in documents}
        styles=style_flags({})
        profile=None
        active=self.db.execute('SELECT * FROM learning_profiles WHERE task_kind=?',(kind,)).fetchone()
        if active:
            proposal=self.db.execute("SELECT * FROM learning_proposals WHERE id=? AND status='applied'",(active['proposal_id'],)).fetchone()
            if proposal and self._proposal_valid(proposal,agent) and style_flags(json.loads(active['styles']))==style_flags(json.loads(proposal['after_styles'])):
                styles=style_flags(json.loads(active['styles']))
                profile={'id':proposal['id'],'version':active['version']}
                for ref in json.loads(proposal['knowledge_refs']):
                    refs[ref['id']]=ref
        if record:
            for ref in refs.values():
                self.db.execute('INSERT OR IGNORE INTO learning_usages VALUES (?,?,?,?,?,?,?,?)',
                    (uuid4().hex,consumer,agent,kind,ref['id'],ref['version'],ref['digest'],now().isoformat()))
        return {'styles':styles,'references':list(refs.values()),'profile':profile}

    def history(self,principal,agent):
        require(principal,True)
        if agent not in self.store.agents:
            raise ValueError('Unknown employee')
        result=[]
        for row in self.db.execute('SELECT w.*,a.content FROM learning_work w JOIN artifacts a ON a.id=w.artifact_id WHERE w.agent=? ORDER BY w.indexed_at DESC LIMIT 100',(agent,)):
            item=dict(row)
            content_ids={r[0] for r in self.db.execute('SELECT id FROM content_versions WHERE task_id=?',(row['task_id'],))}
            report_ids={r[0] for r in self.db.execute("SELECT run_id FROM growth_work WHERE task_id=? AND step='report'",(row['task_id'],))}
            item['feedback']=[dict(r) for r in self.db.execute('SELECT * FROM learning_feedback WHERE agent=?',(agent,)) if (r['target_kind']=='artifact' and r['target_id']==row['artifact_id']) or (r['target_kind']=='content' and r['target_id'] in content_ids) or (r['target_kind']=='report' and r['target_id'] in report_ids)]
            item['ratings']=[dict(r) for r in self.db.execute('SELECT target_kind,target_id,score,rubric,created_at FROM learning_ratings WHERE agent=?',(agent,)) if r['target_id'] in content_ids|report_ids|{row['artifact_id']}]
            item['reviews']=[dict(r) for r in self.db.execute('SELECT reviewer,verdict,note FROM reviews WHERE artifact_id=?',(row['artifact_id'],))]
            item['decisions']=[dict(r) for r in self.db.execute('SELECT actor,decision,created_at FROM approvals WHERE task_id=?',(row['task_id'],))]
            item['costs']=[dict(r) for r in self.db.execute('SELECT kind,mode,amount FROM office_costs WHERE task_id=?',(row['task_id'],))] if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='office_costs'").fetchone() else []
            item['errors']=[r[0] for r in self.db.execute('SELECT error_code FROM runs WHERE task_id=? AND error_code IS NOT NULL',(row['task_id'],))]
            consumers=task_consumers(self.db,row['task_id'])
            item['knowledge_refs']=[dict(r) for consumer in consumers for r in self.db.execute('SELECT knowledge_id,version,item_digest FROM learning_usages WHERE consumer=?',(consumer,))]
            item['knowledge_refs']=list({(r['knowledge_id'],r['version'],r['item_digest']):r for r in item['knowledge_refs']}.values())
            item['research_source_ids']=[r[0] for r in self.db.execute('SELECT s.id FROM campaign_work w JOIN research_sources s ON s.campaign_id=w.campaign_id WHERE w.task_id=?',(row['task_id'],))]
            item['content_revisions']=[dict(r) for r in self.db.execute('SELECT id,version,logical_key,status FROM content_versions WHERE task_id=? ORDER BY version',(row['task_id'],))]
            item['improvements']=[dict(r) for r in self.db.execute('SELECT id,status,category FROM learning_proposals WHERE task_kind=?',(row['task_kind'],))]
            result.append(item)
        return result

    def retention_plan(self,principal):
        require(principal,True)
        return {'expired_items':self.db.execute('SELECT COUNT(*) FROM learning_knowledge WHERE expires_at<=?',(now().isoformat(),)).fetchone()[0],
            'deleted':False,'human_approval_required':True,'method':'検索・再利用を停止し、参照履歴/根拠/保存義務/バックアップを確認後に別途削除計画。自動削除しない。'}


def task_consumers(db,task):
    result=[task]
    row=db.execute('SELECT payload FROM tasks WHERE id=?',(task,)).fetchone()
    if row:
        payload=json.loads(row[0])
        if isinstance(payload,dict) and payload.get('run_id') and payload.get('step'):
            result.append('growth:'+payload['run_id']+':'+payload['step'])
    return result


def validate_task_references(store,task):
    if not task or not exists(store.db):
        return
    learner=Learning(store,initialize=False)
    for consumer in task_consumers(store.db,task):
        for usage in store.db.execute('SELECT * FROM learning_usages WHERE consumer=?',(consumer,)):
            row=store.db.execute('SELECT * FROM learning_knowledge WHERE id=?',(usage['knowledge_id'],)).fetchone()
            if not row or not learner.valid(row) or row['version']!=usage['version'] or row['item_digest']!=usage['item_digest'] or usage['agent'] not in json.loads(row['scope']):
                raise ValueError('Referenced knowledge expired or changed; make a new draft')


def disable_style(learner,principal,identifier):
    require(principal,True)
    if learner.db.execute("UPDATE learning_proposals SET status='revoked' WHERE id=? AND status='applied'",(identifier,)).rowcount!=1:
        raise ValueError('Applied local style required')
    learner.audit(learner.actor(principal),'style_disabled',identifier)
