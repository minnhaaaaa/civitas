from datetime import UTC, datetime, timedelta

from civitas.contracts import ClaimScope, TypedClaim, ValidityInterval
from civitas.evidence import detect_contradictions, normalize_claim

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


def claim(
    claim_id: str,
    value: int,
    *,
    unit: str = "days",
    starts_at: datetime = NOW,
    ends_at: datetime = NOW + timedelta(hours=1),
) -> TypedClaim:
    return TypedClaim(
        claim_id=claim_id,
        subject=" Supplier-A ",
        predicate="Lead-Time-Days",
        value=value,
        unit=unit,
        valid_during=ValidityInterval(starts_at=starts_at, ends_at=ends_at),
        scope=ClaimScope(organization_id="ORG-1", supplier_id="Supplier-A"),
        human_summary=f"Supplier A has a {value} day lead time.",
    )


def test_normalizes_typed_claim_fields_and_convertible_units() -> None:
    normalized = normalize_claim(claim("c1", 48, unit="hours"))

    assert normalized.subject == "supplier_a"
    assert normalized.predicate == "lead_time"
    assert normalized.value == 2
    assert normalized.unit == "day"
    assert normalized.scope.organization_id == "org_1"
    assert normalize_claim(normalized) == normalized


def test_detects_conflicts_only_for_same_scope_and_overlapping_validity() -> None:
    conflict = claim("c1", 3)
    conflicting = claim("c2", 10)
    later = claim(
        "c3",
        5,
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=2),
    )

    contradictions = detect_contradictions(
        (conflict, conflicting, later), critical_claim_ids=frozenset({"c1"})
    )

    assert [item.contradiction_id for item in contradictions] == ["contradiction:c1:c2"]
    assert contradictions[0].severity == "high"


def test_equivalent_units_do_not_contradict() -> None:
    assert detect_contradictions((claim("c1", 2), claim("c2", 48, unit="hours"))) == ()
