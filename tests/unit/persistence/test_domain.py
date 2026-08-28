from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from civitas.domain.inventory import ExpiryKind, InventoryLot
from civitas.domain.planning import Organization
from civitas.persistence.models import Base


def test_domain_entities_are_immutable_and_orm_free() -> None:
    organization = Organization(id="org", name="Civitas", timezone="UTC")

    with pytest.raises(FrozenInstanceError):
        organization.name = "changed"  # type: ignore[misc]

    assert not hasattr(organization, "__table__")


def test_inventory_lot_uses_exact_decimal_quantity() -> None:
    lot = InventoryLot(
        id="lot",
        organization_id="org",
        sku_id="sku",
        warehouse_id="warehouse",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 8, tzinfo=UTC),
        expiry_kind=ExpiryKind.USE_BY,
        initial_quantity=Decimal("10.125"),
        unit_of_measure="kg",
    )

    assert lot.initial_quantity == Decimal("10.125")


def test_metadata_contains_canonical_persistence_tables() -> None:
    required = {
        "organizations",
        "skus",
        "warehouses",
        "suppliers",
        "planning_runs",
        "planning_buckets",
        "demand_forecasts",
        "supplier_offers",
        "inventory_lots",
        "inventory_movements",
        "inventory_reservations",
        "sources",
        "mcp_calls",
        "claims",
        "evidence",
        "evidence_claims",
        "lineage_edges",
        "candidate_plans",
        "jury_decisions",
        "workflow_events",
        "workflow_checkpoints",
        "execution_audits",
    }

    assert required <= set(Base.metadata.tables)
