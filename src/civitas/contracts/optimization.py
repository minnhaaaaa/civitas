"""Solver input and output boundary contracts."""

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject, Quantity
from civitas.contracts.enums import FeasibilityStatus


class ProcurementLine(Contract):
    supplier_id: str
    sku_id: str
    destination_warehouse_id: str
    arrival_bucket_start: datetime
    quantity: Quantity
    landed_cost: Decimal = Field(ge=0)


class DistributionLine(Contract):
    sku_id: str
    source_warehouse_id: str
    destination_warehouse_id: str
    departure_bucket_start: datetime
    arrival_bucket_start: datetime
    quantity: Quantity
    source_lot_ids: tuple[str, ...] = ()


class CandidatePlan(Contract):
    plan_id: str
    planning_run_id: str
    feasibility: FeasibilityStatus
    procurement: tuple[ProcurementLine, ...] = ()
    distribution: tuple[DistributionLine, ...] = ()
    shortage_base_units: int = Field(ge=0)
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    solver_version: str


class OptimizationRequest(Contract):
    planning_run_id: str
    input_data_version: str
    objectives_version: str
    constraints: JsonObject
    maximum_alternatives: int = Field(default=5, ge=1, le=20)


class OptimizationResult(Contract):
    planning_run_id: str
    alternatives: tuple[CandidatePlan, ...]
    diagnostics: JsonObject = Field(default_factory=dict)
