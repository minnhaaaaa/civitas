"""Auditable lot ledger, FEFO queries, and transactional reservation."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import and_, case, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from civitas.domain.inventory import InventoryLot
from civitas.persistence.models import (
    InventoryLotModel,
    InventoryMovementModel,
    InventoryReservationModel,
    OrganizationModel,
)


@dataclass(frozen=True, slots=True)
class LotBalance:
    lot_id: str
    sku_id: str
    warehouse_id: str
    expires_at: datetime
    on_hand: Decimal
    reserved: Decimal

    @property
    def available(self) -> Decimal:
        return self.on_hand - self.reserved


@dataclass(frozen=True, slots=True)
class FEFOAllocation:
    lot_id: str
    quantity: Decimal
    expires_at: datetime


class InsufficientInventory(Exception):
    def __init__(self, requested: Decimal, available: Decimal) -> None:
        super().__init__(f"requested {requested}; only {available} available")
        self.requested = requested
        self.available = available


class DuplicateReservation(Exception):
    pass


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def receive_lot(
        self,
        lot: InventoryLot,
        *,
        movement_id: str,
        idempotency_key: str,
        business_reference: str,
    ) -> None:
        self._session.add(
            InventoryLotModel(
                id=lot.id,
                organization_id=lot.organization_id,
                sku_id=lot.sku_id,
                warehouse_id=lot.warehouse_id,
                received_at=lot.received_at,
                manufactured_at=lot.manufactured_at,
                expires_at=lot.expires_at,
                expiry_kind=lot.expiry_kind.value,
                initial_quantity=lot.initial_quantity,
                unit_of_measure=lot.unit_of_measure,
                status=lot.status.value,
                source_reference=lot.source_reference,
            )
        )
        self._session.add(
            InventoryMovementModel(
                id=movement_id,
                organization_id=lot.organization_id,
                lot_id=lot.id,
                movement_type="receipt",
                quantity_delta=lot.initial_quantity,
                reserved_delta=Decimal("0"),
                occurred_at=lot.received_at,
                business_reference=business_reference,
                idempotency_key=idempotency_key,
            )
        )

    async def balances(
        self,
        *,
        organization_id: str,
        sku_id: str | None = None,
        warehouse_id: str | None = None,
        as_of: datetime | None = None,
        eligible_only: bool = False,
        lock_lots: bool = False,
    ) -> tuple[LotBalance, ...]:
        quantity = func.coalesce(func.sum(InventoryMovementModel.quantity_delta), 0)
        reserved = func.coalesce(func.sum(InventoryMovementModel.reserved_delta), 0)
        statement = (
            select(InventoryLotModel, quantity.label("on_hand"), reserved.label("reserved"))
            .outerjoin(
                InventoryMovementModel,
                (InventoryMovementModel.lot_id == InventoryLotModel.id)
                & (true() if as_of is None else InventoryMovementModel.occurred_at <= as_of),
            )
            .where(InventoryLotModel.organization_id == organization_id)
            .group_by(InventoryLotModel.id)
            .order_by(InventoryLotModel.expires_at, InventoryLotModel.id)
        )
        if sku_id is not None:
            statement = statement.where(InventoryLotModel.sku_id == sku_id)
        if warehouse_id is not None:
            statement = statement.where(InventoryLotModel.warehouse_id == warehouse_id)
        if eligible_only:
            if as_of is None:
                raise ValueError("as_of is required for eligible balance queries")
            statement = statement.where(
                InventoryLotModel.status.in_(("available", "reserved")),
                InventoryLotModel.expires_at > as_of,
            )
        if lock_lots:
            lot_ids = select(InventoryLotModel.id).where(
                InventoryLotModel.organization_id == organization_id
            )
            if sku_id is not None:
                lot_ids = lot_ids.where(InventoryLotModel.sku_id == sku_id)
            if warehouse_id is not None:
                lot_ids = lot_ids.where(InventoryLotModel.warehouse_id == warehouse_id)
            await self._session.execute(lot_ids.with_for_update())
        rows = (await self._session.execute(statement)).all()
        return tuple(
            LotBalance(
                lot_id=lot.id,
                sku_id=lot.sku_id,
                warehouse_id=lot.warehouse_id,
                expires_at=lot.expires_at,
                on_hand=Decimal(on_hand),
                reserved=Decimal(reserved),
            )
            for lot, on_hand, reserved in rows
        )

    async def reserve_fefo(
        self,
        *,
        organization_id: str,
        sku_id: str,
        warehouse_id: str,
        quantity: Decimal,
        occurred_at: datetime,
        business_reference: str,
        idempotency_key: str,
    ) -> tuple[FEFOAllocation, ...]:
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        # Organization lock serializes idempotency checks and stock allocation for the tenant.
        await self._session.execute(
            select(OrganizationModel.id)
            .where(OrganizationModel.id == organization_id)
            .with_for_update()
        )
        duplicate = await self._session.scalar(
            select(func.count())
            .select_from(InventoryReservationModel)
            .where(
                InventoryReservationModel.organization_id == organization_id,
                InventoryReservationModel.idempotency_key == idempotency_key,
            )
        )
        if duplicate:
            raise DuplicateReservation(idempotency_key)

        balances = await self.balances(
            organization_id=organization_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            as_of=occurred_at,
            eligible_only=True,
            lock_lots=True,
        )
        total_available = sum((max(row.available, Decimal("0")) for row in balances), Decimal("0"))
        if total_available < quantity:
            raise InsufficientInventory(quantity, total_available)

        unit = await self._session.scalar(
            select(InventoryLotModel.unit_of_measure)
            .where(
                and_(
                    InventoryLotModel.organization_id == organization_id,
                    InventoryLotModel.sku_id == sku_id,
                    InventoryLotModel.warehouse_id == warehouse_id,
                )
            )
            .limit(1)
        )
        if unit is None:
            raise InsufficientInventory(quantity, Decimal("0"))
        self._session.add(
            InventoryReservationModel(
                id=str(uuid4()),
                organization_id=organization_id,
                sku_id=sku_id,
                warehouse_id=warehouse_id,
                quantity=quantity,
                unit_of_measure=unit,
                occurred_at=occurred_at,
                business_reference=business_reference,
                idempotency_key=idempotency_key,
            )
        )

        remaining = quantity
        allocations: list[FEFOAllocation] = []
        for row in balances:
            allocation = min(max(row.available, Decimal("0")), remaining)
            if allocation == 0:
                continue
            allocations.append(FEFOAllocation(row.lot_id, allocation, row.expires_at))
            self._session.add(
                InventoryMovementModel(
                    id=str(uuid4()),
                    organization_id=organization_id,
                    lot_id=row.lot_id,
                    movement_type="reservation",
                    quantity_delta=Decimal("0"),
                    reserved_delta=allocation,
                    occurred_at=occurred_at,
                    business_reference=business_reference,
                    idempotency_key=idempotency_key,
                )
            )
            remaining -= allocation
            if remaining == 0:
                break
        return tuple(allocations)

    async def reconcile_lot(self, lot_id: str) -> bool:
        statement = (
            select(
                InventoryLotModel.initial_quantity,
                func.coalesce(
                    func.sum(
                        case(
                            (
                                InventoryMovementModel.movement_type == "receipt",
                                InventoryMovementModel.quantity_delta,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .join(InventoryMovementModel, InventoryMovementModel.lot_id == InventoryLotModel.id)
            .where(InventoryLotModel.id == lot_id)
            .group_by(InventoryLotModel.id)
        )
        row = (await self._session.execute(statement)).one_or_none()
        return row is not None and Decimal(row[0]) == Decimal(row[1])
