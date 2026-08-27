"""Core planning domain entities without persistence dependencies."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any

EMPTY_METADATA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Organization:
    id: str
    name: str
    timezone: str


@dataclass(frozen=True, slots=True)
class SKU:
    id: str
    organization_id: str
    code: str
    name: str
    unit_of_measure: str
    base_unit_scale: int = 1
    metadata: Mapping[str, Any] = field(default_factory=lambda: EMPTY_METADATA)


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: str
    organization_id: str
    code: str
    name: str
    timezone: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: EMPTY_METADATA)


@dataclass(frozen=True, slots=True)
class Supplier:
    id: str
    organization_id: str
    code: str
    name: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: EMPTY_METADATA)


@dataclass(frozen=True, slots=True)
class PlanningRun:
    id: str
    organization_id: str
    horizon_start: datetime
    horizon_end: datetime
    bucket_duration: timedelta
    timezone: str
    input_data_version: str
    status: str = "created"


@dataclass(frozen=True, slots=True)
class PlanningBucket:
    id: str
    planning_run_id: str
    sequence: int
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class DemandForecast:
    id: str
    planning_run_id: str
    bucket_id: str
    sku_id: str
    warehouse_id: str
    quantity: Decimal
    unit_of_measure: str
    priority: Decimal = Decimal("1")
    source_version: str = "1"
    raw_payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SupplierOffer:
    id: str
    planning_run_id: str
    supplier_id: str
    sku_id: str
    destination_warehouse_id: str
    valid_from: datetime
    valid_until: datetime
    available_quantity: Decimal
    unit_of_measure: str
    unit_price: Decimal
    currency: str
    lead_time: timedelta
    minimum_order_quantity: Decimal = Decimal("0")
    pack_size: Decimal = Decimal("1")
    expected_shelf_life: timedelta | None = None
    source_version: str = "1"
    raw_payload: Mapping[str, Any] | None = None
