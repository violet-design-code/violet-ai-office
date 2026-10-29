"""Recovery design experiments ONLY, on synthetic databases.

The gate/witness below are test models, NOT an application protection, restore
command, production journal, or durable multi-process implementation.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from uuid import uuid4

from tests import test_accounting_access as fixtures
from tests import test_accounting_policy_transitions as transitions
from violet_office.accounting_access import AccountingAccess, FIELDS, ROLES
from violet_office.integrations.adapters import Principal
from violet_office.office import Office
from violet_office.repair_trial import snapshot
from violet_office.store import Store, now


def digest(value):
    return hashlib.sha256(repr(value).encode('utf-8')).hexdigest()


def events(db):
    return tuple(tuple(r) for r in db.execute('SELECT * FROM acct_events ORDER BY rowid'))


def policies(db):
    return tuple(tuple(r) for r in db.execute('SELECT * FROM acct_policies ORDER BY id'))


@dataclass(frozen=True)
class Witness:
    """A test fixture standing for a fresh authenticated external checkpoint.

    The digest is pinned outside BOTH SQLite images. This is NOT cryptographic
    authentication, a durable anchor, an anti-rollback server, or proof of freshness.
    Production must implement those trust boundaries independently.
    """
    audit: tuple
    grants: tuple
    available: bool = True

    @property
    def head(self):
        return digest((self.audit, self.grants))

    @classmethod
    def capture(cls, db):
        return cls(events(db), policies(db))

    def verify(self, pinned_head):
        if not self.available or self.head != pinned_head:
            raise PermissionError('Recovery evidence unavailable')
        # Column layout is validated by the schema-manifest check separately.
        successful = {}
        for row in self.audit:
            if row[2] in ('proposed', 'approved', 'rejected', 'revoked'):
                prior = successful.get(row[1])
                target = {'proposed': 'pending'}.get(row[2], row[2])
                valid = ((target == 'pending' and prior is None) or
                         (target in ('approved', 'rejected') and prior == 'pending') or
                         (target == 'revoked' and prior in ('pending', 'approved')))
                if not valid:
                    raise PermissionError('Recovery evidence inconsistent')
                successful[row[1]] = target
        if len({r[0] for r in self.audit}) != len(self.audit):
            raise PermissionError('Recovery evidence inconsistent')
        for row in self.grants:
            if successful.get(row[0]) != row[-1]:
                raise PermissionError('Recovery evidence inconsistent')


def verify_staging(db, expected_snapshot, expected_version):
    """Verify a test copy against a separately captured manifest; no repair."""
    if (db.execute('PRAGMA foreign_keys').fetchone()[0] != 1
            or [r[0] for r in db.execute('PRAGMA integrity_check')] != ['ok']
            or db.execute('PRAGMA foreign_key_check').fetchall()
            or db.execute('PRAGMA user_version').fetchone()[0] != expected_version
            or snapshot(db) != expected_snapshot):
        raise PermissionError('Recovery manifest mismatch')


class RecoveryGateModel:
    """Test-only default-deny recovery epoch; never imported by Office/Web.

    All policies in the restored snapshot remain quarantined even after evidence
    reconciliation. New policies need independent per-task scope receipts. Ordinary
    DB authorization is always rechecked, not replaced by the receipt.
    """
    def __init__(self, service, witness, pinned_head):
        self.service = service
        self.witness = witness
        self.pinned_head = pinned_head
        self.epoch = uuid4().hex
        self.baseline = frozenset(r[0] for r in policies(service.db))
        self.reconciled = False
        self.receipts = {}
        self.audit = []  # Fixed metadata only; in-memory experiment, not durable audit.

    def reconcile(self):
        self.reconciled = False
        self.receipts.clear()
        self.witness.verify(self.pinned_head)
        old = events(self.service.db)
        # A backup can lose a suffix, never invent/change/reorder historical events.
        if old != self.witness.audit[:len(old)]:
            raise PermissionError('Restored audit cannot be reconciled')
        self.reconciled = True
        self.audit.append(('reconciled', self.epoch))

    def receipt(self, principal, task, policy, section, fields, start, end):
        self._evidence()
        self.service.owner(principal)
        if policy in self.baseline:
            raise PermissionError('Restored approvals remain quarantined')
        self.service.authorize(principal, task, policy, section, fields, start, end)
        grant = tuple(self.service.db.execute('SELECT * FROM acct_policies WHERE id=?', (policy,)).fetchone())
        key = (task, policy, section, tuple(fields), start, end)
        self.receipts[key] = (self.epoch, digest(grant), principal.identity)
        self.audit.append(('reapproved', self.epoch, task, policy))

    def _evidence(self):
        if not self.reconciled:
            raise PermissionError('Recovery accounting is stopped')
        self.witness.verify(self.pinned_head)

    def read(self, principal, task, policy, section, fields, start, end):
        self._evidence()
        key = (task, policy, section, tuple(fields), start, end)
        receipt = self.receipts.get(key)
        grant = self.service.db.execute('SELECT * FROM acct_policies WHERE id=?', (policy,)).fetchone()
        if (policy in self.baseline or not grant or not receipt
                or receipt[0] != self.epoch or receipt[1] != digest(tuple(grant))):
            raise PermissionError('Current individual recovery approval required')
        return self.service.read(principal, task, policy, section, fields, start, end)


class RestoreSafetyTests(unittest.TestCase):
    setUp = fixtures.AccountingTests.setUp
    tearDown = fixtures.AccountingTests.tearDown
    snapshot = fixtures.AccountingTests.snapshot
    policy = fixtures.AccountingTests.policy
    # Use the existing real independent-connection harness without collecting its
    # test class a second time. Two new tests below exercise both orderings again.
    pending = transitions.PolicyTransitionTests.pending
    race = transitions.PolicyTransitionTests.race
    state = transitions.PolicyTransitionTests.state
    actions = transitions.PolicyTransitionTests.actions
    deny_read = transitions.PolicyTransitionTests.deny_read

    @contextmanager
    def image(self, source=None):
        """SQLite backup API into a brand NEW temporary synthetic Store only."""
        st = Store(Path(self.temp.name)/(uuid4().hex+'.sqlite3'))
        try:
            (source or self.st.db).backup(st.db)
            self.assertEqual(st.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            yield st, AccountingAccess(Office(st))
        finally:
            st.close()

    def gate(self, service=None, witness=None):
        witness = witness or Witness.capture(self.st.db)
        return RecoveryGateModel(service or self.s, witness, witness.head)

    def get(self, gate, policy, agent='AI27', section='detail', fields=None, business='BIZ-A'):
        return gate.read(self.owner, self.tasks[business, agent], policy, section,
                         fields or sorted(FIELDS[section]), self.start, self.end)

    def receipt(self, gate, p, agent='AI27', section='detail', fields=None, business='BIZ-A'):
        gate.receipt(self.owner, self.tasks[business, agent], p, section,
                     fields or sorted(FIELDS[section]), self.start, self.end)

    def test_pre_revocation_backup_really_contains_unsafe_old_approval(self):
        p = self.policy()
        with self.image() as (old, service):
            self.s.revoke(self.owner, p)
            # Demonstrate the real current app limitation, not claim runtime fixed.
            old.db.execute('BEGIN IMMEDIATE')
            try:
                result = service.read(self.owner, self.tasks['BIZ-A', 'AI27'], p, 'detail',
                                      ['amount_yen'], self.start, self.end)
                self.assertTrue(result['data'])
            finally:
                old.db.execute('ROLLBACK')  # Restore pristine synthetic image after demonstration.
            before = snapshot(old.db)
            gate = self.gate(service)
            with self.assertRaises(PermissionError): self.get(gate, p)
            gate.reconcile()
            with self.assertRaises(PermissionError): self.receipt(gate, p)
            with self.assertRaises(PermissionError): self.get(gate, p)
            self.assertEqual(before, snapshot(old.db))

    def test_post_revocation_backup_never_revives(self):
        p = self.policy(); self.s.revoke(self.owner, p)
        with self.image() as (_, service):
            gate = self.gate(service); gate.reconcile()
            with self.assertRaises(PermissionError): self.get(gate, p)
            with self.assertRaises(ValueError): service.decide(self.owner, p, True)

    def test_missing_external_history_is_denied(self):
        p = self.policy(); witness = Witness.capture(self.st.db)
        gate = self.gate(witness=witness)
        gate.witness = Witness(witness.audit, witness.grants, available=False)
        with self.assertRaises(PermissionError): gate.reconcile()
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_missing_revocation_suffix_cannot_pass_pinned_head(self):
        p = self.policy(); old = Witness.capture(self.st.db)
        self.s.revoke(self.owner, p); latest = Witness.capture(self.st.db)
        gate = RecoveryGateModel(self.s, old, latest.head)
        with self.assertRaises(PermissionError): gate.reconcile()
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_audit_and_current_policy_disagreement_is_denied(self):
        p = self.policy(); self.s.revoke(self.owner, p)
        full = Witness.capture(self.st.db)
        bad = Witness(tuple(r for r in full.audit if r[2] != 'revoked'), full.grants)
        gate = self.gate(witness=bad)
        with self.assertRaises(PermissionError): gate.reconcile()
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_restored_audit_tampering_and_reordering_are_rejected(self):
        self.policy(); witness = Witness.capture(self.st.db)
        with self.image() as (st, service):
            st.db.execute("UPDATE acct_events SET action='changed' WHERE action='approved'")
            gate = self.gate(service, witness)
            with self.assertRaises(PermissionError): gate.reconcile()
        reversed_witness = Witness(tuple(reversed(witness.audit)), witness.grants)
        with self.assertRaises(PermissionError): self.gate(witness=reversed_witness).reconcile()

    def test_new_individual_approval_only_and_old_id_remains_quarantined(self):
        old = self.policy(); gate = self.gate(); gate.reconcile()
        with self.assertRaises(PermissionError): self.receipt(gate, old)
        fresh = self.policy()
        with self.assertRaises(PermissionError): self.get(gate, fresh)
        self.receipt(gate, fresh)
        self.assertTrue(self.get(gate, fresh)['data'])
        with self.assertRaises(PermissionError): self.get(gate, old)
        self.assertTrue(all('合成秘密' not in repr(r) for r in gate.audit))

    def test_pending_rejected_expired_and_revoked_new_policies_denied(self):
        gate = self.gate(); gate.reconcile()
        for status in ('pending', 'rejected', 'expired', 'revoked'):
            p = self.policy(approve=False)
            if status == 'rejected': self.s.decide(self.owner, p, False)
            if status == 'revoked': self.s.revoke(self.owner, p)
            if status == 'expired':
                self.s.decide(self.owner, p, True)
                self.st.db.execute('UPDATE acct_policies SET expires_at=? WHERE id=?',
                                   ((now()-timedelta(seconds=1)).isoformat(), p))
            with self.subTest(status=status), self.assertRaises(PermissionError): self.receipt(gate, p)
            with self.assertRaises(PermissionError): self.get(gate, p)

    def test_reapproved_scope_cannot_expand_fields_dates_or_business(self):
        gate = self.gate(); gate.reconcile()
        p = self.policy(fields=['amount_yen']); self.receipt(gate, p, fields=['amount_yen'])
        for fields, start, end, task in (
            (['amount_yen','day'], self.start, self.end, self.tasks['BIZ-A','AI27']),
            (['amount_yen'], '2025-01-01', self.end, self.tasks['BIZ-A','AI27']),
            (['amount_yen'], self.start, self.end, self.tasks['BIZ-B','AI27'])):
            with self.subTest(task=task, fields=fields), self.assertRaises(PermissionError):
                gate.read(self.owner, task, p, 'detail', fields, start, end)
        self.assertTrue(self.get(gate, p, fields=['amount_yen'])['data'])

    def test_receipt_requires_current_representative_not_viewer(self):
        gate = self.gate(); gate.reconcile(); p = self.policy()
        with self.assertRaises(PermissionError):
            gate.receipt(Principal('viewer','viewer'), self.tasks['BIZ-A','AI27'], p,
                         'detail', sorted(FIELDS['detail']), self.start, self.end)
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_all_28_roles_after_recovery_use_current_explicit_scope(self):
        before = self.snapshot(); gate = self.gate(); gate.reconcile()
        self.assertEqual(len(self.st.agents), 28)
        for agent in self.st.agents:
            for section in FIELDS:
                with self.subTest(agent=agent, section=section):
                    if section not in ROLES.get(agent, set()):
                        with self.assertRaises(PermissionError): self.policy(agent, section)
                        with self.assertRaises(PermissionError): self.get(gate, 'missing', agent, section)
                        continue
                    field = sorted(FIELDS[section])[0]
                    p = self.policy(agent, section, [field])
                    if section in ('advertising','terms'):
                        self.s.approved_source(self.owner,'BIZ-A',section,'2026-01-05',field,10)
                    self.receipt(gate, p, agent, section, [field])
                    body = self.get(gate, p, agent, section, [field])
                    self.assertEqual(body['business_id'], 'BIZ-A')
                    self.assertNotIn('合成秘密明細本文', json.dumps(body, ensure_ascii=False))
        self.assertEqual(before, self.snapshot())

    def test_revocation_after_reapproval_invalidates_receipt(self):
        gate = self.gate(); gate.reconcile(); p = self.policy(); self.receipt(gate, p)
        self.s.revoke(self.owner, p)
        with self.assertRaises(PermissionError): self.get(gate, p)
        with self.assertRaises(ValueError): self.s.decide(self.owner, p, True)
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_new_recovery_epoch_and_evidence_outage_stop_existing_receipts(self):
        gate = self.gate(); gate.reconcile(); p = self.policy(); self.receipt(gate, p)
        other = self.gate(); other.reconcile()
        other.receipts = dict(gate.receipts)
        with self.assertRaises(PermissionError): self.get(other, p)
        gate.witness = Witness(gate.witness.audit, gate.witness.grants, available=False)
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_schema_data_keys_and_both_new_tables_preserved_by_backup(self):
        p = self.policy(); self.s.bind_task(self.owner, self.tasks['BIZ-A','AI27'], p)
        self.s.read(self.owner, self.tasks['BIZ-A','AI27'], p, 'detail', ['amount_yen'], self.start, self.end)
        before = snapshot(self.st.db)
        with self.image() as (st, service):
            self.assertEqual(before, snapshot(st.db))
            self.assertEqual(st.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(st.db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(st.db.execute('PRAGMA user_version').fetchone()[0], self.st.db.execute('PRAGMA user_version').fetchone()[0])
            gate = self.gate(service); gate.reconcile()
            with self.assertRaises(PermissionError): self.get(gate, p)
            self.assertEqual(before, snapshot(st.db))

    def test_restore_interruption_discards_staging_and_preserves_sources(self):
        self.policy(); source = snapshot(self.st.db)
        with self.image() as (st, _):
            target = snapshot(st.db)
            # This models interruption of SQLite backup, never file replacement.
            self.st.db.execute("INSERT INTO acct_events VALUES(?,?,?,?,?)", (uuid4().hex,None,'synthetic_extra','owner',now().isoformat()))
            changed = snapshot(self.st.db)
            def fail(status, remaining, total):
                raise RuntimeError('Synthetic backup interruption')
            with self.assertRaises(RuntimeError): self.st.db.backup(st.db, pages=1, progress=fail)
            self.assertEqual(changed, snapshot(self.st.db))
            self.assertEqual(target, snapshot(st.db))
            self.assertNotEqual(source, changed)

    def test_lost_audit_suffix_is_detected_and_retained_outside_restored_db(self):
        p = self.policy()
        with self.image() as (st, service):
            prior = events(st.db); self.s.revoke(self.owner, p)
            witness = Witness.capture(self.st.db)
            lost = witness.audit[len(prior):]
            self.assertTrue(any(r[1] == p and r[2] == 'revoked' for r in lost))
            gate = self.gate(service, witness); gate.reconcile()
            self.assertEqual(events(st.db), prior)  # No invented/replayed historical rows.
            self.assertEqual(gate.witness.audit, witness.audit)
            with self.assertRaises(PermissionError): self.get(gate, p)

    def test_two_connections_revoke_then_approve_cannot_resurrect(self):
        p, a, b = self.race('revoke','approve')
        self.assertEqual((a,b), ('success','denied')); self.assertEqual(self.state(p), 'revoked')
        self.deny_read(p)
        gate = self.gate(); gate.reconcile()
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_two_connections_approve_then_revoke_finishes_denied(self):
        p, a, b = self.race('approve','revoke')
        self.assertEqual((a,b), ('success','success')); self.assertEqual(self.state(p), 'revoked')
        self.deny_read(p)
        gate = self.gate(); gate.reconcile()
        with self.assertRaises(PermissionError): self.get(gate, p)

    def test_staging_manifest_rejects_data_schema_and_version_changes(self):
        self.policy(); expected = snapshot(self.st.db)
        version = self.st.db.execute('PRAGMA user_version').fetchone()[0]
        for mutation in ("UPDATE portfolio_ledger SET amount_yen=999", "CREATE TABLE synthetic_unexpected(id)",
                         "PRAGMA user_version=999"):
            with self.subTest(mutation=mutation), self.image() as (st, _):
                verify_staging(st.db, expected, version)
                st.db.execute(mutation)
                changed = snapshot(st.db)
                with self.assertRaises(PermissionError): verify_staging(st.db, expected, version)
                self.assertEqual(changed, snapshot(st.db))

    def test_staging_manifest_refuses_historical_fk_damage_without_repair(self):
        # Seed a historical fault in an in-memory synthetic image, then enable FK.
        db = sqlite3.connect(':memory:')
        try:
            self.st.db.backup(db)
            db.execute('CREATE TABLE synthetic_orphan(id REFERENCES tasks(id))')
            db.execute("INSERT INTO synthetic_orphan VALUES('missing')"); db.commit()
            db.execute('PRAGMA foreign_keys=ON')
            before = snapshot(db)
            with self.assertRaises(PermissionError): verify_staging(db, before, db.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(before, snapshot(db))
            self.assertTrue(db.execute('PRAGMA foreign_key_check').fetchall())
        finally: db.close()

    def test_invented_success_transition_or_duplicate_event_is_rejected(self):
        p = self.policy(); witness = Witness.capture(self.st.db)
        for audit in (witness.audit + (witness.audit[-1],),
                      witness.audit + ((uuid4().hex, p, 'approved', 'owner', now().isoformat()),)):
            with self.subTest(audit_length=len(audit)), self.assertRaises(PermissionError):
                self.gate(witness=Witness(audit, witness.grants)).reconcile()

    def test_failed_reconciliation_clears_previously_issued_receipts(self):
        gate = self.gate(); gate.reconcile(); p = self.policy(); self.receipt(gate, p)
        self.assertTrue(self.get(gate, p)['data'])
        gate.witness = Witness((), ())
        with self.assertRaises(PermissionError): gate.reconcile()
        self.assertFalse(gate.reconciled); self.assertEqual(gate.receipts, {})
        with self.assertRaises(PermissionError): self.get(gate, p)
