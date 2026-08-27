"""Perishable inventory domain entities."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ExpiryKind(StrEnum):
    USE_BY = "use_by"
    BEST_BEFORE = "best_before"


class InventoryStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    DEPLETED = "depleted"


class MovementType(StrEnum):
    RECEIPT = "receipt"
    RESERVATION = "reservation"
    RELEASE = "release"
    SHIPMENT = "shipment"
    TRANSFER = "transfer"
    WASTE = "waste"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class InventoryLot:
    id: str
    organization_id: str
    sku_id: str
    warehouse_id: str
    received_at: datetime
    expires_at: datetime
    expiry_kind: ExpiryKind
    initial_quantity: Decimal
    unit_of_measure: str
    status: InventoryStatus = InventoryStatus.AVAILABLE
    manufactured_at: datetime | None = None
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class InventoryMovement:
    id: str
    lot_id: str
    movement_type: MovementType
    quantity: Decimal
    occurred_at: datetime
    business_reference: str
    idempotency_key: str
