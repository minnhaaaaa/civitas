"""MCP-facing golden evaluation kept separate from conversational presentation."""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.runner import ScenarioResult, run_scenario
from evaluation.scenarios import GoldenScenario


@dataclass(frozen=True, slots=True)
class MCPScenarioAssessment:
    """A deterministic decision result plus a non-authoritative presentation check."""

    scenario_id: str
    decision: ScenarioResult
    presentation_is_safe: bool


def run_mcp_scenario(
    scenario: GoldenScenario,
    *,
    conversational_summary: str = "",
) -> MCPScenarioAssessment:
    """Run the deterministic system checks without scoring prose as decision quality.

    Presentation is limited to verifying that a supplied human-facing summary
    does not claim approval for a non-approved Jury state.  Solver, lineage,
    Jury, and execution outcomes remain in ``decision`` and are never derived
    from that text.
    """

    decision = run_scenario(scenario)
    claims_approval = "approved" in conversational_summary.casefold()
    presentation_is_safe = not claims_approval or decision.actual_jury_state.value == "approve"
    return MCPScenarioAssessment(
        scenario_id=scenario.manifest.scenario_id,
        decision=decision,
        presentation_is_safe=presentation_is_safe,
    )
