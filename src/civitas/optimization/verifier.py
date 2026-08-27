"""Independent deterministic verification of solver alternatives.

The verifier deliberately does not import OR-Tools or reuse solver constraints.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from civitas.contracts.enums import FeasibilityStatus
from civitas.optimization.models import Alternative, InventoryLot, LotStatus, OptimizationProblem
from civitas.optimization.validation import validate_problem


@dataclass(frozen=True, slots=True)
class VerificationResult:
    valid: bool
    violations: tuple[str, ...]


class ConstraintVerifier:
    def verify(self, problem: OptimizationProblem, alternative: Alternative) -> VerificationResult:
        validate_problem(problem)
        violations: list[str] = []
        buckets = {item.bucket_id: item for item in problem.buckets}
        bucket_index = {item.bucket_id: index for index, item in enumerate(problem.buckets)}
        demands = {item.demand_id: item for item in problem.demands}
        lots = {item.lot_id: item for item in problem.inventory_lots}
        offers = {item.offer_id: item for item in problem.supplier_offers}
        lanes = {item.lane_id: item for item in problem.transport_lanes}
        orders = {item.offer_id: item.quantity for item in alternative.procurements}
        if len(orders) != len(alternative.procurements):
            violations.append("procurement contains duplicate offer lines")

        for procurement in alternative.procurements:
            offer = offers.get(procurement.offer_id)
            if offer is None:
                violations.append(f"unknown offer {procurement.offer_id}")
                continue
            if procurement.quantity <= 0 or procurement.quantity > offer.capacity:
                violations.append(f"offer {offer.offer_id} exceeds its valid quantity range")
            if procurement.quantity % offer.pack_size:
                violations.append(f"offer {offer.offer_id} violates pack size")
            if procurement.quantity < offer.minimum_order:
                violations.append(f"offer {offer.offer_id} violates minimum order")

        allocated_by_source: dict[str, int] = defaultdict(int)
        allocated_by_demand: dict[str, int] = defaultdict(int)
        allocated_by_lane: dict[str, int] = defaultdict(int)
        allocation_by_lot_and_bucket: dict[tuple[str, int], int] = defaultdict(int)
        for allocation in alternative.allocations:
            if allocation.quantity <= 0:
                violations.append("allocations must be positive")
                continue
            demand = demands.get(allocation.demand_id)
            if demand is None:
                violations.append(f"unknown demand {allocation.demand_id}")
                continue
            demand_index = bucket_index[demand.bucket_id]
            allocated_by_demand[demand.demand_id] += allocation.quantity
            allocated_by_source[allocation.source_id] += allocation.quantity
            if allocation.source_kind == "lot":
                lot = lots.get(allocation.source_id)
                if lot is None:
                    violations.append(f"unknown lot {allocation.source_id}")
                    continue
                if lot.status is not LotStatus.AVAILABLE:
                    violations.append(f"lot {lot.lot_id} is not available")
                if lot.sku_id != demand.sku_id:
                    violations.append(f"lot {lot.lot_id} has the wrong SKU")
                if lot.expires_at < buckets[demand.bucket_id].end:
                    violations.append(f"lot {lot.lot_id} expires before demand is served")
                if (
                    lot.available_from is not None
                    and lot.available_from >= buckets[demand.bucket_id].end
                ):
                    violations.append(f"lot {lot.lot_id} is not yet available")
                use_index = demand_index
                if lot.warehouse_id != demand.warehouse_id:
                    lane = lanes.get(allocation.lane_id or "")
                    if lane is None:
                        violations.append(f"allocation from {lot.lot_id} requires a transport lane")
                    else:
                        allocated_by_lane[lane.lane_id] += allocation.quantity
                        if (
                            lane.source_warehouse_id != lot.warehouse_id
                            or lane.destination_warehouse_id != demand.warehouse_id
                            or (lane.sku_ids and lot.sku_id not in lane.sku_ids)
                        ):
                            violations.append(f"lane {lane.lane_id} is not eligible")
                        available_index = self._available_index(problem, lot.available_from)
                        if available_index + lane.transit_buckets > demand_index:
                            violations.append(f"lane {lane.lane_id} arrives too late")
                        use_index -= lane.transit_buckets
                elif allocation.lane_id is not None:
                    violations.append("local allocation cannot specify a lane")
                allocation_by_lot_and_bucket[(lot.lot_id, use_index)] += allocation.quantity
            elif allocation.source_kind == "offer":
                offer = offers.get(allocation.source_id)
                if offer is None:
                    violations.append(f"unknown offer source {allocation.source_id}")
                    continue
                if allocation.lane_id is not None:
                    violations.append("supplier offer allocation cannot specify a lane")
                if (
                    offer.sku_id != demand.sku_id
                    or offer.destination_warehouse_id != demand.warehouse_id
                ):
                    violations.append(f"offer {offer.offer_id} is not eligible for demand")
                if bucket_index[offer.arrival_bucket_id] > demand_index:
                    violations.append(f"offer {offer.offer_id} arrives too late")
                if (
                    offer.expires_at is not None
                    and offer.expires_at < buckets[demand.bucket_id].end
                ):
                    violations.append(f"offer {offer.offer_id} expires before demand is served")
            else:
                violations.append(f"unknown source kind {allocation.source_kind}")

        for demand in problem.demands:
            fulfilled = allocated_by_demand[demand.demand_id]
            if fulfilled > demand.quantity:
                violations.append(f"demand {demand.demand_id} is over-allocated")
            if fulfilled < demand.minimum_service:
                violations.append(f"demand {demand.demand_id} misses minimum service")
        computed_shortage = sum(
            demand.quantity - allocated_by_demand[demand.demand_id] for demand in problem.demands
        )
        if computed_shortage != alternative.shortage:
            violations.append("reported shortage does not match allocations")
        expected_feasibility = (
            FeasibilityStatus.FULLY_FEASIBLE
            if computed_shortage == 0
            else FeasibilityStatus.PARTIALLY_FULFILLED
        )
        if alternative.feasibility is not expected_feasibility:
            violations.append("reported feasibility does not match allocations")
        computed_weighted = sum(
            (demand.quantity - allocated_by_demand[demand.demand_id])
            * demand.priority
            * buckets[demand.bucket_id].urgency
            for demand in problem.demands
        )
        if computed_weighted != alternative.weighted_shortage:
            violations.append("reported weighted shortage does not match allocations")

        for lot in problem.inventory_lots:
            if allocated_by_source[lot.lot_id] > lot.quantity:
                violations.append(f"lot {lot.lot_id} is over-allocated")
        for offer in problem.supplier_offers:
            if allocated_by_source[offer.offer_id] > orders.get(offer.offer_id, 0):
                violations.append(f"offer {offer.offer_id} allocation exceeds its order")
        for lane in problem.transport_lanes:
            if allocated_by_lane[lane.lane_id] > lane.capacity:
                violations.append(f"lane {lane.lane_id} exceeds capacity")

        self._verify_fefo(problem, allocation_by_lot_and_bucket, violations)
        self._verify_warehouse_capacity(
            problem, orders, allocated_by_source, alternative, bucket_index, violations
        )
        total_cost = sum(
            orders.get(offer.offer_id, 0) * offer.unit_cost for offer in problem.supplier_offers
        ) + sum(
            allocation.quantity * lanes[allocation.lane_id].unit_cost
            for allocation in alternative.allocations
            if allocation.lane_id in lanes
        )
        if problem.budget is not None and total_cost > problem.budget:
            violations.append("budget exceeded")
        return VerificationResult(valid=not violations, violations=tuple(sorted(set(violations))))

    def _verify_fefo(
        self,
        problem: OptimizationProblem,
        allocation_by_lot_and_bucket: dict[tuple[str, int], int],
        violations: list[str],
    ) -> None:
        groups: dict[tuple[str, str], list[InventoryLot]] = defaultdict(list)
        for lot in problem.inventory_lots:
            if lot.status is LotStatus.AVAILABLE:
                groups[(lot.sku_id, lot.warehouse_id)].append(lot)
        for group_lots in groups.values():
            ordered_lots = sorted(group_lots, key=lambda item: (item.expires_at, item.lot_id))
            cumulative_used: dict[str, int] = defaultdict(int)
            for index, bucket in enumerate(problem.buckets):
                for lot in ordered_lots:
                    cumulative_used[lot.lot_id] += allocation_by_lot_and_bucket[(lot.lot_id, index)]
                for newer_index, newer in enumerate(ordered_lots):
                    if allocation_by_lot_and_bucket[(newer.lot_id, index)] == 0:
                        continue
                    for older in ordered_lots[:newer_index]:
                        available = (
                            older.available_from is None or older.available_from < bucket.end
                        )
                        unexpired = older.expires_at >= bucket.end
                        if (
                            available
                            and unexpired
                            and cumulative_used[older.lot_id] < older.quantity
                        ):
                            violations.append(
                                f"FEFO violation: {newer.lot_id} used before {older.lot_id}"
                            )

    def _verify_warehouse_capacity(
        self,
        problem: OptimizationProblem,
        orders: dict[str, int],
        allocated_by_source: dict[str, int],
        alternative: Alternative,
        bucket_index: dict[str, int],
        violations: list[str],
    ) -> None:
        del allocated_by_source
        demand_by_id = {item.demand_id: item for item in problem.demands}
        lanes = {item.lane_id: item for item in problem.transport_lanes}
        consumed_before: dict[tuple[str, int], int] = defaultdict(int)
        incoming_at: dict[tuple[str, int], int] = defaultdict(int)
        for allocation in alternative.allocations:
            demand = demand_by_id.get(allocation.demand_id)
            if demand is None:
                continue
            demand_index = bucket_index[demand.bucket_id]
            use_index = demand_index
            if allocation.lane_id is not None and allocation.lane_id in lanes:
                use_index -= lanes[allocation.lane_id].transit_buckets
                incoming_at[(demand.warehouse_id, demand_index)] += (
                    allocation.quantity * problem.sku_volume.get(demand.sku_id, 1)
                )
            for index in range(use_index + 1, len(problem.buckets)):
                consumed_before[(allocation.source_id, index)] += allocation.quantity
        for limit in problem.warehouse_capacities:
            index = bucket_index[limit.bucket_id]
            occupied = incoming_at[(limit.warehouse_id, index)]
            for lot in problem.inventory_lots:
                available_index = self._available_index(problem, lot.available_from)
                if (
                    lot.status is LotStatus.AVAILABLE
                    and lot.warehouse_id == limit.warehouse_id
                    and available_index <= index
                    and lot.expires_at >= problem.buckets[index].end
                ):
                    remaining = lot.quantity - consumed_before[(lot.lot_id, index)]
                    occupied += remaining * problem.sku_volume.get(lot.sku_id, 1)
            for offer in problem.supplier_offers:
                if (
                    offer.destination_warehouse_id == limit.warehouse_id
                    and bucket_index[offer.arrival_bucket_id] <= index
                    and (offer.expires_at is None or offer.expires_at >= problem.buckets[index].end)
                ):
                    remaining = (
                        orders.get(offer.offer_id, 0) - consumed_before[(offer.offer_id, index)]
                    )
                    occupied += remaining * problem.sku_volume.get(offer.sku_id, 1)
            if occupied > limit.maximum_base_units:
                violations.append(
                    f"warehouse {limit.warehouse_id} exceeds capacity in {limit.bucket_id}"
                )

    def _available_index(
        self, problem: OptimizationProblem, available_from: datetime | None
    ) -> int:
        if available_from is None:
            return 0
        return next(
            (index for index, bucket in enumerate(problem.buckets) if bucket.end > available_from),
            len(problem.buckets),
        )
