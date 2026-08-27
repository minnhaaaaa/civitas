"""Deterministic contradiction detection over normalized typed claims."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

from civitas.contracts.claims import TypedClaim
from civitas.evidence.claims import (
    normalized_claim_key,
    normalized_claim_value,
    validity_overlaps,
)


class ContradictionSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class Contradiction:
    contradiction_id: str
    left_claim_id: str
    right_claim_id: str
    severity: ContradictionSeverity
    reason_code: str = "CONFLICTING_TYPED_VALUES"


def detect_contradictions(
    claims: Iterable[TypedClaim],
    *,
    critical_claim_ids: frozenset[str] = frozenset(),
) -> tuple[Contradiction, ...]:
    """Find overlapping claims with equal typed keys and unequal typed values."""

    grouped: dict[tuple[object, ...], list[TypedClaim]] = {}
    for claim in claims:
        grouped.setdefault(normalized_claim_key(claim), []).append(claim)

    contradictions: list[Contradiction] = []
    for group in grouped.values():
        for left, right in combinations(sorted(group, key=lambda item: item.claim_id), 2):
            if not validity_overlaps(left, right):
                continue
            if normalized_claim_value(left) == normalized_claim_value(right):
                continue
            critical = left.claim_id in critical_claim_ids or right.claim_id in critical_claim_ids
            severity = ContradictionSeverity.HIGH if critical else ContradictionSeverity.MEDIUM
            pair = sorted((left.claim_id, right.claim_id))
            contradictions.append(
                Contradiction(
                    contradiction_id=f"contradiction:{pair[0]}:{pair[1]}",
                    left_claim_id=pair[0],
                    right_claim_id=pair[1],
                    severity=severity,
                )
            )
    return tuple(sorted(contradictions, key=lambda item: item.contradiction_id))
