import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from civitas.domain.inventory import ExpiryKind, InventoryLot
from civitas.domain.planning import Organization
from civitas.persistence.database import Database
from civitas.persistence.inventory import (
    DuplicateReservation,
    InsufficientInventory,
    InventoryService,
)
from civitas.persistence.models import OrganizationModel, SKUModel, WarehouseModel


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


async def seed_inventory(
    database: Database,
    quantities_and_expiries: list[tuple[Decimal, datetime]],
) -> tuple[str, str, str, list[str]]:
    organization_id = identifier("org")
    sku_id = identifier("sku")
    warehouse_id = identifier("warehouse")
    lot_ids: list[str] = []
    async with database.unit_of_work() as uow:
        session = uow.require_session()
        session.add(OrganizationModel(id=organization_id, name="Org", timezone="UTC"))
        session.add(
            SKUModel(
                id=sku_id,
                organization_id=organization_id,
                code=identifier("code"),
                name="Apples",
                unit_of_measure="kg",
                base_unit_scale=1000,
                attributes={},
            )
        )
        session.add(
            WarehouseModel(
                id=warehouse_id,
                organization_id=organization_id,
                code=identifier("code"),
                name="Central",
                timezone="UTC",
                attributes={},
            )
        )
        await session.flush()
        inventory = InventoryService(session)
        for index, (quantity, expires_at) in enumerate(quantities_and_expiries):
            lot_id = identifier("lot")
            lot_ids.append(lot_id)
            await inventory.receive_lot(
                InventoryLot(
                    id=lot_id,
                    organization_id=organization_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                    received_at=expires_at - timedelta(days=10),
                    expires_at=expires_at,
                    expiry_kind=ExpiryKind.USE_BY,
                    initial_quantity=quantity,
                    unit_of_measure="kg",
                ),
                movement_id=identifier("movement"),
                idempotency_key=identifier(f"receipt-{index}"),
                business_reference=identifier("receipt"),
            )
    return organization_id, sku_id, warehouse_id, lot_ids


@pytest.mark.asyncio(loop_scope="session")
async def test_migrations_build_empty_database(database: Database) -> None:
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    assert "inventory_movements" in tables
    assert "execution_audits" in tables
    assert "alembic_version" in tables


@pytest.mark.asyncio(loop_scope="session")
async def test_unit_of_work_commits_repository_entities(database: Database) -> None:
    organization = Organization(id=identifier("org"), name="Repository Org", timezone="UTC")
    async with database.unit_of_work() as uow:
        await uow.organizations.add(organization)

    async with database.unit_of_work() as uow:
        restored = await uow.organizations.get(organization.id)

    assert restored == organization


@pytest.mark.asyncio(loop_scope="session")
async def test_lot_balances_reconcile_and_reservations_use_fefo(database: Database) -> None:
    now = datetime.now(UTC)
    organization_id, sku_id, warehouse_id, lot_ids = await seed_inventory(
        database,
        [(Decimal("5"), now + timedelta(days=2)), (Decimal("10"), now + timedelta(days=5))],
    )
    async with database.unit_of_work() as uow:
        inventory = InventoryService(uow.require_session())
        allocations = await inventory.reserve_fefo(
            organization_id=organization_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("8"),
            occurred_at=now,
            business_reference=identifier("order"),
            idempotency_key=identifier("reserve"),
        )
        assert [(row.lot_id, row.quantity) for row in allocations] == [
            (lot_ids[0], Decimal("5")),
            (lot_ids[1], Decimal("3")),
        ]
        reconciled = [await inventory.reconcile_lot(lot_id) for lot_id in lot_ids]
        assert all(reconciled)

    async with database.unit_of_work() as uow:
        balances = await InventoryService(uow.require_session()).balances(
            organization_id=organization_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            as_of=now,
        )
        assert [row.available for row in balances] == [Decimal("0"), Decimal("7")]


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_reservation_key_is_rejected(database: Database) -> None:
    now = datetime.now(UTC)
    organization_id, sku_id, warehouse_id, _ = await seed_inventory(
        database, [(Decimal("10"), now + timedelta(days=2))]
    )
    key = identifier("same-key")
    async with database.unit_of_work() as uow:
        await InventoryService(uow.require_session()).reserve_fefo(
            organization_id=organization_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("2"),
            occurred_at=now,
            business_reference="order-1",
            idempotency_key=key,
        )

    with pytest.raises(DuplicateReservation):
        async with database.unit_of_work() as uow:
            await InventoryService(uow.require_session()).reserve_fefo(
                organization_id=organization_id,
                sku_id=sku_id,
                warehouse_id=warehouse_id,
                quantity=Decimal("2"),
                occurred_at=now,
                business_reference="order-1",
                idempotency_key=key,
            )


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_reservations_cannot_over_allocate(database: Database) -> None:
    now = datetime.now(UTC)
    organization_id, sku_id, warehouse_id, _ = await seed_inventory(
        database, [(Decimal("10"), now + timedelta(days=2))]
    )

    async def reserve(key: str) -> str:
        try:
            async with database.unit_of_work() as uow:
                await InventoryService(uow.require_session()).reserve_fefo(
                    organization_id=organization_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                    quantity=Decimal("8"),
                    occurred_at=now,
                    business_reference=key,
                    idempotency_key=key,
                )
            return "reserved"
        except InsufficientInventory:
            return "insufficient"

    outcomes = await asyncio.gather(reserve(identifier("a")), reserve(identifier("b")))

    assert sorted(outcomes) == ["insufficient", "reserved"]
    async with database.unit_of_work() as uow:
        balances = await InventoryService(uow.require_session()).balances(
            organization_id=organization_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            as_of=now,
        )
        assert sum((row.reserved for row in balances), Decimal("0")) == Decimal("8")
