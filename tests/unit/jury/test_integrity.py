from datetime import UTC, datetime, timedelta

from civitas.contracts import (
    CandidatePlan,
    ClaimScope,
    EvidenceIdentity,
    EvidenceOrigin,
    EvidenceRecord,
    FeasibilityStatus,
    JuryRequest,
    TypedClaim,
)
from civitas.evidence import (
    DecisionIntegrityCalculator,
    DissentInvestigationPlan,
    DissentProtocol,
    GateFacts,
    IntegrityPolicyV1,
    JuryEvaluator,
    JuryInputs,
    ReasonCode,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


def claim(claim_id: str = "c1", value: int = 3) -> TypedClaim:
    return TypedClaim(
        claim_id=claim_id,
        subject="supplier-a",
        predicate="lead_time",
        value=value,
        unit="day",
        valid_at=NOW,
        scope=ClaimScope(organization_id="org-1", supplier_id="supplier-a"),
        human_summary=f"Supplier A lead time is {value} days.",
    )


def evidence(
    evidence_id: str,
    *,
    source_id: str = "source-a",
    source_type: str = "supplier_api",
    mcp_server: str | None = "supplier-mcp",
    tool_name: str | None = "get_lead_time",
    claim_ids: tuple[str, ...] = ("c1",),
    retrieved_at: datetime = NOW,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        claim_ids=claim_ids,
        identity=EvidenceIdentity(
            canonical_source_id=source_id,
            canonical_source_type=source_type,
            mcp_server=mcp_server,
            tool_name=tool_name,
            normalized_arguments={"supplier_id": "supplier-a"},
            retrieved_at=retrieved_at,
            observation_version="v1",
            raw_response_sha256="a" * 64,
        ),
        origin=EvidenceOrigin.EXTERNAL,
        content_summary="current supplier lead time",
    )


def completed_dissent(*, invalidates: bool = False):  # type: ignore[no-untyped-def]
    protocol = DissentProtocol()
    report = protocol.record_plan(
        DissentInvestigationPlan(
            context_id="dissent-thread-1",
            memory_namespace="dissent-memory-1",
            tool_cache_namespace="dissent-cache-1",
            checks=("verify lead time",),
            tool_budget=3,
        )
    )
    report = protocol.record_fresh_retrieval(report, evidence_ids=("fresh-evidence",))
    return protocol.compare_with_existing_graph(
        report,
        checked_claim_ids=("c1",),
        establishes_invalidity=invalidates,
    )


def request(
    *,
    feasibility: FeasibilityStatus = FeasibilityStatus.FULLY_FEASIBLE,
    policy_version: str = "decision-integrity-v1",
) -> JuryRequest:
    return JuryRequest(
        planning_run_id="run-1",
        candidate_plan=CandidatePlan(
            plan_id="plan-1",
            planning_run_id="run-1",
            feasibility=feasibility,
            shortage_base_units=0,
            solver_version="solver-1",
        ),
        supporting_claim_ids=("c1",),
        evidence_ids=("e1", "e2"),
        policy_version=policy_version,
    )


def sound_inputs() -> JuryInputs:
    return JuryInputs(
        claims=(claim(),),
        evidence=(
            evidence("e1", source_id="source-a", source_type="supplier_api"),
            evidence("e2", source_id="source-b", source_type="order_history"),
        ),
        dissent=completed_dissent(),
    )


def test_complete_independent_evidence_is_approve_eligible() -> None:
    evaluation = JuryEvaluator().evaluate(
        request(), sound_inputs(), evaluation_id="eval-1", calculated_at=NOW + timedelta(minutes=1)
    )

    assert evaluation.integrity_score == 100
    assert evaluation.state == "approve"
    assert all(gate.passed for gate in evaluation.gates)


def test_duplicate_canonical_source_groups_reduce_independence() -> None:
    inputs = JuryInputs(
        claims=(claim(),),
        evidence=(
            evidence("e1", source_id="same", tool_name="endpoint_one"),
            evidence("e2", source_id="same", tool_name="endpoint_two"),
        ),
        dissent=completed_dissent(),
    )

    result = DecisionIntegrityCalculator().calculate(
        inputs, calculated_at=NOW + timedelta(minutes=1), default_critical_claim_ids=("c1",)
    )

    assert result.components.evidence_independence == 50


def test_missing_mcp_provenance_lowers_only_provenance_component() -> None:
    complete = sound_inputs()
    incomplete = JuryInputs(
        claims=complete.claims,
        evidence=(
            evidence("e1", source_id="source-a", source_type="supplier_api", mcp_server=None),
            evidence("e2", source_id="source-b", source_type="order_history", tool_name=None),
        ),
        dissent=complete.dissent,
    )
    calculator = DecisionIntegrityCalculator()
    baseline = calculator.calculate(
        complete, calculated_at=NOW + timedelta(minutes=1), default_critical_claim_ids=("c1",)
    )
    result = calculator.calculate(
        incomplete, calculated_at=NOW + timedelta(minutes=1), default_critical_claim_ids=("c1",)
    )

    assert result.components.provenance_completeness < baseline.components.provenance_completeness
    assert result.components.critical_claim_coverage == baseline.components.critical_claim_coverage
    assert result.components.evidence_independence == baseline.components.evidence_independence


def test_infeasibility_gate_overrides_perfect_numeric_score() -> None:
    evaluation = JuryEvaluator().evaluate(
        request(feasibility=FeasibilityStatus.INFEASIBLE),
        sound_inputs(),
        evaluation_id="eval-reject",
        calculated_at=NOW + timedelta(minutes=1),
    )

    assert evaluation.integrity_score == 100
    assert evaluation.state == "reject"
    assert ReasonCode.SOLVER_INFEASIBLE in evaluation.reason_codes


def test_dissent_failure_gets_no_credit_and_fails_closed() -> None:
    protocol = DissentProtocol()
    failed = protocol.fail(
        protocol.record_plan(
            DissentInvestigationPlan(
                context_id="clean-room",
                memory_namespace="clean-memory",
                tool_cache_namespace="clean-cache",
                checks=("verify lead time",),
                tool_budget=1,
            )
        ),
        "supplier MCP unavailable",
    )
    inputs = JuryInputs(
        claims=sound_inputs().claims, evidence=sound_inputs().evidence, dissent=failed
    )

    evaluation = JuryEvaluator().evaluate(
        request(), inputs, evaluation_id="eval-dissent", calculated_at=NOW + timedelta(minutes=1)
    )

    assert evaluation.components.dissent_robustness == 0
    assert evaluation.state == "investigate"
    assert ReasonCode.DISSENT_CHECK_FAILED in evaluation.reason_codes
    assert evaluation.required_investigation


def test_historical_evaluations_retain_their_policy_and_implementation_versions() -> None:
    old_policy = IntegrityPolicyV1(version="integrity-v1.0", implementation_version="impl-a")
    new_policy = IntegrityPolicyV1(version="integrity-v1.1", implementation_version="impl-b")
    old_evaluation = JuryEvaluator(old_policy).evaluate(
        request(policy_version="integrity-v1.0"),
        sound_inputs(),
        evaluation_id="old",
        calculated_at=NOW + timedelta(minutes=1),
    )
    new_evaluation = JuryEvaluator(new_policy).evaluate(
        request(policy_version="integrity-v1.1"),
        sound_inputs(),
        evaluation_id="new",
        calculated_at=NOW + timedelta(minutes=1),
    )

    assert (old_evaluation.policy_version, old_evaluation.implementation_version) == (
        "integrity-v1.0",
        "impl-a",
    )
    assert (new_evaluation.policy_version, new_evaluation.implementation_version) == (
        "integrity-v1.1",
        "impl-b",
    )


def test_stale_execution_gate_investigates_even_when_score_is_high() -> None:
    base = sound_inputs()
    inputs = JuryInputs(
        claims=base.claims,
        evidence=base.evidence,
        dissent=base.dissent,
        gate_facts=GateFacts(stale_execution_claim_ids=("c1",)),
    )

    evaluation = JuryEvaluator().evaluate(
        request(), inputs, evaluation_id="stale", calculated_at=NOW + timedelta(minutes=1)
    )

    assert evaluation.state == "investigate"
    assert ReasonCode.STALE_EXECUTION_DATA in evaluation.reason_codes


def test_unresolved_critical_contradiction_gate_overrides_score() -> None:
    base = sound_inputs()
    inputs = JuryInputs(
        claims=(claim("c1", 3), claim("c2", 10)),
        evidence=base.evidence,
        dissent=base.dissent,
        critical_claim_ids=frozenset({"c1"}),
    )

    evaluation = JuryEvaluator().evaluate(
        request(), inputs, evaluation_id="conflict", calculated_at=NOW + timedelta(minutes=1)
    )

    assert evaluation.state == "investigate"
    assert ReasonCode.CRITICAL_CONTRADICTION_UNRESOLVED in evaluation.reason_codes
    assert "contradiction:c1:c2" in evaluation.required_investigation[0]


def test_evaluator_ignores_evidence_not_selected_by_request() -> None:
    inputs = JuryInputs(
        claims=(claim(),),
        evidence=(evidence("outside", source_id="unselected"),),
        dissent=completed_dissent(),
    )

    evaluation = JuryEvaluator().evaluate(
        request(), inputs, evaluation_id="scoped", calculated_at=NOW + timedelta(minutes=1)
    )

    assert evaluation.components.critical_claim_coverage == 0
    assert ReasonCode.CRITICAL_CLAIM_UNSUPPORTED in evaluation.reason_codes
