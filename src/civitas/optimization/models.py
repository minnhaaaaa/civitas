"""Pure in-memory models used by the optimization engine.

These models intentionally do not depend on persistence.  Quantities are integer
base units by the time an :class:`OptimizationProblem` reaches the solver.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from civitas.contracts.enums import FeasibilityStatus


class LotStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    DEPLETED = "depleted"


@dataclass(frozen=True, slots=True)
class PlanningBucket:
    bucket_id: str
    start: datetime
    end: datetime
    urgency: int = 1


@dataclass(frozen=True, slots=True)
class Demand:
    demand_id: str
    sku_id: str
    warehouse_id: str
    bucket_id: str
    quantity: int
    priority: int = 1
    minimum_service: int = 0


@dataclass(frozen=True, slots=True)
class InventoryLot:
    lot_id: str
    sku_id: str
    warehouse_id: str
    quantity: int
    expires_at: datetime
    status: LotStatus = LotStatus.AVAILABLE
    available_from: datetime | None = None
    unit_cost: int = 0


@dataclass(frozen=True, slots=True)
class SupplierOffer:
    offer_id: str
    supplier_id: str
    sku_id: str
    destination_warehouse_id: str
    arrival_bucket_id: str
    capacity: int
    unit_cost: int
    pack_size: int = 1
    minimum_order: int = 0
    expires_at: datetime | None = None
    risk: int = 0
    expected_waste_rate: int = 0


@dataclass(frozen=True, slots=True)
class WarehouseCapacity:
    warehouse_id: str
    bucket_id: str
    maximum_base_units: int


@dataclass(frozen=True, slots=True)
class TransportLane:
    lane_id: str
    source_warehouse_id: str
    destination_warehouse_id: str
    sku_ids: tuple[str, ...]
    capacity: int
    transit_buckets: int = 0
    unit_cost: int = 0


@dataclass(frozen=True, slots=True)
class OptimizationProblem:
    planning_run_id: str
    buckets: tuple[PlanningBucket, ...]
    demands: tuple[Demand, ...]
    inventory_lots: tuple[InventoryLot, ...] = ()
    supplier_offers: tuple[SupplierOffer, ...] = ()
    warehouse_capacities: tuple[WarehouseCapacity, ...] = ()
    transport_lanes: tuple[TransportLane, ...] = ()
    sku_volume: dict[str, int] = field(default_factory=dict)
    budget: int | None = None
    shortage_tolerance: int = 0
    maximum_alternatives: int = 5


@dataclass(frozen=True, slots=True)
class ProcurementDecision:
    offer_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    demand_id: str
    source_id: str
    source_kind: str
    quantity: int
    lane_id: str | None = None


@dataclass(frozen=True, slots=True)
class Alternative:
    alternative_id: str
    feasibility: FeasibilityStatus
    weighted_shortage: int
    shortage: int
    procurements: tuple[ProcurementDecision, ...]
    allocations: tuple[AllocationDecision, ...]
    metrics: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: FeasibilityStatus
    alternatives: tuple[Alternative, ...]
    optimal_weighted_shortage: int | None
    diagnostics: dict[str, object] = field(default_factory=dict)
