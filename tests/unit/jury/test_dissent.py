import pytest

from civitas.evidence import DissentInvestigationPlan, DissentProtocol


def plan() -> DissentInvestigationPlan:
    return DissentInvestigationPlan(
        context_id="separate-thread",
        memory_namespace="separate-memory",
        tool_cache_namespace="separate-cache",
        checks=("verify capacity",),
        tool_budget=2,
    )


def test_existing_graph_cannot_be_revealed_before_fresh_retrieval() -> None:
    report = DissentProtocol.record_plan(plan())

    with pytest.raises(ValueError, match="only after fresh retrieval"):
        DissentProtocol.compare_with_existing_graph(report, checked_claim_ids=("c1",))


def test_unavailable_required_check_earns_zero_robustness() -> None:
    report = DissentProtocol.record_plan(plan())
    report = DissentProtocol.record_fresh_retrieval(
        report,
        evidence_ids=("e-fresh",),
        unavailable_checks=("verify capacity",),
    )
    report = DissentProtocol.compare_with_existing_graph(report, checked_claim_ids=("c1",))

    assert not report.completed
    assert report.robustness_score == 0


def test_bare_comparison_statement_without_checked_claims_earns_no_credit() -> None:
    report = DissentProtocol.record_plan(plan())
    report = DissentProtocol.record_fresh_retrieval(report, evidence_ids=("e-fresh",))
    report = DissentProtocol.compare_with_existing_graph(report, checked_claim_ids=())

    assert not report.completed
    assert report.robustness_score == 0


def test_write_access_is_rejected() -> None:
    with pytest.raises(ValueError, match="read-only"):
        DissentInvestigationPlan(
            context_id="thread",
            memory_namespace="memory",
            tool_cache_namespace="cache",
            checks=("check",),
            tool_budget=1,
            read_only=False,
        )
