"""Scenario-suite metrics kept separate by evaluation concern."""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.runner import ScenarioResult


@dataclass(frozen=True, slots=True)
class SuiteMetrics:
    total_scenarios: int
    solver_status_accuracy: float
    jury_state_accuracy: float
    reason_code_precision: float
    reason_code_recall: float
    lineage_group_precision: float
    lineage_group_recall: float
    regret_alignment_rate: float
    execution_retry_accuracy: float
    oracle_agreement_rate: float


def calculate_suite_metrics(results: tuple[ScenarioResult, ...]) -> SuiteMetrics:
    if not results:
        raise ValueError("at least one scenario result is required")

    expected_reason_total = sum(len(item.expected_reason_codes) for item in results)
    actual_reason_total = sum(len(item.actual_reason_codes) for item in results)
    matched_reason_total = sum(
        len(item.expected_reason_codes & item.actual_reason_codes) for item in results
    )

    expected_lineage_total = sum(len(item.expected_lineage_pairs) for item in results)
    actual_lineage_total = sum(len(item.actual_lineage_pairs) for item in results)
    matched_lineage_total = sum(
        len(item.expected_lineage_pairs & item.actual_lineage_pairs) for item in results
    )

    regret_denominator = sum(1 for item in results if item.regret_checked)
    execution_denominator = sum(1 for item in results if item.expected_execution_state is not None)
    oracle_denominator = sum(1 for item in results if item.oracle_checked)

    return SuiteMetrics(
        total_scenarios=len(results),
        solver_status_accuracy=_ratio(sum(item.solver_status_match for item in results), len(results)),
        jury_state_accuracy=_ratio(sum(item.jury_state_match for item in results), len(results)),
        reason_code_precision=_ratio(matched_reason_total, actual_reason_total),
        reason_code_recall=_ratio(matched_reason_total, expected_reason_total),
        lineage_group_precision=_ratio(matched_lineage_total, actual_lineage_total),
        lineage_group_recall=_ratio(matched_lineage_total, expected_lineage_total),
        regret_alignment_rate=_ratio(
            sum(item.regret_match for item in results if item.regret_checked), regret_denominator
        ),
        execution_retry_accuracy=_ratio(
            sum(
                bool(item.execution_state_match)
                for item in results
                if item.expected_execution_state is not None
            ),
            execution_denominator,
        ),
        oracle_agreement_rate=_ratio(
            sum(bool(item.oracle_match) for item in results if item.oracle_checked),
            oracle_denominator,
        ),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 100.0 if denominator == 0 else round(numerator / denominator * 100, 2)
