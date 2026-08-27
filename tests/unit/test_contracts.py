from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from civitas.contracts import ClaimScope, TypedClaim


def test_typed_claim_requires_one_validity_form() -> None:
    claim = TypedClaim(
        claim_id="claim-1",
        subject="supplier-1",
        predicate="lead_time",
        value=3,
        unit="day",
        valid_at=datetime(2026, 8, 27, tzinfo=UTC),
        scope=ClaimScope(organization_id="org-1", supplier_id="supplier-1"),
        human_summary="Supplier 1 has a three-day lead time.",
    )

    assert claim.value == 3


def test_typed_claim_rejects_missing_validity() -> None:
    with pytest.raises(ValidationError):
        TypedClaim(
            claim_id="claim-1",
            subject="supplier-1",
            predicate="lead_time",
            value=3,
            unit="day",
            scope=ClaimScope(organization_id="org-1"),
            human_summary="Supplier 1 has a three-day lead time.",
        )
