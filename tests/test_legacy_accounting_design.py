"""Design-only legacy bridge prototype. NOT imported by the application.

Historical prototype checks remain; the original exposure case now verifies the connected fix.
Actual route and HTTP coverage also lives in test_legacy_accounting_enforcement.py.
"""
from datetime import timedelta
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tests import test_accounting_access as fixtures
from violet_office.accounting_access import AccountingAccess, FIELDS
from violet_office.business_foundation import ai_allowed
from violet_office.store import now

ROUTES = ('read_for_ai.finance_read', 'read_for_ai.kpi_read', 'business-task-kpis')
SECTIONS = {'AI01':'management','AI02':'summary','AI06':'improvement',
            'AI07':'advertising','AI14':'sales','AI15':'terms','AI25':'system',
            'AI27':'summary'}


def proposed_bridge(o, principal, route, task, policy, section, fields, start, end):
    """Test-local proposal: use approved projections, NEVER filter an old response."""
    if route not in ROUTES:
        raise ValueError('Explicit known route required')
    context = o.db.execute('SELECT t.agent,p.business_id FROM tasks t JOIN portfolio_tasks p ON p.task_id=t.id WHERE t.id=?',(task,)).fetchone()
    if not context or SECTIONS.get(context['agent']) != section:
        raise PermissionError('No approved role projection for this route')
    # Keep coarse legacy restrictions; fine-grained policy cannot widen them.
    if not ai_allowed(o.store,context['agent'],context['business_id'],'finance_read'):
        raise PermissionError('Legacy finance gate denied')
    if route == 'business-task-kpis' and not ai_allowed(o.store,context['agent'],context['business_id'],'work'):
        raise PermissionError('Legacy work gate denied')
    return AccountingAccess(o).read(principal,task,policy,section,fields,start,end)


class LegacyProtectionDesignTests(unittest.TestCase):
    setUp = fixtures.AccountingTests.setUp
    tearDown = fixtures.AccountingTests.tearDown
    snapshot = fixtures.AccountingTests.snapshot
    policy = fixtures.AccountingTests.policy

    def enable_synthetic_legacy_finance(self):
        self.st.db.execute('UPDATE bf_ai_assignments SET permissions=?',
                           (json.dumps(['business_read','work','finance_read']),))

    def bridge(self, agent, policy, section, fields=None, route=ROUTES[0], b='BIZ-A'):
        return proposed_bridge(self.o,self.owner,route,self.tasks[b,agent],policy,section,
                               fields or sorted(FIELDS[section]),self.start,self.end)

    def test_original_ai04_exposure_is_now_blocked_on_real_paths(self):
        self.enable_synthetic_legacy_finance()
        for resource in ('finance_read','kpi_read'):
            with self.assertRaises(PermissionError):
                self.o.business_foundation.read_for_ai(self.owner,'BIZ-A','AI04',resource,self.start,self.end,task=self.tasks['BIZ-A','AI04'])
        with self.assertRaises(PermissionError):
            self.o.business_operations.for_task(self.owner,self.tasks['BIZ-A','AI04'],self.start,self.end)

    def test_all_28_roles_all_three_proposed_routes(self):
        self.enable_synthetic_legacy_finance()
        before=self.snapshot()
        for agent in self.st.agents:
            section=SECTIONS.get(agent)
            policy=self.policy(agent,section) if section else None
            if section in ('advertising','terms'):
                for f in FIELDS[section]:self.s.approved_source(self.owner,'BIZ-A',section,'2026-01-05',f,10)
            for route in ROUTES:
                with self.subTest(agent=agent,route=route):
                    if section:
                        result=self.bridge(agent,policy,section,route=route)
                        self.assertEqual(result['business_id'],'BIZ-A')
                        if isinstance(result['data'],dict):self.assertEqual(set(result['data']),FIELDS[section])
                        self.assertNotIn('合成秘密明細本文',json.dumps(result,ensure_ascii=False))
                    else:
                        with self.assertRaises(PermissionError):self.bridge(agent,'missing','summary',route=route)
        self.assertEqual(before,self.snapshot())

    def test_ai04_denied_before_any_accounting_query_in_proposal(self):
        self.enable_synthetic_legacy_finance()
        for route in ROUTES:
            with patch.object(AccountingAccess,'read',side_effect=AssertionError('must not query')):
                with self.assertRaises(PermissionError):self.bridge('AI04','missing','summary',route=route)

    def test_marketing_sales_improvement_cannot_request_details_or_excess_fields(self):
        self.enable_synthetic_legacy_finance()
        for agent in ('AI01','AI02','AI06','AI07','AI14','AI15','AI25'):
            section=SECTIONS[agent];p=self.policy(agent,section)
            for route in ROUTES:
                with self.subTest(agent=agent,route=route):
                    with self.assertRaises(PermissionError):self.bridge(agent,p,'detail',route=route)
                    with self.assertRaises(PermissionError):self.bridge(agent,p,section,['evidence'],route)
        # Broad legacy KPI access must not become an unfiltered KPI response.
        p=self.policy('AI07','advertising')
        with self.assertRaises(PermissionError):self.bridge('AI07',p,'advertising',['profit_yen'],ROUTES[1])

    def test_missing_expired_cross_business_policy_and_no_fallback(self):
        self.enable_synthetic_legacy_finance()
        for route in ROUTES:
            p=self.policy('AI02','summary',approve=False)
            with self.assertRaises(PermissionError):self.bridge('AI02',p,'summary',route=route)
            p=self.policy('AI02','summary',b='BIZ-B')
            with self.assertRaises(PermissionError):self.bridge('AI02',p,'summary',route=route)
            p=self.policy('AI02','summary')
            self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',((now()-timedelta(seconds=1)).isoformat(),p))
            with self.assertRaises(PermissionError):self.bridge('AI02',p,'summary',route=route)
        p=self.policy('AI07','advertising')
        with self.assertRaises(ValueError):self.bridge('AI07',p,'advertising')

    def test_legacy_business_read_and_representative_summary_continue(self):
        self.enable_synthetic_legacy_finance()
        before=self.snapshot()
        self.assertEqual(self.o.business_foundation.read_for_ai(self.owner,'BIZ-A','AI04')['business_id'],'BIZ-A')
        self.assertIn('revenue_yen',self.o.business_operations.summary(self.owner,'BIZ-A',self.start,self.end))
        self.assertEqual(before,self.snapshot())

    def test_proposal_does_not_widen_existing_work_or_finance_denials(self):
        p=self.policy('AI02','summary')
        with self.assertRaises(PermissionError):self.bridge('AI02',p,'summary')
        self.enable_synthetic_legacy_finance()
        self.st.db.execute("UPDATE bf_ai_assignments SET permissions=? WHERE business_id='BIZ-A' AND agent='AI02'",(json.dumps(['business_read','finance_read']),))
        with self.assertRaises(PermissionError):self.bridge('AI02',p,'summary',route=ROUTES[2])

    def test_synthetic_repair_permission_matrix_all_28_and_active_unchanged(self):
        self.enable_synthetic_legacy_finance()
        memory=sqlite3.connect(':memory:')
        memory.row_factory=sqlite3.Row
        try:
            self.st.db.backup(memory)
            seven={'AI01','AI02','AI04','AI06','AI07','AI25','AI27'}
            for agent in self.st.agents:
                permissions=['business_read','work']+(['finance_read'] if agent in seven else [])
                memory.execute("UPDATE bf_ai_assignments SET permissions=?,active=? WHERE business_id='BIZ-A' AND agent=?",
                               (json.dumps(permissions),int(agent!='AI03'),agent))
            memory.execute("DELETE FROM bf_profiles WHERE business_id='BIZ-A'")
            store=SimpleNamespace(db=memory,agents=self.st.agents)
            before_rows=[tuple(r) for r in memory.execute('SELECT * FROM bf_ai_assignments ORDER BY business_id,agent')]
            def matrix():
                return {(a,p,s):ai_allowed(store,a,'BIZ-A',p,strict=s) for a in store.agents
                        for p in ('business_read','work','finance_read') for s in (True,False)}
            before=matrix()
            memory.execute("INSERT INTO bf_profiles VALUES('BIZ-A','合成','合成',NULL,1,1,1)")
            after=matrix()
            self.assertEqual(len(before),168)
            for agent in store.agents:
                for permission in ('business_read','work','finance_read'):
                    self.assertFalse(before[agent,permission,True])
                    expected=agent!='AI03' and (permission!='finance_read' or agent in seven)
                    self.assertEqual(after[agent,permission,True],expected)
                    self.assertTrue(before[agent,permission,False])
                    self.assertEqual(after[agent,permission,False],expected)
            self.assertEqual(before_rows,[tuple(r) for r in memory.execute('SELECT * FROM bf_ai_assignments ORDER BY business_id,agent')])
        finally:memory.close()
