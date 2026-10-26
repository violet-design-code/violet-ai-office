"""Repeatable test evidence and fail-closed release assessment; no deployment."""
import argparse
import ast
import hashlib
import json
import platform
import sqlite3
import ssl
import sys
import unittest
from pathlib import Path

from .store import now

ROOT=Path(__file__).resolve().parent.parent
REQUIRED_TESTS={
    'tests.test_phase6_scenarios.ScenarioTests.'+name for name in (
        'test_A_sns_request_research_plan_copy_audit_approval_pending',
        'test_B_referral_terms_human_check_required_no_false_compliance',
        'test_C_follow_explicit_consent_registration_seven_day_schedule',
        'test_D_inquiry_booking_approval_confirmation_pauses_delivery',
        'test_E_stop_persists_and_prevents_old_approved_send',
        'test_F_reconsent_only_one_purpose_and_new_history',
        'test_G_api_failure_bounded_retry_failure_and_dashboard_alert',
        'test_H_unapproved_blocked_approved_mock_once_no_real_publication',
    )
} | {'tests.test_recovery.RecoveryTests.test_wal_snapshot_and_restore_quarantine_preserve_source',
     'tests.test_office.OfficeTests.test_host_origin_csrf_and_viewer_mutations_blocked'}


def source_fingerprint(root=ROOT):
    files=[]
    for directory in ('violet_office','tests','config','scripts'):
        files.extend(p for p in (root/directory).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.css','.sh','.bat'))
    aggregate=hashlib.sha256()
    for path in sorted(files):
        aggregate.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return aggregate.hexdigest()


REQUIRED_TESTS.update({
    'tests.test_growth.GrowthTests.'+name for name in (
        'test_goals_unset_remain_nullable_and_zero_is_explicit',
        'test_daily_generation_needed_roles_handoffs_and_approval_stop',
        'test_weekly_and_monthly_reports_generate_without_fake_success',
        'test_failure_auto_pause_safe_retry_reuses_completed_handoffs',
        'test_plan_assignment_priority_deadline_and_human_gate',
        'test_two_scheduler_workers_do_not_duplicate_artifacts',
    )
})


def dependency_inventory():
    external=set()
    for path in (ROOT/'violet_office').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Import):
                modules=[alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node,ast.ImportFrom) and node.level==0:
                modules=[(node.module or '').split('.')[0]]
            else:
                continue
            external.update(m for m in modules if m and m not in sys.stdlib_module_names and m!='violet_office')
    return {'runtime_external_imports':sorted(external),'python':platform.python_version(),
        'sqlite':sqlite3.sqlite_version,'openssl':ssl.OPENSSL_VERSION,
        'os':platform.system(),'latest_cve_database_check':'未実施・最新公式アドバイザリの照合が必要',
        'claim':'第三者ランタイム依存は検出されない場合も、Python/SQLite/OpenSSL/OSの脆弱性がないという保証ではない'}


def assess(evidence=None):
    evidence=evidence if isinstance(evidence,dict) else {}
    info=evidence.get('tests',{})
    info=info if isinstance(info,dict) else {}
    executed=evidence.get('executed_tests',[])
    executed=executed if isinstance(executed,list) and all(isinstance(name,str) for name in executed) else []
    tested=bool(evidence.get('source_fingerprint')==source_fingerprint()
        and info.get('successful') is True and info.get('run')==info.get('passed')==len(set(executed))
        and all(info.get(field,0)==0 for field in ('failures','errors','skipped','expected_failures','unexpected_successes'))
        and REQUIRED_TESTS <= set(executed))
    gates=[
        {'condition':'必須ローカル/モック試験','state':'passed' if tested else 'unverified','note':'現在のソース指紋と試験結果を照合。本番サービス試験は含まない'},
        {'condition':'重大セキュリティ問題・本番対策','state':'blocked','note':'TLS/MFA・暗号化・最新脆弱性照合・第三者監査は未完了'},
        {'condition':'外部API接続確認','state':'blocked','note':'実サービスはすべて未接続'},
        {'condition':'誤送信防止','state':'mock_only','note':'モック試験と実送信経路無効化。実APIでの結果照会は未検証'},
        {'condition':'承認機能','state':'local_only','note':'ローカル承認。外部実行はできない'},
        {'condition':'停止・再同意','state':'mock_only','note':'既存LINE本体との連携・回帰試験は未実施'},
        {'condition':'バックアップ・復元','state':'local_tested' if tested else 'unverified','note':'別ファイル復元・隔離を一時DBで検証'},
        {'condition':'代表の明示的本番移行承認','state':'missing','note':'承認なし。記録の作成や本番移行は実施しない'},
    ]
    return {'production_allowed':False,'decision_ja':'本番移行不可','gates':gates,
        'local_use':'匿名・合成データを使うローカル検証のみ','actual_external_services':[],
        'automated_deployment':False}


def saved_assessment():
    try:
        report=ROOT/'reports'/'phase7-validation.json'
        if not report.is_file():
            report=ROOT/'reports'/'phase6-validation.json'
        evidence=json.loads(report.read_text())
        return assess(evidence)
    except (OSError,ValueError,TypeError,KeyError):
        return assess()


def main():
    p=argparse.ArgumentParser(description='全自動テスト証跡と本番移行判定（デプロイなし）')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),top_level_dir=str(ROOT))
    def case_ids(node):
        if isinstance(node,unittest.TestSuite):
            return [name for child in node for name in case_ids(child)]
        return [node.id()]
    executed=case_ids(suite)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    failed=len(result.failures)
    errors=len(result.errors)
    skipped=len(result.skipped)
    expected=len(result.expectedFailures)
    unexpected=len(result.unexpectedSuccesses)
    evidence={'recorded_at':now().isoformat(),'source_fingerprint':source_fingerprint(),'executed_tests':executed,
        'tests':{'run':result.testsRun,'passed':result.testsRun-failed-errors-skipped-expected-unexpected,
        'failures':failed,'errors':errors,'skipped':skipped,'expected_failures':expected,'unexpected_successes':unexpected,
        'successful':result.wasSuccessful() and result.testsRun>0 and expected==0},
        'dependencies':dependency_inventory(),'not_executed':['実LLM回答品質','既存LINE本体の回帰','外部API実接続/誤送信/実課金','最新CVEデータベース照合','法令・公式規約の実査','Windows/macOS/スマートフォン実機']}
    evidence['release']=assess(evidence)
    with Path(args.output).open('x',encoding='utf-8') as file:
        json.dump(evidence,file,ensure_ascii=False,indent=2)
    raise SystemExit(0 if evidence['tests']['successful'] else 1)


if __name__=='__main__':
    main()
