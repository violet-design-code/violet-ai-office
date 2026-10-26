"""Offline AI01 coordination. Rules select work; humans supply specialist results.

No LLM, external sender, implicit reviewer or background worker is implemented.
"""
import json
from pathlib import Path
from uuid import uuid4

from .registry import DEFAULT_CONFIG
from .store import now

ROUTING = DEFAULT_CONFIG.with_name('routing.json')


class Coordinator:
    def __init__(self, store, routing=ROUTING):
        self.store = store
        self.db = store.db
        self.rules = json.loads(Path(routing).read_text())
        if self.rules['version'] != 1:
            raise ValueError('Unsupported routing version')
        ids = []
        for rule in self.rules['rules']:
            if rule['agent'] not in store.agents or rule['agent'] == 'AI01' or not rule['keywords'] or any(not isinstance(k, str) or not k.strip() for k in rule['keywords']):
                raise ValueError('Invalid routing rule')
            ids.append(rule['agent'])
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate routing agent')

    def analyze(self, instruction):
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
            raise ValueError('Instruction must contain 1 to 4000 characters')
        text = instruction.casefold()
        selected = []
        for rule in self.rules['rules']:
            matches = [k for k in rule['keywords'] if k.casefold() in text]
            if matches:
                selected.append({'agent':rule['agent'], 'reason':matches, 'category':rule['category'], 'role':self.store.agents[rule['agent']]['role']})
        return {'method':'keyword_rules_v1','status':'planned' if selected else 'clarification_required', 'assignments':selected}

    @staticmethod
    def _action(agent):
        if agent in {'AI02','AI06','AI07','AI14','AI20','AI25'}:
            return 'management_decision'
        if agent in {'AI08','AI09','AI10','AI11','AI12','AI13'}:
            return 'sns_post'
        if agent in {'AI21','AI22','AI24'}:
            return 'line_send'
        if agent == 'AI23':
            return 'booking_confirm'
        if agent == 'AI15':
            return 'contract_guidance'
        if agent in {'AI16','AI17','AI18','AI19'}:
            return 'customer_contact'
        return 'internal'

    def submit(self, key, instruction, priority=3):
        if type(priority) is not int or not 1 <= priority <= 5:
            raise ValueError('Priority must be 1 (highest) to 5')
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValueError('Request key required (max 200 characters)')
        plan = self.analyze(instruction)
        def op():
            old = self.db.execute('SELECT * FROM requests WHERE request_key=?', (key,)).fetchone()
            if old:
                if old['instruction'] != instruction or self.db.execute('SELECT priority FROM request_options WHERE request_id=?',(old['id'],)).fetchone()[0] != priority:
                    raise ValueError('Request key reused with different instruction')
                return old['id']
            request = uuid4().hex
            self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?)', (request,key,instruction,plan['status'],now().isoformat()))
            self.db.execute('INSERT INTO request_options VALUES (?,?)',(request,priority))
            children = []
            for assignment in plan['assignments']:
                agent = assignment['agent']
                task = self.store.create_task(f'request:{request}:{agent}',agent,{
                    'instruction':instruction, 'matched_keywords':assignment['reason'], 'category':assignment['category'],
                    'deliverable':self.store.agents[agent]['outputs'],
                    'execution_mode':'manual_result_required',
                }, action=self._action(agent))
                self.db.execute('INSERT INTO request_tasks VALUES (?,?,?)',(request,task,'specialist'))
                children.append(task)
            if children:
                # Aggregate is also a draft, never a way to bypass external approval.
                external = any(self._action(a['agent']) != 'internal' for a in plan['assignments'])
                task = self.store.create_task(f'request:{request}:AI01','AI01',{'request_id':request,'purpose':'reviewed_artifact_collection'},action='customer_contact' if external else 'internal')
                self.db.execute('INSERT INTO request_tasks VALUES (?,?,?)',(request,task,'aggregate'))
                self.db.executemany('INSERT INTO dependencies VALUES (?,?)',[(task,child) for child in children])
            return request
        return self.store._transaction(op)

    def report(self, request):
        row = self.db.execute('SELECT * FROM requests WHERE id=?',(request,)).fetchone()
        if not row:
            raise ValueError('Unknown request')
        tasks = self.db.execute('''SELECT t.id,t.agent,t.status,t.action,rt.kind,
            a.id AS artifact_id,a.content FROM request_tasks rt JOIN tasks t ON t.id=rt.task_id
            LEFT JOIN artifacts a ON a.task_id=t.id WHERE rt.request_id=? ORDER BY rt.kind,t.agent''',(request,)).fetchall()
        result = [dict(t) for t in tasks]
        specialists = [t for t in result if t['kind']=='specialist']
        aggregate = next((t for t in result if t['kind']=='aggregate'),None)
        if row['status']=='clarification_required':
            status='clarification_required'
        elif any(t['status'] in {'failed','changes_requested','rejected'} for t in result):
            status='blocked'
        elif aggregate['status'] in {'completed','manual_execution_pending'}:
            status='collected' if aggregate['status']=='completed' else 'manual_execution_pending'
        elif aggregate['status'] in {'review_pending','approval_pending'}:
            status=aggregate['status']
        elif all(t['status'] in {'completed','approval_pending','manual_execution_pending'} for t in specialists):
            status='ready_to_collect'
        else:
            status='in_progress'
        labels={'clarification_required':'要確認','blocked':'対応が必要','collected':'集約・レビュー完了','manual_execution_pending':'代表承認済み・手動実行待ち','review_pending':'集約結果のレビュー待ち','approval_pending':'集約結果の代表承認待ち','ready_to_collect':'集約可能','in_progress':'担当作業・レビュー待ち'}
        priority=self.db.execute('SELECT priority FROM request_options WHERE request_id=?',(request,)).fetchone()[0]
        return {'request_id':request,'status':status,'priority':priority,
            'report_ja':f"状況: {labels[status]}。担当作業 {len(specialists)}件。レビュー通過 {sum(t['status'] in {'completed','approval_pending','manual_execution_pending'} for t in specialists)}件。外部操作は未実行。",
            'method':'keyword_rules_v1','tasks':result}

    def collect(self, request):
        def op():
            report = self.report(request)
            aggregate = next((t for t in report['tasks'] if t['kind']=='aggregate'), None)
            if aggregate and aggregate['artifact_id']:
                if report['status']=='blocked':
                    raise ValueError('Request blocked')
                return aggregate['artifact_id']
            if report['status'] != 'ready_to_collect':
                raise ValueError('All specialist artifacts must pass review')
            content = json.dumps({'type':'reviewed_artifact_collection','request_id':request,
                'items':[{'task_id':t['id'],'agent':t['agent'],'artifact_id':t['artifact_id'],'content':t['content']} for t in report['tasks'] if t['kind']=='specialist'],
                'notice':'参照付き集約。生成AIによる要約・法令適合保証・外部実行ではありません。'},ensure_ascii=False)
            run = self.store.claim(aggregate['id'])
            artifact = self.store.finish(run,content)
            self.store._event(aggregate['id'],'collected')
            return artifact
        return self.store._transaction(op)

    def queue(self):
        """Pending local work, highest request priority first. No automatic execution."""
        rows=self.db.execute("""SELECT t.id,t.agent,t.action,o.priority,rt.request_id FROM tasks t
            JOIN request_tasks rt ON rt.task_id=t.id JOIN request_options o ON o.request_id=rt.request_id
            JOIN requests r ON r.id=rt.request_id WHERE t.status='pending'
            AND NOT EXISTS (SELECT 1 FROM dependencies d JOIN tasks p ON p.id=d.prerequisite
                WHERE d.task_id=t.id AND p.status NOT IN ('completed','approval_pending','manual_execution_pending'))
            ORDER BY o.priority,r.created_at,t.agent""").fetchall()
        return [dict(row) for row in rows]
