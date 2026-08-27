from datetime import UTC, datetime

from civitas.contracts import (
    ClaimScope,
    EvidenceIdentity,
    EvidenceOrigin,
    EvidenceRecord,
    TypedClaim,
)
from civitas.evidence import EvidenceGraphProjector, LineageAnalyzer

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
HASH = "a" * 64


def typed_claim() -> TypedClaim:
    return TypedClaim(
        claim_id="claim-lead-time",
        subject="supplier-a",
        predicate="lead_time",
        value=3,
        unit="day",
        valid_at=NOW,
        scope=ClaimScope(organization_id="org-1", supplier_id="supplier-a"),
        human_summary="Supplier A lead time is three days.",
    )


def evidence(
    evidence_id: str,
    *,
    canonical_source: str,
    endpoint: str,
    origin: EvidenceOrigin = EvidenceOrigin.EXTERNAL,
    derived_from: tuple[str, ...] = (),
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        claim_ids=("claim-lead-time",),
        identity=EvidenceIdentity(
            canonical_source_id=canonical_source,
            canonical_source_type="supplier_operational_record",
            mcp_server="supplier-mcp",
            tool_name=endpoint,
            normalized_arguments={"supplier_id": "supplier-a"},
            retrieved_at=NOW,
            raw_response_sha256=HASH,
        ),
        origin=origin,
        agent_id="supplier-agent",
        content_summary="lead time evidence",
        derived_from=derived_from,
    )


def test_duplicate_endpoints_backed_by_one_upstream_source_count_once() -> None:
    records = (
        evidence("e1", canonical_source="supplier-db", endpoint="get_supplier"),
        evidence("e2", canonical_source="supplier-db", endpoint="get_lead_time"),
    )
    analyzer = LineageAnalyzer(EvidenceGraphProjector().project((typed_claim(),), records))

    assert analyzer.claim_source_groups("claim-lead-time") == frozenset(
        {"supplier_operational_record:supplier-db"}
    )


def test_agent_echoes_resolve_to_external_ancestor_without_increasing_independence() -> None:
    records = (
        evidence("external", canonical_source="supplier-db", endpoint="get_lead_time"),
        evidence(
            "echo-1",
            canonical_source="agent-memory-1",
            endpoint="read_memory",
            origin=EvidenceOrigin.AGENT_DERIVED,
            derived_from=("external",),
        ),
        evidence(
            "echo-2",
            canonical_source="agent-memory-2",
            endpoint="read_memory",
            origin=EvidenceOrigin.AGENT_DERIVED,
            derived_from=("echo-1",),
        ),
    )
    analyzer = LineageAnalyzer(EvidenceGraphProjector().project((typed_claim(),), records))

    assert analyzer.effective_source_groups("echo-2") == frozenset(
        {"supplier_operational_record:supplier-db"}
    )
    assert len(analyzer.claim_source_groups("claim-lead-time")) == 1


def test_missing_parent_marks_lineage_incomplete() -> None:
    record = evidence(
        "echo",
        canonical_source="agent-memory",
        endpoint="read_memory",
        origin=EvidenceOrigin.AGENT_DERIVED,
        derived_from=("missing",),
    )
    analyzer = LineageAnalyzer(EvidenceGraphProjector().project((typed_claim(),), (record,)))

    assert not analyzer.has_complete_lineage("echo")
    assert analyzer.effective_source_groups("echo") == frozenset()
