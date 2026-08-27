"""MCP-driven golden scenario checks with no live server or provider."""

from evaluation.mcp import run_mcp_scenario
from evaluation.scenarios import get_scenario


def test_false_consensus_remains_investigate_even_if_presentation_claims_approval() -> None:
    assessment = run_mcp_scenario(
        get_scenario("shared-source-false-consensus"),
        conversational_summary="The procurement plan is approved and ready to execute.",
    )

    assert assessment.decision.jury_state_match
    assert assessment.decision.actual_jury_state.value == "investigate"
    assert assessment.presentation_is_safe is False


def test_duplicate_execution_retry_is_deterministic_and_presentation_independent() -> None:
    assessment = run_mcp_scenario(
        get_scenario("duplicate-execution-retry"),
        conversational_summary="The retry returned the original execution receipt.",
    )

    assert assessment.decision.execution_state_match
    assert assessment.decision.actual_execution_state is not None
    assert assessment.presentation_is_safe
