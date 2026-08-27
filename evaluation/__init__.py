"""Deterministic evaluation fixtures, runners, and reports."""

from evaluation.generators import small_problem_strategy
from evaluation.metrics import SuiteMetrics, calculate_suite_metrics
from evaluation.report import render_suite_report
from evaluation.runner import ExecutionLedgerSimulator, ScenarioResult, run_scenario, run_suite
from evaluation.scenarios import (
    ALL_SCENARIOS,
    ExpectedEvidenceLineage,
    ExpectedOutcome,
    GoldenScenario,
    HiddenWorldState,
    InterventionStep,
    ScenarioManifest,
    VisibleObservations,
    get_scenario,
)

__all__ = [
    "ALL_SCENARIOS",
    "ExecutionLedgerSimulator",
    "ExpectedEvidenceLineage",
    "ExpectedOutcome",
    "GoldenScenario",
    "HiddenWorldState",
    "InterventionStep",
    "ScenarioManifest",
    "ScenarioResult",
    "SuiteMetrics",
    "VisibleObservations",
    "calculate_suite_metrics",
    "get_scenario",
    "render_suite_report",
    "run_scenario",
    "run_suite",
    "small_problem_strategy",
]
