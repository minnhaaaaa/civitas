from evaluation import ALL_SCENARIOS, calculate_suite_metrics, render_suite_report, run_suite


def test_golden_suite_contains_the_required_ten_scenarios() -> None:
    assert [item.manifest.scenario_id for item in ALL_SCENARIOS] == [
        "independent-consensus",
        "shared-source-false-consensus",
        "agent-echo",
        "stale-lead-time-contradiction",
        "clean-mcp-evidence",
        "genuine-objective-conflict",
        "partial-fulfillment",
        "fefo-failure",
        "warehouse-capacity-conflict",
        "duplicate-execution-retry",
    ]


def test_system_visible_fixtures_do_not_expose_hidden_truth() -> None:
    for scenario in ALL_SCENARIOS:
        visible_claim_ids = {item.claim_id for item in scenario.visible.jury_inputs.claims}
        visible_evidence_ids = {item.evidence_id for item in scenario.visible.jury_inputs.evidence}
        hidden_claim_ids = {item.claim_id for item in scenario.true_world_state.hidden_claims}
        hidden_evidence_ids = {
            item.evidence_id for item in scenario.true_world_state.hidden_evidence
        }

        assert visible_claim_ids.isdisjoint(hidden_claim_ids)
        assert visible_evidence_ids.isdisjoint(hidden_evidence_ids)


def test_golden_suite_matches_expected_solver_jury_and_execution_outcomes() -> None:
    results = run_suite(ALL_SCENARIOS)

    for scenario, result in zip(ALL_SCENARIOS, results, strict=True):
        assert result.solver_status_match
        assert result.jury_state_match
        assert result.actual_reason_codes == scenario.expected_outcome.reason_codes
        assert result.expected_lineage_pairs == result.actual_lineage_pairs
        if scenario.expected_outcome.execution_state is not None:
            assert result.execution_state_match


def test_golden_suite_uses_regret_and_reports_metrics_separately() -> None:
    results = run_suite(ALL_SCENARIOS)
    metrics = calculate_suite_metrics(results)
    report = render_suite_report(metrics, results)

    assert all(item.regret_match for item in results if item.regret_checked)
    assert metrics.total_scenarios == 10
    assert metrics.solver_status_accuracy == 100.0
    assert metrics.jury_state_accuracy == 100.0
    assert metrics.reason_code_precision == 100.0
    assert metrics.reason_code_recall == 100.0
    assert metrics.lineage_group_precision == 100.0
    assert metrics.lineage_group_recall == 100.0
    assert metrics.execution_retry_accuracy == 100.0
    assert "Solver status accuracy" in report
    assert "Reason-code precision" in report
    assert "Lineage recall" in report
    assert "Regret alignment rate" in report
