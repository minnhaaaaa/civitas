"""Application-port adapter and JSON-to-solver input translation."""

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import cast

from civitas.contracts.common import JsonObject, Quantity
from civitas.contracts.optimization import (
    CandidatePlan,
    DistributionLine,
    OptimizationRequest,
    OptimizationResult,
    ProcurementLine,
)
from civitas.optimization.models import (
    Alternative,
    Demand,
    InventoryLot,
    LotStatus,
    OptimizationProblem,
    PlanningBucket,
    SupplierOffer,
    TransportLane,
    WarehouseCapacity,
)
from civitas.optimization.solver import SOLVER_VERSION, OptimizationEngine
from civitas.optimization.units import UnitConverter, UnitDefinition


class OrToolsOptimizer:
    """Implements the application optimizer port without database dependencies."""

    def __init__(self, engine: OptimizationEngine | None = None) -> None:
        self._engine = engine or OptimizationEngine()

    async def solve(self, request: OptimizationRequest) -> OptimizationResult:
        problem, base_unit, money_scale = problem_from_request(request)
        result = self._engine.solve(problem)
        input_version = hashlib.sha256(request.input_data_version.encode("utf-8")).hexdigest()[:12]
        alternatives = tuple(
            _to_contract(
                problem,
                item,
                base_unit,
                money_scale,
                plan_id=f"{item.alternative_id}-{input_version}",
            )
            for item in result.alternatives
        )
        diagnostics: JsonObject = {
            "status": result.status.value,
            "optimal_weighted_shortage": result.optimal_weighted_shortage,
            "solver_version": SOLVER_VERSION,
            "verified_alternatives": len(alternatives),
        }
        return OptimizationResult(
            planning_run_id=request.planning_run_id,
            alternatives=alternatives,
            diagnostics=diagnostics,
        )


def problem_from_request(
    request: OptimizationRequest,
) -> tuple[OptimizationProblem, str, Decimal]:
    """Translate the documented JSON payload into an integer problem.

    Quantities accept either an integer (already in base units) or
    ``{"value": "1.25", "unit": "kg"}``.  ``unit_definitions`` maps units to
    exact base-unit multipliers, for example ``{"kg": "1000", "g": "1"}``.
    Monetary values are multiplied by ``money_scale`` (default 100).
    """

    payload = cast(dict[str, object], request.constraints)
    base_unit = _string(payload, "base_unit", default="each")
    raw_definitions = _mapping(payload.get("unit_definitions", {base_unit: "1"}))
    definitions = tuple(
        UnitDefinition(
            unit=unit,
            base_unit=base_unit,
            base_units_per_unit=Decimal(str(multiplier)),
        )
        for unit, multiplier in sorted(raw_definitions.items())
    )
    converter = UnitConverter(definitions)
    money_scale = Decimal(str(payload.get("money_scale", "100")))
    if money_scale <= 0 or money_scale != money_scale.to_integral_value():
        raise ValueError("money_scale must be a positive integer")

    buckets = tuple(
        PlanningBucket(
            bucket_id=_string(item, "bucket_id"),
            start=_datetime(item, "start"),
            end=_datetime(item, "end"),
            urgency=_integer(item, "urgency", default=1),
        )
        for item in _records(payload, "buckets")
    )
    demands = tuple(
        Demand(
            demand_id=_string(item, "demand_id"),
            sku_id=_string(item, "sku_id"),
            warehouse_id=_string(item, "warehouse_id"),
            bucket_id=_string(item, "bucket_id"),
            quantity=_quantity(item, "quantity", converter, base_unit),
            priority=_integer(item, "priority", default=1),
            minimum_service=_quantity(item, "minimum_service", converter, base_unit, default=0),
        )
        for item in _records(payload, "demands")
    )
    lots = tuple(
        InventoryLot(
            lot_id=_string(item, "lot_id"),
            sku_id=_string(item, "sku_id"),
            warehouse_id=_string(item, "warehouse_id"),
            quantity=_quantity(item, "quantity", converter, base_unit),
            expires_at=_datetime(item, "expires_at"),
            status=LotStatus(_string(item, "status", default=LotStatus.AVAILABLE.value)),
            available_from=_optional_datetime(item, "available_from"),
            unit_cost=_money(item, "unit_cost", money_scale, default="0"),
        )
        for item in _records(payload, "inventory_lots", required=False)
    )
    offers = tuple(
        SupplierOffer(
            offer_id=_string(item, "offer_id"),
            supplier_id=_string(item, "supplier_id"),
            sku_id=_string(item, "sku_id"),
            destination_warehouse_id=_string(item, "destination_warehouse_id"),
            arrival_bucket_id=_string(item, "arrival_bucket_id"),
            capacity=_quantity(item, "capacity", converter, base_unit),
            unit_cost=_money(item, "unit_cost", money_scale),
            pack_size=_quantity(item, "pack_size", converter, base_unit, default=1),
            minimum_order=_quantity(item, "minimum_order", converter, base_unit, default=0),
            expires_at=_optional_datetime(item, "expires_at"),
            risk=_integer(item, "risk", default=0),
            expected_waste_rate=_integer(item, "expected_waste_rate", default=0),
        )
        for item in _records(payload, "supplier_offers", required=False)
    )
    capacities = tuple(
        WarehouseCapacity(
            warehouse_id=_string(item, "warehouse_id"),
            bucket_id=_string(item, "bucket_id"),
            maximum_base_units=_quantity(item, "maximum_base_units", converter, base_unit),
        )
        for item in _records(payload, "warehouse_capacities", required=False)
    )
    lanes = tuple(
        TransportLane(
            lane_id=_string(item, "lane_id"),
            source_warehouse_id=_string(item, "source_warehouse_id"),
            destination_warehouse_id=_string(item, "destination_warehouse_id"),
            sku_ids=tuple(_strings(item.get("sku_ids", []), "sku_ids")),
            capacity=_quantity(item, "capacity", converter, base_unit),
            transit_buckets=_integer(item, "transit_buckets", default=0),
            unit_cost=_money(item, "unit_cost", money_scale, default="0"),
        )
        for item in _records(payload, "transport_lanes", required=False)
    )
    raw_volume = _mapping(payload.get("sku_volume", {}))
    sku_volume = {
        key: _object_integer(value, f"sku_volume.{key}") for key, value in raw_volume.items()
    }
    budget = _money(payload, "budget", money_scale) if payload.get("budget") is not None else None
    return (
        OptimizationProblem(
            planning_run_id=request.planning_run_id,
            buckets=buckets,
            demands=demands,
            inventory_lots=lots,
            supplier_offers=offers,
            warehouse_capacities=capacities,
            transport_lanes=lanes,
            sku_volume=sku_volume,
            budget=budget,
            shortage_tolerance=_integer(payload, "shortage_tolerance", default=0),
            maximum_alternatives=request.maximum_alternatives,
        ),
        base_unit,
        money_scale,
    )


def _to_contract(
    problem: OptimizationProblem,
    alternative: Alternative,
    base_unit: str,
    money_scale: Decimal,
    *,
    plan_id: str,
) -> CandidatePlan:
    offer_by_id = {item.offer_id: item for item in problem.supplier_offers}
    demand_by_id = {item.demand_id: item for item in problem.demands}
    bucket_by_id = {item.bucket_id: item for item in problem.buckets}
    bucket_index = {item.bucket_id: index for index, item in enumerate(problem.buckets)}
    lane_by_id = {item.lane_id: item for item in problem.transport_lanes}
    procurement = tuple(
        ProcurementLine(
            supplier_id=offer_by_id[item.offer_id].supplier_id,
            sku_id=offer_by_id[item.offer_id].sku_id,
            destination_warehouse_id=offer_by_id[item.offer_id].destination_warehouse_id,
            arrival_bucket_start=bucket_by_id[offer_by_id[item.offer_id].arrival_bucket_id].start,
            quantity=Quantity(value=Decimal(item.quantity), unit=base_unit),
            landed_cost=(
                Decimal(item.quantity * offer_by_id[item.offer_id].unit_cost) / money_scale
            ),
        )
        for item in alternative.procurements
    )
    distribution: list[DistributionLine] = []
    lot_by_id = {item.lot_id: item for item in problem.inventory_lots}
    for item in alternative.allocations:
        if item.lane_id is None:
            continue
        lane = lane_by_id[item.lane_id]
        demand = demand_by_id[item.demand_id]
        arrival_index = bucket_index[demand.bucket_id]
        departure_index = arrival_index - lane.transit_buckets
        distribution.append(
            DistributionLine(
                sku_id=demand.sku_id,
                source_warehouse_id=lane.source_warehouse_id,
                destination_warehouse_id=lane.destination_warehouse_id,
                departure_bucket_start=problem.buckets[departure_index].start,
                arrival_bucket_start=problem.buckets[arrival_index].start,
                quantity=Quantity(value=Decimal(item.quantity), unit=base_unit),
                source_lot_ids=(lot_by_id[item.source_id].lot_id,),
            )
        )
    return CandidatePlan(
        plan_id=plan_id,
        planning_run_id=problem.planning_run_id,
        feasibility=alternative.feasibility,
        procurement=procurement,
        distribution=tuple(distribution),
        shortage_base_units=alternative.shortage,
        metrics=alternative.metrics,
        solver_version=SOLVER_VERSION,
    )


def _records(
    payload: dict[str, object], key: str, *, required: bool = True
) -> list[dict[str, object]]:
    value = payload.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects")
    return cast(list[dict[str, object]], value)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("expected an object with string keys")
    return cast(dict[str, object], value)


def _string(payload: dict[str, object], key: str, *, default: str | None = None) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _strings(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return cast(list[str], value)


def _integer(payload: dict[str, object], key: str, *, default: int | None = None) -> int:
    value = payload.get(key, default)
    return _object_integer(value, key)


def _object_integer(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _datetime(payload: dict[str, object], key: str) -> datetime:
    value = _string(payload, key)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{key} must include a timezone")
    return parsed


def _optional_datetime(payload: dict[str, object], key: str) -> datetime | None:
    return None if payload.get(key) is None else _datetime(payload, key)


def _quantity(
    payload: dict[str, object],
    key: str,
    converter: UnitConverter,
    base_unit: str,
    *,
    default: int | None = None,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a quantity")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{key} cannot be negative")
        return value
    quantity = _mapping(value)
    amount = Decimal(_string(quantity, "value"))
    unit = _string(quantity, "unit", default=base_unit)
    converted, converted_unit = converter.to_base_units(amount, unit)
    if converted_unit != base_unit:
        raise ValueError(f"{key} converts to an unexpected base unit")
    return converted


def _money(
    payload: dict[str, object], key: str, scale: Decimal, *, default: str | None = None
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{key} must be a decimal monetary value")
    scaled = Decimal(str(value)) * scale
    if scaled < 0 or scaled != scaled.to_integral_value():
        raise ValueError(f"{key} is not exactly representable at money_scale")
    return int(scaled)
