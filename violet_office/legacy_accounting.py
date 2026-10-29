"""Fail-closed bridge for legacy AI accounting reads; no policy provisioning."""
import json
import sqlite3
from .accounting_access import AccountingAccess, FIELDS, expiry
from .business_foundation import ai_allowed
from .portfolio import period
from .store import now

# Detail and audit are deliberately absent: use explicit dedicated AccountingAccess policies.
LEGACY_SECTIONS = {'AI01':'management','AI02':'summary','AI06':'improvement',
                   'AI07':'advertising','AI14':'sales','AI15':'terms',
                   'AI25':'system','AI27':'summary'}
VERSION = 'accounting-access-v1'
REQUIRED = {'acct_policies','acct_sources','acct_handles','acct_events','acct_task_reads','acct_task_policies'}


def require_schema(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not REQUIRED <= tables:
        raise PermissionError('Accounting policy service unavailable')


def deny_generic_content(store, task):
    """Do not serialize a task that has consumed accounting data into legacy artifacts."""
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='acct_task_reads'").fetchone():
        if store.db.execute('SELECT 1 FROM acct_task_reads WHERE task_id=?',(task,)).fetchone():
            raise PermissionError('Accounting task requires dedicated authorized references')


class LegacyAccounting:
    def __init__(self, office):
        self.o, self.db = office, office.db

    def read(self, principal, task, start, end, *, business=None, agent=None, require_work=False):
        # Keep authorization, policy choice, read and classification in one transaction.
        try:
            return self.o.store._transaction(lambda: self._read(principal,task,start,end,business,agent,require_work))
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            # Never echo source content, policy text, SQL or malformed input.
            raise PermissionError('Accounting access denied') from None

    def _read(self, p, task, start, end, expected_business, expected_agent, require_work):
        row = self.db.execute('SELECT t.agent,p.business_id FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE t.id=?',(task,)).fetchone()
        if not row:
            raise PermissionError('Authenticated mapped task required')
        b, a = row['business_id'], row['agent']
        self.o.portfolio.access(p,b)
        section = LEGACY_SECTIONS.get(a)
        if not section or (expected_business is not None and expected_business != b) or (expected_agent is not None and expected_agent != a):
            raise PermissionError('Accounting scope denied')
        if not ai_allowed(self.o.store,a,b,'finance_read') or (require_work and not ai_allowed(self.o.store,a,b,'work')):
            raise PermissionError('Accounting scope denied')
        period(start,end)
        require_schema(self.db)
        # Caller cannot pick a wider section, spoof agent, or choose between conflicting grants.
        rows = self.db.execute("SELECT p.* FROM acct_policies p JOIN acct_task_policies t ON t.policy_id=p.id JOIN office_users u ON u.username=t.approved_by WHERE t.task_id=? AND u.role='representative' AND p.business_id=? AND p.agent=? AND p.section=? AND p.status='approved' AND p.start_day<=? AND p.end_day>=?",(task,b,a,section,start,end)).fetchall()
        valid = [r for r in rows if expiry(r['expires_at']) > now()]
        if len(valid) != 1:
            raise PermissionError('One current approved accounting policy required')
        grant = valid[0]
        fields = json.loads(grant['fields'])
        if not isinstance(fields,list) or not fields or not all(isinstance(f,str) for f in fields) or len(set(fields)) != len(fields) or not set(fields) <= FIELDS[section]:
            raise PermissionError('Accounting policy fields denied')
        data = AccountingAccess(self.o).read(p,task,grant['id'],section,fields,start,end)
        return {'version':VERSION, 'task_id':task, 'agent':a, **data}
