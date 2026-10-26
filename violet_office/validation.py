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
    for directory in ('violet_office','tests','config','scripts','migrations'):
        files.extend(p for p in (root/directory).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.css','.sh','.bat','.sql'))
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


REQUIRED_TESTS.update({
    'tests.test_learning.LearningTests.'+name for name in (
        'test_feedback_guidance_changes_next_same_work_after_full_approval',
        'test_unapproved_improvement_not_applied_and_comparison_not_business_proof',
        'test_source_revocation_removes_profile_and_blocks_old_draft_approval',
        'test_private_data_and_customer_conversation_import_are_rejected',
        'test_prompt_injection_quarantine_and_permission_flags_rejected',
        'test_28_employee_evaluation_insufficient_data_and_no_ranking',
        'test_api_budget_and_permissions_are_unchanged',
        'test_recovery_copy_requires_knowledge_feedback_and_style_reapproval',
    )
})


REQUIRED_TESTS.update({
    'tests.test_phase9.Phase9Tests.'+name for name in (
        'test_every_segment_server_side_access_matrix',
        'test_card_is_hashed_and_manual_approval_required',
        'test_parallel_last_seat_never_double_books',
        'test_three_day_drafts_yes_maybe_unanswered_excludes_no',
        'test_preparation_and_dispatch_deduplicated',
        'test_stop_and_reconsent_never_revive_old_approval',
        'test_selection_quality_multiple_metrics_and_manual_override',
        'test_upgrade_actual_phase8_schema_preserves_old_rows',
        'test_restore_pauses_scheduler_selection_and_invalidates_approvals',
    )
})


REQUIRED_TESTS.update({
    'tests.test_portfolio.PortfolioTests.'+name for name in (
        'test_six_confirmed_businesses_and_no_invented_prices',
        'test_contacts_same_name_are_not_merged_or_shared',
        'test_database_composite_foreign_keys_enforce_scope',
        'test_business_budget_guard_applies_to_shared_store_claim',
        'test_unapproved_estimate_or_operation_cannot_be_recorded',
        'test_meeting_report_has_no_private_contacts_and_does_not_apply_improvements',
        'test_upgrade_actual_phase9_schema_preserves_every_old_row',
        'test_seminars_remain_unchanged_and_linking_is_explicit',
        'test_legacy_line_and_customer_records_not_reassigned',
        'test_web_scope_idor_csrf_and_existing_roles',
        'test_restore_halts_portfolio_and_requires_reapproval',
    )
})


REQUIRED_TESTS.update({
    'tests.test_development.DevelopmentTests.'+name for name in (
        'test_multiple_ideas_no_seeded_targets_or_automatic_business_link',
        'test_unapproved_plan_and_safety_field_changes_are_blocked',
        'test_new_evidence_or_scenario_revision_invalidates_approval_and_pauses',
        'test_scope_and_unlinked_ideas_are_representative_only',
        'test_actual_estimate_mock_and_revision_are_separate_from_forecast',
        'test_upgrade_from_phase10_schema_is_additive_and_idempotent',
        'test_restore_reapproval_and_no_automatic_plan_restart',
    )
} | {'tests.test_development.FinanceTests.test_known_monthly_profit_break_even_and_payback',
     'tests.test_development.FinanceTests.test_missing_values_do_not_default_to_zero'})


REQUIRED_TESTS.update({
    'tests.test_sales.SalesTests.'+name for name in (
        'test_tax_unknown_blocks_quote_send_and_contract_without_touching_old_prices',
        'test_identity_link_requires_two_purpose_consents_and_unlink_preserves_businesses',
        'test_actual_pipeline_invoice_partial_payment_final_payment_and_profit',
        'test_mock_pipeline_does_not_enter_actual_financial_or_old_booking_counts',
        'test_full_refund_credit_cancellation_reopen_and_revised_quote',
        'test_web_acl_csrf_xss_and_manual_forgery_rejected',
        'test_migration_from_actual_phase11_schema_preserves_all_old_rows',
        'test_restore_rechecks_approvals_preserves_financial_history',
        'test_opaque_shared_tasks_still_obey_emergency_stop_and_no_legacy_execution',
    )
} | {'tests.test_sales.SalesFinanceTests.test_unknown_tax_is_not_zero_and_not_approvable'})


REQUIRED_TESTS.update({
    'tests.test_workflow.WorkflowTests.'+name for name in (
        'test_all_28_roles_generate_local_templates_and_do_not_change_registry',
        'test_pause_resume_reassign_priority_and_owner_only',
        'test_progress_cannot_claim_sent_from_approval',
        'test_mock_payment_never_enters_real_dashboard',
        'test_stop_consent_response_and_seminar_revision_invalidate_queue',
        'test_three_day_reminder_reuses_original_canonical_id',
        'test_restore_pauses_tasks_and_reapproves_queued_drafts',
        'test_additive_migration_preserves_all_phase12_tables_and_rows',
        'test_web_scope_nonce_csrf_and_owner_controls',
    )
})


REQUIRED_TESTS.update({
    'tests.test_seminar_support.SeminarSupportTests.'+name for name in (
        'test_exact_target_segment_enforced_on_old_and_new_portals',
        'test_duplicate_application_capacity_and_cancellation_after_deadline',
        'test_separate_purpose_consent_and_global_stop_gate',
        'test_existing_workflow_draft_cannot_bypass_new_purpose_consent',
        'test_mock_success_once_and_parallel_no_double_execution',
        'test_deadline_previous_day_event_day_due_dates_and_scheduling',
        'test_real_metrics_denominators_cancel_and_interview',
        'test_financial_attribution_uses_existing_sources_only_and_no_double_count',
        'test_restore_holds_schedule_approvals_and_preserves_rsvp',
        'test_migration_preserves_phase13_rows_and_digital_product_plan',
    )
})


REQUIRED_TESTS.update({
    'tests.test_business_foundation.BusinessFoundationTests.'+name for name in (
        'test_registration_unique_id_idempotency_and_no_seeded_performance',
        'test_assignment_many_to_many_all_28_definitions_unchanged',
        'test_scoped_ai_reads_do_not_inherit_representative_global_access',
        'test_assignment_revocation_blocks_pending_and_inflight_output',
        'test_human_business_acl_and_customers_remain_separate',
        'test_sales_expenses_profit_and_estimates_are_scoped',
        'test_legacy_businesses_and_pending_tasks_unchanged_until_opt_in',
        'test_migration_is_additive_idempotent_and_keeps_previous_rows',
        'test_restore_revokes_assignment_preserves_business_and_customer_rows',
        'test_independent_reviewer_also_needs_business_assignment',
    )
})


REQUIRED_TESTS.update({
    'tests.test_business_operations.BusinessOperationsTests.'+name for name in (
        'test_policy_unconfirmed_does_not_add_two_revenue_ledgers',
        'test_scm_partial_payment_unpaid_and_cash_not_revenue',
        'test_full_refund_and_unpaid_cancellation_do_not_leave_sales_or_unpaid',
        'test_period_boundaries_jst_exclusive_end_and_prior_invoices',
        'test_custom_kpi_requires_rule_and_complete_period',
        'test_global_totals_same_basis_and_partial_or_mixed_data',
        'test_global_has_no_customer_pii_and_nonrepresentative_is_denied',
        'test_authenticated_task_context_uses_server_employee_and_assignment',
        'test_migration_keeps_all_prior_data_and_is_repeatable',
        'test_restore_preserves_reporting_settings_and_revokes_ai_assignment',
    )
})


REQUIRED_TESTS.update({
    'tests.test_customer_journey.CustomerJourneyTests.'+name for name in (
        'test_pending_profile_no_invented_conditions_and_no_approval',
        'test_official_pages_unavailable_do_not_establish_authority_or_age',
        'test_intake_atomic_idempotent_no_customer_duplication_or_implicit_consent',
        'test_three_existing_roles_selected_deduplicated_and_28_unchanged',
        'test_many_business_assignments_do_not_inherit_each_others_access',
        'test_role_revocation_blocks_existing_tasks_even_legacy_compatibility',
        'test_pending_profile_cannot_be_bypassed_from_existing_sales_approval',
        'test_mock_journey_booking_approval_is_separate_and_never_sales',
        'test_migration_preserves_existing_rows_and_is_repeatable',
        'test_restore_retains_records_and_requires_reapproval',
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
        report=next((ROOT/'reports'/name for name in ('phase17-validation.json','phase16-validation.json','phase15-validation.json','phase14-validation.json','phase13-validation.json','phase12-validation.json','phase11-validation.json','phase10-validation.json','phase9-validation.json','phase8-validation.json','phase7-validation.json','phase6-validation.json') if (ROOT/'reports'/name).is_file()),ROOT/'reports'/'missing.json')
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
