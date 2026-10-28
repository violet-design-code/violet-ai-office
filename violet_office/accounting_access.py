"""Opt-in accounting sandbox service. Not installed by Office or exposed over HTTP.

Only trusted server code may construct this service with an authenticated Principal.
Do not pass its results through legacy artifacts/logs; use issue()/receive() handles.
"""
from datetime import date, datetime, timezone
import json
from uuid import uuid4
from .portfolio import period, text
from .store import now

FIELDS = {
    'detail': {'day', 'kind', 'amount_yen'},
    'summary': {'revenue_yen', 'expense_yen', 'profit_yen'},
    'management': {'revenue_yen', 'expense_yen', 'profit_yen'},
    'improvement': {'expense_yen', 'profit_yen'},
    'advertising': {'budget_yen', 'spend_yen'},
    'sales': {'confirmed_net_yen', 'contact_count', 'proposal_count', 'contract_percent'},
    'terms': {'rate_percent', 'cap_yen'},
    'system': {'expense_yen', 'local_processing_seconds'},
    'audit': {'day', 'kind', 'amount_yen'},
}
ROLES = {'AI01': {'management'}, 'AI02': {'summary'}, 'AI06': {'improvement'},
         'AI07': {'advertising'}, 'AI14': {'sales'}, 'AI15': {'terms'},
         'AI25': {'system'}, 'AI27': {'detail', 'summary'}, 'AI28': {'audit'}}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS acct_policies(
 id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES portfolio_businesses(id),
 agent TEXT NOT NULL, section TEXT NOT NULL, fields TEXT NOT NULL,
 start_day TEXT NOT NULL, end_day TEXT NOT NULL, purpose TEXT NOT NULL,
 expires_at TEXT NOT NULL, requested_by TEXT NOT NULL REFERENCES office_users(username),
 approved_by TEXT REFERENCES office_users(username), status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS acct_sources(
 id TEXT PRIMARY KEY, business_id TEXT NOT NULL REFERENCES portfolio_businesses(id),
 section TEXT NOT NULL, day TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
 approved_by TEXT NOT NULL REFERENCES office_users(username));
CREATE TABLE IF NOT EXISTS acct_handles(
 id TEXT PRIMARY KEY, business_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id),
 policy_id TEXT NOT NULL REFERENCES acct_policies(id), section TEXT NOT NULL,
 fields TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS acct_events(
 id TEXT PRIMARY KEY, policy_id TEXT, action TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL);
'''


def initialize_sandbox(office, principal):
    """Explicit opt-in only. No migration number and no automatic startup hook."""
    office.portfolio.access(principal, owner=True)
    office.db.executescript(SCHEMA)


def expiry(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timezone-aware expiry required')
    return parsed.astimezone(timezone.utc)


class AccountingAccess:
    def __init__(self, office):
        self.o, self.db = office, office.db

    def owner(self, p, business=None):
        self.o.portfolio.access(p, business, owner=True)

    def event(self, p, policy, action):
        # No amounts, reasons, free text, source rows or attachment contents in logs.
        self.db.execute('INSERT INTO acct_events VALUES(?,?,?,?,?)',
                        (uuid4().hex, policy, action, p.identity, now().isoformat()))

    def propose(self, p, business, agent, section, fields, start, end, purpose, expires):
        self.owner(p, business)
        if agent not in self.o.store.agents or section not in ROLES.get(agent, set()):
            raise PermissionError('Role does not permit this accounting section')
        if not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields) or len(set(fields)) != len(fields) or not set(fields) <= FIELDS[section]:
            raise ValueError('Explicit allowlisted fields required')
        period(start, end)
        purpose = text(purpose, 300)
        if expiry(expires) <= now():
            raise ValueError('Future expiry required')
        identifier = uuid4().hex
        self.db.execute('INSERT INTO acct_policies VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?)',
                        (identifier, business, agent, section, json.dumps(sorted(fields)), start, end,
                         purpose, expires, p.identity, 'pending'))
        self.event(p, identifier, 'proposed')
        return identifier

    def decide(self, p, identifier, approve):
        row = self.db.execute('SELECT * FROM acct_policies WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise PermissionError('Unknown policy')
        self.owner(p, row['business_id'])
        if type(approve) is not bool or row['status'] != 'pending':
            raise ValueError('Pending policy and explicit decision required')
        if expiry(row['expires_at']) <= now():
            raise ValueError('Expired policy')
        self.db.execute('UPDATE acct_policies SET status=?,approved_by=? WHERE id=?',
                        ('approved' if approve else 'rejected', p.identity, identifier))
        self.event(p, identifier, 'approved' if approve else 'rejected')

    def revoke(self, p, identifier):
        row = self.db.execute('SELECT business_id FROM acct_policies WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise PermissionError('Unknown policy')
        self.owner(p, row[0])
        self.db.execute("UPDATE acct_policies SET status='revoked' WHERE id=?", (identifier,))
        self.event(p, identifier, 'revoked')

    def authorize(self, p, task, policy, section, fields, start, end):
        context = self.db.execute('SELECT t.agent,t.status,p.business_id FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE t.id=?', (task,)).fetchone()
        grant = self.db.execute('SELECT * FROM acct_policies WHERE id=?', (policy,)).fetchone()
        if not context or not grant:
            raise PermissionError('Mapped task and approved policy required')
        business, agent = context['business_id'], context['agent']
        self.o.portfolio.access(p, business)
        period(start, end)
        assignment = self.db.execute('SELECT active FROM bf_ai_assignments WHERE business_id=? AND agent=?', (business, agent)).fetchone()
        profile = self.db.execute('SELECT enabled FROM bf_profiles WHERE business_id=?', (business,)).fetchone()
        approver = self.db.execute('SELECT role FROM office_users WHERE username=?', (grant['approved_by'],)).fetchone()
        paused = any(self.db.execute('SELECT paused FROM '+table+' WHERE task_id=?', (task,)).fetchone()[0]
                     for table in ('wf_tasks', 's14_work') if self.db.execute('SELECT 1 FROM '+table+' WHERE task_id=?', (task,)).fetchone())
        if (context['status'] not in ('pending', 'running', 'review_pending', 'approval_pending') or paused
            or not assignment or not assignment[0] or not profile or not profile[0]
            or grant['business_id'] != business or grant['agent'] != agent
            or section != grant['section'] or section not in ROLES.get(agent, set())
            or grant['status'] != 'approved' or not approver or approver[0] != 'representative'
            or expiry(grant['expires_at']) <= now()
            or not fields or not set(fields) <= set(json.loads(grant['fields']))
            or not set(fields) <= FIELDS[section]
            or not grant['start_day'] <= start < end <= grant['end_day']):
            raise PermissionError('Accounting scope denied')
        return business

    def approved_source(self, p, business, section, day, field, value):
        """Explicit representative-confirmed structured facts, never guessed ledger tags."""
        self.owner(p, business)
        if section not in ('advertising', 'terms') or field not in FIELDS[section]:
            raise ValueError('Known structured source required')
        date.fromisoformat(day)
        if type(value) not in (int, float) or not 0 <= value < 10**12:
            raise ValueError('Bounded nonnegative numeric source required')
        if field == 'rate_percent' and value > 100:
            raise ValueError('Rate must be at most 100')
        self.db.execute('INSERT INTO acct_sources VALUES(?,?,?,?,?,?,?)',
                        (uuid4().hex, business, section, day, field, json.dumps(value), p.identity))

    def read(self, p, task, policy, section, fields, start, end):
        return self.o.store._transaction(lambda: self._read(p, task, policy, section, fields, start, end))

    def _read(self, p, task, policy, section, fields, start, end):
        business = self.authorize(p, task, policy, section, fields, start, end)
        if section in ('detail', 'audit'):
            # Evidence, personal information and arbitrary narrative are never returned.
            rows = self.db.execute("SELECT day,kind,amount_yen FROM portfolio_ledger WHERE business_id=? AND mode='actual' AND day>=? AND day<? ORDER BY day,id", (business, start, end)).fetchall()
            result = [{f: row[f] for f in fields} for row in rows]
        elif section in ('advertising', 'terms'):
            rows = self.db.execute('SELECT day,field,value FROM acct_sources WHERE business_id=? AND section=? AND day>=? AND day<? ORDER BY day,id', (business, section, start, end)).fetchall()
            result = [{'day': r['day'], 'field': r['field'], 'value': json.loads(r['value'])} for r in rows if r['field'] in fields]
            if not result:
                raise ValueError('No approved source; do not infer accounting fields')
        else:
            data = (self.o.sales.dashboard(p, business, start, end) if section == 'sales' else
                    self.o.portfolio.summary(p, business, start, end) if section == 'system' else
                    self.o.business_operations.summary(p, business, start, end))
            result = {f: data[f] for f in fields}
        self.event(p, policy, 'read')
        return {'business_id': business, 'section': section, 'period': [start, end],
                'data': result, 'source': 'legacy_actual_ledger' if section in ('detail', 'audit') else 'approved_structured_facts' if section in ('terms', 'advertising') else 'existing_manual_aggregate',
                'limitations': 'No bank reconciliation; missing values remain null; no external transmission.'}

    def issue(self, p, task, policy, section, fields, start, end):
        return self.o.store._transaction(lambda: self._issue(p, task, policy, section, fields, start, end))

    def _issue(self, p, task, policy, section, fields, start, end):
        business = self.authorize(p, task, policy, section, fields, start, end)
        handle = uuid4().hex
        self.db.execute('INSERT INTO acct_handles VALUES(?,?,?,?,?,?,?,?)',
                        (handle, business, task, policy, section, json.dumps(sorted(fields)), start, end))
        return handle  # No copied values, narrative, attachments or serialized legacy artifact.

    def receive(self, p, handle, target_task, target_policy):
        return self.o.store._transaction(lambda: self._receive(p, handle, target_task, target_policy))

    def _receive(self, p, handle, target_task, target_policy):
        row = self.db.execute('SELECT * FROM acct_handles WHERE id=?', (handle,)).fetchone()
        if not row:
            raise PermissionError('Unknown accounting handle')
        fields = json.loads(row['fields'])
        self.authorize(p, row['task_id'], row['policy_id'], row['section'], fields, row['start_day'], row['end_day'])
        business = self.authorize(p, target_task, target_policy, row['section'], fields, row['start_day'], row['end_day'])
        if business != row['business_id']:
            raise PermissionError('Cross-business handoff denied')
        return self.read(p, target_task, target_policy, row['section'], fields, row['start_day'], row['end_day'])
