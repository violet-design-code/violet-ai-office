"""Transactional storage; no model calls, external integrations or sender exist here.

Only anonymized, non-secret material belongs in payloads. Authentication of the
human representative must be supplied by a future trusted application boundary.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .registry import load_agents

ACTIONS = {'internal', 'sns_post', 'line_send', 'customer_contact', 'booking_confirm', 'contract_guidance', 'management_decision'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 agent TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', max_attempts INTEGER NOT NULL,
 timeout_seconds INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), status TEXT NOT NULL,
 deadline TEXT NOT NULL, error_code TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON runs(task_id) WHERE status='running';
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), author TEXT NOT NULL, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), reviewer TEXT NOT NULL,
 verdict TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 decision TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), event TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, instruction TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS request_tasks (
 request_id TEXT NOT NULL REFERENCES requests(id), task_id TEXT UNIQUE NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL, PRIMARY KEY(request_id,task_id));
CREATE TABLE IF NOT EXISTS request_options (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 5));
CREATE TABLE IF NOT EXISTS dependencies (
 task_id TEXT NOT NULL REFERENCES tasks(id), prerequisite TEXT NOT NULL REFERENCES tasks(id),
 PRIMARY KEY(task_id,prerequisite), CHECK(task_id != prerequisite));
CREATE TABLE IF NOT EXISTS budget (
 id INTEGER PRIMARY KEY CHECK(id=1), limit_microusd INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reservations (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), reserved INTEGER NOT NULL, actual INTEGER);
'''


def now():
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path, budget_microusd=0):
        if type(budget_microusd) is not int or budget_microusd < 0:
            raise ValueError('Invalid budget')
        self.agents = load_agents()
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        self.db.execute('INSERT OR IGNORE INTO budget VALUES (1, ?)', (budget_microusd,))

    def close(self):
        self.db.close()

    def _event(self, task, event):
        self.db.execute('INSERT INTO events(task_id,event,created_at) VALUES (?,?,?)', (task, event, now().isoformat()))

    def _transaction(self, fn):
        if self.db.in_transaction:
            return fn()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            result = fn()
            self.db.execute('COMMIT')
            return result
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def create_task(self, dedupe_key, agent, payload, action='internal', max_attempts=3, timeout_seconds=60):
        if agent not in self.agents or action not in ACTIONS or not dedupe_key:
            raise ValueError('Invalid task')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5 or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError('Invalid execution limits')
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(json.dumps([agent, action, encoded, max_attempts, timeout_seconds]).encode()).hexdigest()
        def op():
            old = self.db.execute('SELECT * FROM tasks WHERE dedupe_key=?', (dedupe_key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('Idempotency key reused with different task')
                return old['id']
            task = uuid4().hex
            self.db.execute('INSERT INTO tasks(id,dedupe_key,fingerprint,agent,action,payload,max_attempts,timeout_seconds) VALUES (?,?,?,?,?,?,?,?)', (task,dedupe_key,fingerprint,agent,action,encoded,max_attempts,timeout_seconds))
            self._event(task, 'created')
            return task
        return self._transaction(op)

    def claim(self, task, reserve_microusd=0):
        """Claim a local drafting operation, never authorization for external effects."""
        if type(reserve_microusd) is not int or reserve_microusd < 0:
            raise ValueError('Invalid reservation')
        def op():
            row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
            if not row or row['status'] != 'pending' or row['attempts'] >= row['max_attempts']:
                raise ValueError('Task unavailable')
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='growth_proposals'").fetchone():
                proposal=self.db.execute('SELECT status FROM growth_proposals WHERE task_id=?',(task,)).fetchone()
                if proposal and proposal[0]!='approved_local':
                    raise ValueError('Business plan requires representative approval')
            blocked = self.db.execute(
                "SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.prerequisite WHERE d.task_id=? AND t.status NOT IN ('completed','approval_pending','manual_execution_pending') LIMIT 1",
                (task,)).fetchone()
            if blocked:
                raise ValueError('Dependencies require passed review')
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='wf_tasks'").fetchone():
                paused=self.db.execute('SELECT paused FROM wf_tasks WHERE task_id=?',(task,)).fetchone()
                if paused and paused[0]: raise ValueError('Workflow task is paused')
            from .portfolio import guard_claim
            guard_claim(self,task,reserve_microusd)
            from .development import guard_claim as guard_development
            guard_development(self,task,reserve_microusd)
            used = self.db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM reservations').fetchone()[0]
            limit = self.db.execute('SELECT limit_microusd FROM budget WHERE id=1').fetchone()[0]
            if used + reserve_microusd > limit:
                raise ValueError('Budget exceeded')
            # Additive office accounting guard; older non-office DBs keep existing behavior.
            office_month = None
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='office_budgets'").fetchone():
                from zoneinfo import ZoneInfo
                office_month = now().astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y-%m')
                cap = self.db.execute('SELECT limit_microusd FROM office_budgets WHERE month=?',(office_month,)).fetchone()
                monthly_reserved = self.db.execute('SELECT COALESCE(SUM(amount),0) FROM office_month_reservations WHERE month=?',(office_month,)).fetchone()[0]
                accounted = self.db.execute("SELECT COALESCE(SUM(amount),0) FROM office_costs WHERE substr(incurred_date,1,7)=? AND mode='manual'",(office_month,)).fetchone()[0]
                if reserve_microusd > 0 and monthly_reserved + accounted + reserve_microusd > (cap[0] if cap else 0):
                    raise ValueError('Monthly budget exceeded')
            run = uuid4().hex
            deadline = (now() + timedelta(seconds=row['timeout_seconds'])).isoformat()
            self.db.execute('INSERT INTO runs VALUES (?,?,?, ?,NULL)', (run,task,'running',deadline))
            self.db.execute('INSERT INTO reservations VALUES (?,?,NULL)', (run,reserve_microusd))
            if office_month:
                self.db.execute('INSERT INTO office_month_reservations VALUES (?,?,?)',(run,office_month,reserve_microusd))
            self.db.execute("UPDATE tasks SET status='running', attempts=attempts+1 WHERE id=?", (task,))
            self._event(task, 'claimed')
            return run
        return self._transaction(op)

    def _active(self, run):
        row = self.db.execute("SELECT r.*,t.agent,t.attempts,t.max_attempts FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.id=? AND r.status='running'", (run,)).fetchone()
        if not row:
            raise ValueError('Run unavailable')
        return row

    def finish(self, run, content):
        def op():
            row = self._active(run)
            if row['deadline'] <= now().isoformat():
                raise ValueError('Run expired; expire it before retrying')
            if self.db.execute("SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.prerequisite WHERE d.task_id=? AND t.status NOT IN ('completed','approval_pending','manual_execution_pending') LIMIT 1",(row['task_id'],)).fetchone():
                raise ValueError('A prerequisite is no longer accepted')
            artifact = uuid4().hex
            self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?)', (artifact,row['task_id'],row['agent'],content))
            self.db.execute("UPDATE runs SET status='completed' WHERE id=?", (run,))
            self.db.execute("UPDATE tasks SET status='review_pending' WHERE id=?", (row['task_id'],))
            self._event(row['task_id'], 'artifact_created')
            from .learning import record_artifact
            task=self.db.execute('SELECT * FROM tasks WHERE id=?',(row['task_id'],)).fetchone()
            record_artifact(self.db,task,artifact,self.agents[task['agent']]['role'],now().isoformat())
            return artifact
        return self._transaction(op)

    def fail(self, run, code, retryable=False):
        # Persist only enumerated error codes, never raw provider messages or credentials.
        if code not in {'timeout','rate_limit','unavailable','invalid_input','internal'}:
            raise ValueError('Unsupported error code')
        def op():
            row = self._active(run)
            status = 'pending' if retryable and row['attempts'] < row['max_attempts'] else 'failed'
            self.db.execute("UPDATE runs SET status='failed',error_code=? WHERE id=?", (code,run))
            self.db.execute('UPDATE tasks SET status=? WHERE id=?', (status,row['task_id']))
            self._event(row['task_id'], 'run_failed:'+code)
        self._transaction(op)

    def expire(self):
        # Do not re-execute a timed-out external call: phase one has no such calls.
        def op():
            rows = self.db.execute("SELECT id FROM runs WHERE status='running' AND deadline<=?", (now().isoformat(),)).fetchall()
            for row in rows:
                self.fail(row['id'], 'timeout', retryable=True)
            return len(rows)
        return self._transaction(op)

    def review(self, artifact, reviewer, verdict, note=''):
        if verdict not in {'pass','changes_requested'}:
            raise ValueError('Invalid verdict')
        def op():
            row = self.db.execute('SELECT a.*, t.status FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE a.id=?', (artifact,)).fetchone()
            if not row or row['status'] != 'review_pending' or reviewer != self.agents[row['author']]['reviewer']:
                raise ValueError('Independent assigned reviewer required')
            self.db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)', (uuid4().hex,artifact,reviewer,verdict,note))
            task = self.db.execute('SELECT action FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
            status = ('completed' if task['action']=='internal' else 'approval_pending') if verdict=='pass' else 'changes_requested'
            self.db.execute('UPDATE tasks SET status=? WHERE id=?', (status,row['task_id']))
            self._event(row['task_id'], 'review:'+verdict)
        self._transaction(op)

    def decide(self, artifact, decision, actor):
        """Trusted local representative API; actor is not remote authentication."""
        if actor != 'representative' or decision not in {'approve','reject'}:
            raise ValueError('Representative decision required')
        def op():
            row = self.db.execute('SELECT a.task_id,t.status FROM artifacts a JOIN tasks t ON t.id=a.task_id WHERE a.id=?', (artifact,)).fetchone()
            if not row or row['status'] != 'approval_pending':
                raise ValueError('Reviewed artifact required')
            if decision == 'approve' and self.db.execute(
                "SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.prerequisite WHERE d.task_id=? AND t.status NOT IN ('completed','approval_pending','manual_execution_pending') LIMIT 1",
                (row['task_id'],)).fetchone():
                raise ValueError('A prerequisite is no longer accepted')
            self.db.execute('INSERT INTO approvals VALUES (?,?,?,?,?,?)', (uuid4().hex,row['task_id'],artifact,decision,actor,now().isoformat()))
            status = 'manual_execution_pending' if decision=='approve' else 'rejected'
            self.db.execute('UPDATE tasks SET status=? WHERE id=?', (status,row['task_id']))
            self._event(row['task_id'], 'representative:'+decision)
        self._transaction(op)

    def summary(self):
        return dict(self.db.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status').fetchall())
