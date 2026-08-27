"""Validation for database-independent optimization inputs."""

from collections import Counter

from civitas.optimization.models import OptimizationProblem


def validate_problem(problem: OptimizationProblem) -> None:
    if not problem.buckets:
        raise ValueError("at least one planning bucket is required")
    if not 1 <= problem.maximum_alternatives <= 20:
        raise ValueError("maximum_alternatives must be between 1 and 20")
    if problem.shortage_tolerance < 0:
        raise ValueError("shortage_tolerance cannot be negative")
    if problem.budget is not None and problem.budget < 0:
        raise ValueError("budget cannot be negative")

    _unique("bucket_id", [item.bucket_id for item in problem.buckets])
    _unique("demand_id", [item.demand_id for item in problem.demands])
    _unique("lot_id", [item.lot_id for item in problem.inventory_lots])
    _unique("offer_id", [item.offer_id for item in problem.supplier_offers])
    _unique("lane_id", [item.lane_id for item in problem.transport_lanes])
    source_ids = [item.lot_id for item in problem.inventory_lots] + [
        item.offer_id for item in problem.supplier_offers
    ]
    _unique("source ID across lots and offers", source_ids)
    _unique(
        "warehouse capacity key",
        [f"{item.warehouse_id}:{item.bucket_id}" for item in problem.warehouse_capacities],
    )

    ordered = sorted(problem.buckets, key=lambda item: (item.start, item.bucket_id))
    if list(problem.buckets) != ordered:
        raise ValueError("buckets must be ordered by start time and bucket_id")
    for bucket in problem.buckets:
        if bucket.start >= bucket.end:
            raise ValueError(f"bucket {bucket.bucket_id} has an invalid interval")
        if bucket.urgency <= 0:
            raise ValueError("bucket urgency must be positive")
    for left, right in zip(problem.buckets, problem.buckets[1:], strict=False):
        if left.end > right.start:
            raise ValueError("planning buckets cannot overlap")

    bucket_ids = {item.bucket_id for item in problem.buckets}
    for demand in problem.demands:
        if demand.bucket_id not in bucket_ids:
            raise ValueError(f"demand {demand.demand_id} references an unknown bucket")
        if demand.quantity < 0 or demand.minimum_service < 0:
            raise ValueError("demand quantities cannot be negative")
        if demand.minimum_service > demand.quantity:
            raise ValueError("minimum service cannot exceed demand")
        if demand.priority <= 0:
            raise ValueError("demand priority must be positive")
    for lot in problem.inventory_lots:
        if lot.quantity < 0 or lot.unit_cost < 0:
            raise ValueError("lot quantities and costs cannot be negative")
    for offer in problem.supplier_offers:
        if offer.arrival_bucket_id not in bucket_ids:
            raise ValueError(f"offer {offer.offer_id} references an unknown bucket")
        if (
            min(
                offer.capacity,
                offer.unit_cost,
                offer.minimum_order,
                offer.risk,
                offer.expected_waste_rate,
            )
            < 0
        ):
            raise ValueError("offer values cannot be negative")
        if offer.pack_size <= 0:
            raise ValueError("offer pack size must be positive")
        if offer.minimum_order > offer.capacity:
            raise ValueError("minimum order cannot exceed offer capacity")
    for capacity in problem.warehouse_capacities:
        if capacity.bucket_id not in bucket_ids:
            raise ValueError("warehouse capacity references an unknown bucket")
        if capacity.maximum_base_units < 0:
            raise ValueError("warehouse capacity cannot be negative")
    for lane in problem.transport_lanes:
        if lane.source_warehouse_id == lane.destination_warehouse_id:
            raise ValueError("transport lanes must connect different warehouses")
        if lane.capacity < 0 or lane.transit_buckets < 0 or lane.unit_cost < 0:
            raise ValueError("transport lane values cannot be negative")
    for sku_id, volume in problem.sku_volume.items():
        if not sku_id or volume <= 0:
            raise ValueError("SKU volume factors must have a name and be positive")


def _unique(label: str, values: list[str]) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")
