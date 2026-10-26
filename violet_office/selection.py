"""Explainable local recommendations. No permission changes or worker activation."""
import json
from uuid import uuid4
from .content import encode
from .store import now
from .integrations.adapters import require

CAPABILITIES={
 'planning':('AI02','AI06'), 'research':('AI03','AI04','AI05'),
 'sns':('AI09','AI08'), 'creative':('AI10','AI11','AI12'), 'schedule':('AI13',),
 'referral':('AI16','AI17','AI19'), 'referral_design':('AI15',), 'booking':('AI18','AI23'),
 'line':('AI22','AI21','AI24'), 'analysis':('AI27','AI06'),
 'management':('AI01','AI07','AI14','AI20','AI25'), 'integration':('AI26',),'audit':('AI28',)}
# All roles retained. Adjacent candidates are proposals, not interchangeable executors.
EVALUATION_KIND={'sns':'sns','referral':'referral','line':'line','booking':'booking','analysis':'report','audit':'report','management':'planning','research':'planning','planning':'planning','creative':'sns','schedule':'sns','integration':'planning','referral_design':'referral'}

class Selection:
    def __init__(self,store,learning):
        self.store,self.db,self.learning=store,store.db,learning

    def enabled(self):
        return bool(self.db.execute('SELECT enabled FROM selection_settings WHERE id=1').fetchone()[0])

    def set_enabled(self,principal,enabled):
        require(principal,True)
        if type(enabled) is not bool: raise ValueError('Boolean required')
        self.db.execute('UPDATE selection_settings SET enabled=? WHERE id=1',(int(enabled),))
        self.db.execute('INSERT INTO selection_events(actor,event,ref,detail,created_at) VALUES(?,?,?,?,?)',(principal.identity or 'representative','enabled' if enabled else 'paused','selection','{}',now().isoformat()))

    def propose(self,principal,key,kind,start,end):
        require(principal,True)
        if kind not in CAPABILITIES or not key or len(key)>120: raise ValueError('Known capability and request key required')
        def op():
            old=self.db.execute('SELECT * FROM selection_plans WHERE request_key=?',(key,)).fetchone()
            request=encode([kind,start,end,self.enabled()])
            if old:
                if json.loads(old['selection'])['request']!=request: raise ValueError('Conflicting request')
                return old['id']
            evaluation,data=self.learning.evaluate(principal,EVALUATION_KIND[kind],start,end)
            candidates=[]
            for agent in CAPABILITIES[kind]:
                metric=next(a for a in data['agents'] if a['agent']==agent)
                measured=all(metric[x] is not None for x in ('quality_score','quality_pass_percent','error_percent'))
                candidates.append({'agent':agent,'role':self.store.agents[agent]['role'],'metrics':metric,'comparable':measured})
            eligible=[a for a in candidates if a['comparable']]
            chosen=CAPABILITIES[kind][0]
            reason='データ不足のため既存の主担当を提案。実績による優劣は評価不能。'
            if self.enabled() and len(eligible)==len(candidates) and len(candidates)>1:
                ranked=sorted(eligible,key=lambda a:(-a['metrics']['quality_score'],-a['metrics']['quality_pass_percent'],a['metrics']['error_percent'],a['agent']))
                chosen=ranked[0]['agent']
                reason='同業務・同期間の候補に必要母数あり。品質評点→レビュー通過率→失敗率の順で提案。承認率のみで判断せず、事業効果は未評価。'
            elif self.enabled() and len(candidates)==1: reason='専門分野の担当が1人のため役割適合で提案。候補間の能力比較は行わない。'
            elif not self.enabled(): reason='自動選抜は停止中。既存の主担当を提案。'
            identifier=uuid4().hex
            self.db.execute('INSERT INTO selection_plans VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,key,kind,evaluation,encode({'request':request,'candidates':candidates,'period':[start,end]}),chosen,reason,'proposed',None,now().isoformat()))
            return identifier
        return self.store._transaction(op)

    def assign(self,principal,identifier,chosen):
        require(principal,True)
        def op():
            row=self.db.execute('SELECT * FROM selection_plans WHERE id=?',(identifier,)).fetchone()
            if not row or chosen not in CAPABILITIES[row['kind']]: raise ValueError('Candidate required')
            if row['task_id']:
                if row['chosen']==chosen: return row['task_id']
                old=self.db.execute('SELECT * FROM tasks WHERE id=?',(row['task_id'],)).fetchone()
                if old['status'] not in ('pending','paused') or old['attempts'] or self.db.execute('SELECT 1 FROM artifacts WHERE task_id=?',(old['id'],)).fetchone():
                    raise ValueError('Only unstarted tasks can be reassigned')
                self.db.execute("UPDATE tasks SET status='paused' WHERE id=?",(old['id'],))
                self.store._event(old['id'],'selection_reassigned_paused')
            task=self.store.create_task('selection:'+identifier+':'+chosen,chosen,{'selection_id':identifier,'kind':row['kind'],'mode':'manual_result_required','instruction':'承認済み範囲のローカル下書き・分析のみ。外部操作は禁止。'},action='management_decision')
            self.db.execute("UPDATE tasks SET status='pending' WHERE id=? AND status='paused' AND attempts=0",(task,))
            self.db.execute('INSERT INTO selection_events(actor,event,ref,detail,created_at) VALUES(?,?,?,?,?)',(principal.identity or 'representative','assigned',identifier,encode({'before':row['chosen'],'after':chosen,'task':task}),now().isoformat()))
            self.db.execute("UPDATE selection_plans SET chosen=?,status='assigned',task_id=? WHERE id=?",(chosen,task,identifier))
            return task
        return self.store._transaction(op)
