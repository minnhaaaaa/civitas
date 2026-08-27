"""Human-readable suite reporting."""

from __future__ import annotations

from evaluation.metrics import SuiteMetrics
from evaluation.runner import ScenarioResult


def render_suite_report(metrics: SuiteMetrics, results: tuple[ScenarioResult, ...]) -> str:
    lines = [
        "# Evaluation Suite",
        f"Scenarios: {metrics.total_scenarios}",
        f"Solver status accuracy: {metrics.solver_status_accuracy:.2f}%",
        f"Jury state accuracy: {metrics.jury_state_accuracy:.2f}%",
        f"Reason-code precision: {metrics.reason_code_precision:.2f}%",
        f"Reason-code recall: {metrics.reason_code_recall:.2f}%",
        f"Lineage precision: {metrics.lineage_group_precision:.2f}%",
        f"Lineage recall: {metrics.lineage_group_recall:.2f}%",
        f"Regret alignment rate: {metrics.regret_alignment_rate:.2f}%",
        f"Execution retry accuracy: {metrics.execution_retry_accuracy:.2f}%",
        f"Oracle agreement rate: {metrics.oracle_agreement_rate:.2f}%",
        "",
        "Scenario results:",
    ]
    lines.extend(
        (
            f"- {item.manifest.scenario_id}: "
            f"solver={item.actual_solver_status.value}, "
            f"jury={item.actual_jury_state.value}, "
            f"reasons={sorted(item.actual_reason_codes)}"
        )
        for item in results
    )
    return "\n".join(lines)
