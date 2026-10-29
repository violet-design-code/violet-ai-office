"""Portable regressions for Windows spawn pressure; no live DBs or OS settings.

The bounded Event waits below inject a slow/unresponsive child deliberately;
there are no retry loops or sleeps in the implementation.
"""
from contextlib import closing
from datetime import date
import multiprocessing
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch

from tests import synthetic_separated_abort as separated
from tests import test_seminar_support as seminar_tests
from violet_office.synthetic_recovery import SyntheticRealm, Unavailable


def delayed_issuer(connection, capability, boot):
    threading.Event().wait(0.25)  # Controlled fault: slow interpreter/crypto startup.
    separated._issuer_process(connection, capability, boot)


def one_second_plus_issuer(connection, capability, boot):
    threading.Event().wait(1.2)  # Reproduce the exact old one-second deadline.
    separated._issuer_process(connection, capability, boot)


def silent_issuer(connection, capability, boot):
    threading.Event().wait(5)  # Controlled fault: no startup registration.
    connection.close()


def dead_issuer(connection, capability, boot):
    os._exit(47)


class WindowsSuiteLifecycleTests(unittest.TestCase):
    def peer(self):
        return separated.MockAuthenticatedPeer('SUPERVISOR-SID')

    def test_slow_spawn_uses_startup_budget_not_short_command_timeout(self):
        with patch.object(separated,'_issuer_process',delayed_issuer):
            issuer=separated.IsolatedIssuer(self.peer(),timeout=0.01,startup_timeout=15)
        try:
            self.assertEqual(set(issuer.registration),{'public','key_id','boot'})
            self.assertEqual(issuer.timeout,0.01)
        finally: issuer.close()
        self.assertFalse(issuer.process.is_alive())

    def test_reported_one_second_startup_deadline_reproduced(self):
        with patch.object(separated,'_issuer_process',one_second_plus_issuer):
            with self.assertRaises(Unavailable):
                separated.IsolatedIssuer(self.peer(),timeout=1,startup_timeout=1)
            issuer=separated.IsolatedIssuer(self.peer(),timeout=1,startup_timeout=15)
        try: self.assertEqual(set(issuer.registration),{'public','key_id','boot'})
        finally: issuer.close()

    def test_startup_timeout_reaps_unresponsive_child(self):
        before={p.pid for p in multiprocessing.active_children()}
        try:
            with patch.object(separated,'_issuer_process',silent_issuer):
                with self.assertRaises(Unavailable):
                    separated.IsolatedIssuer(self.peer(),timeout=1,startup_timeout=0.02)
            self.assertEqual({p.pid for p in multiprocessing.active_children()},before)
        finally:
            for process in multiprocessing.active_children():
                if process.pid not in before: process.terminate(); process.join(2)

    def test_startup_child_death_is_failure_not_registration(self):
        with patch.object(separated,'_issuer_process',dead_issuer):
            with self.assertRaises(Unavailable): separated.IsolatedIssuer(self.peer(),startup_timeout=15)

    def test_command_timeout_discards_channel_and_cannot_reuse_late_reply(self):
        peer=self.peer(); issuer=separated.IsolatedIssuer(peer)
        try:
            with patch.object(issuer,'_reply',side_effect=Unavailable('Synthetic deadline')):
                with self.assertRaises(Unavailable): issuer.request(peer,'rotate')
            self.assertFalse(issuer.process.is_alive())
            with self.assertRaises(Unavailable): issuer.request(peer,'rotate')
        finally: issuer.close()

    def test_startup_and_request_budgets_are_distinct_and_bounded(self):
        peer=self.peer()
        with patch.object(separated,'wait_for_owned_reply',wraps=separated.wait_for_owned_reply) as wait:
            issuer=separated.IsolatedIssuer(peer,timeout=1,startup_timeout=15)
            try: issuer.request(peer,'rotate')
            finally: issuer.close()
        self.assertEqual([call.args[2] for call in wait.call_args_list],[15,1])

    def test_sqlite_busy_diagnostic_is_safe_and_still_denies(self):
        realm=SyntheticRealm()
        try:
            error=sqlite3.OperationalError('SQL and confidential-path must not leak')
            error.sqlite_errorcode=sqlite3.SQLITE_BUSY
            with patch('violet_office.synthetic_recovery.sqlite3.connect',side_effect=error):
                with self.assertRaises(Unavailable) as raised: realm.client().status()
            self.assertIn('local_ledger:sqlite_busy',str(raised.exception))
            self.assertNotIn('confidential-path',str(raised.exception))
        finally: realm.close()

    def test_sqlite_open_diagnostic_distinguishes_external_authority(self):
        realm=SyntheticRealm()
        try:
            from violet_office.synthetic_recovery import database
            error=sqlite3.OperationalError('sensitive filename')
            error.sqlite_errorcode=sqlite3.SQLITE_CANTOPEN
            with patch('violet_office.synthetic_recovery.sqlite3.connect',side_effect=error):
                with self.assertRaises(Unavailable) as raised:
                    with database(realm.context,realm.context.external): pass
            self.assertIn('mock_authority:sqlite_open',str(raised.exception))
            self.assertNotIn('sensitive filename',str(raised.exception))
        finally: realm.close()

    def test_external_commit_busy_reproduces_reported_exit_stage_and_stays_closed(self):
        # A genuine SQLite reader lock, NOT evidence that Windows had this lock.
        from violet_office.synthetic_recovery import Denied
        realm=SyntheticRealm(); original=sqlite3.connect
        try:
            with closing(original(realm.context.external,isolation_level=None)) as reader:
                reader.execute('BEGIN')
                with closing(reader.execute('SELECT body FROM journal')) as cursor:
                    cursor.fetchall()
                def short_test_lock_wait(*args,**kwargs):
                    # Shorten only this controlled reproduction; runtime stays 5s.
                    kwargs['timeout']=0.05
                    return original(*args,**kwargs)
                with patch('violet_office.synthetic_recovery.sqlite3.connect',side_effect=short_test_lock_wait):
                    with self.assertRaises(Unavailable) as raised:
                        realm.client().enroll(realm.representative_token)
                message=str(raised.exception)
                self.assertIn('mock_authority:sqlite_busy',message)
                self.assertIn('phase=commit',message)
                self.assertIn('sqlite_errorcode=5',message)
                reader.execute('ROLLBACK')
            for path,expected in ((realm.context.local,0),(realm.context.external,1)):
                with closing(original(path)) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM journal').fetchone()[0],expected)
            with self.assertRaises(Denied): realm.client().status()
        finally: realm.close()

    def test_seminar_security_assertions_survive_month_year_and_leap_boundaries(self):
        for day in (date(2030,1,30),date(2030,12,30),date(2032,2,27)):
            with self.subTest(day=day):
                case=seminar_tests.SeminarSupportTests('test_web_auth_csrf_xss_nonce_and_mobile_route')
                case.setUp()
                try:
                    case.day=day
                    case.test_web_auth_csrf_xss_nonce_and_mobile_route()
                finally: case.tearDown()
