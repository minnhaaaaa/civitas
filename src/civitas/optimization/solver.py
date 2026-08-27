"""Deterministic feasibility-first CP-SAT optimization."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from ortools.sat.python import cp_model

from civitas.contracts.enums import FeasibilityStatus
from civitas.optimization.models import (
    AllocationDecision,
    Alternative,
    Demand,
    LotStatus,
    OptimizationProblem,
    ProcurementDecision,
    SolveResult,
    TransportLane,
)
from civitas.optimization.validation import validate_problem
from civitas.optimization.verifier import ConstraintVerifier

SOLVER_VERSION = "civitas-cp-sat-v1"


@dataclass(frozen=True, slots=True)
class _Source:
    source_id: str
    kind: str
    sku_id: str
    warehouse_id: str
    quantity: int
    available_index: int
    expires_at_index: int
    expires_sort_key: int
    unit_cost: int
    risk: int
    expected_waste_rate: int
    supplier_id: str | None


@dataclass(slots=True)
class _BuiltModel:
    model: cp_model.CpModel
    shortage: dict[str, cp_model.IntVar]
    order: dict[str, cp_model.IntVar]
    ordered: dict[str, cp_model.IntVar]
    allocation: dict[tuple[str, str, str | None], cp_model.IntVar]
    weighted_shortage: cp_model.LinearExpr
    metric_exprs: dict[str, cp_model.LinearExpr]
    sources: dict[str, _Source]


class OptimizationEngine:
    """Builds solver-owned alternatives and validates them independently."""

    def solve(self, problem: OptimizationProblem) -> SolveResult:
        validate_problem(problem)
        stage_one = self._build(problem)
        stage_one.model.minimize(stage_one.weighted_shortage)
        first_solver = self._solver()
        status = first_solver.solve(stage_one.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return SolveResult(
                status=FeasibilityStatus.INFEASIBLE,
                alternatives=(),
                optimal_weighted_shortage=None,
                diagnostics={"solver_status": first_solver.status_name(status)},
            )

        optimum = int(first_solver.value(stage_one.weighted_shortage))
        candidates: list[Alternative] = []
        seen: set[tuple[tuple[str, int], ...]] = set()
        profiles = self._objective_profiles()
        for profile_name, weights in profiles:
            if len(candidates) >= problem.maximum_alternatives:
                break
            built = self._build(problem)
            built.model.add(built.weighted_shortage <= optimum + problem.shortage_tolerance)
            objective = sum(built.metric_exprs[name] * weight for name, weight in weights.items())
            built.model.minimize(objective)
            solver = self._solver()
            candidate_status = solver.solve(built.model)
            if candidate_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                continue
            signature = (
                tuple(
                    sorted((offer_id, solver.value(var)) for offer_id, var in built.order.items())
                )
                + tuple(
                    sorted(
                        (f"shortage:{key}", solver.value(var))
                        for key, var in built.shortage.items()
                    )
                )
                + tuple(
                    sorted(
                        (f"allocation:{source}:{demand}:{lane}", solver.value(var))
                        for (source, demand, lane), var in built.allocation.items()
                    )
                )
            )
            if signature in seen:
                continue
            seen.add(signature)
            candidates.append(
                self._extract(problem, built, solver, profile_name, len(candidates) + 1)
            )

        if not candidates:
            candidates.append(self._extract(problem, stage_one, first_solver, "fulfillment", 1))

        candidates = self._non_dominated(candidates)
        status_value = (
            FeasibilityStatus.FULLY_FEASIBLE
            if candidates[0].shortage == 0
            else FeasibilityStatus.PARTIALLY_FULFILLED
        )
        candidates = [
            Alternative(
                alternative_id=item.alternative_id,
                feasibility=status_value,
                weighted_shortage=item.weighted_shortage,
                shortage=item.shortage,
                procurements=item.procurements,
                allocations=item.allocations,
                metrics=item.metrics,
            )
            for item in candidates
        ]
        verifier = ConstraintVerifier()
        for candidate in candidates:
            verification = verifier.verify(problem, candidate)
            if not verification.valid:
                raise RuntimeError(
                    "solver produced an invalid alternative: " + "; ".join(verification.violations)
                )
        return SolveResult(
            status=status_value,
            alternatives=tuple(candidates),
            optimal_weighted_shortage=optimum,
            diagnostics={"solver_version": SOLVER_VERSION, "candidate_count": len(candidates)},
        )

    def _build(self, problem: OptimizationProblem) -> _BuiltModel:
        model = cp_model.CpModel()
        bucket_index = {bucket.bucket_id: index for index, bucket in enumerate(problem.buckets)}
        sources = self._sources(problem, bucket_index)
        demands = {demand.demand_id: demand for demand in problem.demands}

        shortage = {
            demand.demand_id: model.new_int_var(0, demand.quantity, f"shortage_{demand.demand_id}")
            for demand in problem.demands
        }
        order: dict[str, cp_model.IntVar] = {}
        ordered: dict[str, cp_model.IntVar] = {}
        for offer in problem.supplier_offers:
            packs = offer.capacity // offer.pack_size
            pack_var = model.new_int_var(0, packs, f"packs_{offer.offer_id}")
            order[offer.offer_id] = model.new_int_var(0, offer.capacity, f"order_{offer.offer_id}")
            model.add(order[offer.offer_id] == pack_var * offer.pack_size)
            ordered[offer.offer_id] = model.new_bool_var(f"ordered_{offer.offer_id}")
            model.add(order[offer.offer_id] == 0).only_enforce_if(ordered[offer.offer_id].Not())
            model.add(order[offer.offer_id] >= max(offer.minimum_order, 1)).only_enforce_if(
                ordered[offer.offer_id]
            )

        allocation: dict[tuple[str, str, str | None], cp_model.IntVar] = {}
        lane_by_id = {lane.lane_id: lane for lane in problem.transport_lanes}
        for source, demand in product(sources.values(), problem.demands):
            for lane_id in self._eligible_routes(source, demand, problem, bucket_index):
                key = (source.source_id, demand.demand_id, lane_id)
                allocation[key] = model.new_int_var(
                    0,
                    min(source.quantity, demand.quantity),
                    f"alloc_{source.source_id}_{demand.demand_id}_{lane_id or 'local'}",
                )

        for demand in problem.demands:
            fulfilled = sum(
                var
                for (unused_source, demand_id, unused_lane), var in allocation.items()
                if demand_id == demand.demand_id
            )
            model.add(fulfilled + shortage[demand.demand_id] == demand.quantity)
            model.add(fulfilled >= demand.minimum_service)

        for source in sources.values():
            used = sum(
                var
                for (source_id, unused_demand, unused_lane), var in allocation.items()
                if source_id == source.source_id
            )
            if source.kind == "offer":
                model.add(used <= order[source.source_id])
            else:
                model.add(used <= source.quantity)

        for lane in problem.transport_lanes:
            model.add(
                sum(
                    var
                    for (unused_source, unused_demand, lane_id), var in allocation.items()
                    if lane_id == lane.lane_id
                )
                <= lane.capacity
            )

        self._add_fefo(model, problem, sources, demands, allocation, bucket_index)
        self._add_capacities(model, problem, sources, demands, allocation, order, bucket_index)

        cost = cp_model.LinearExpr.sum(
            [order[item.offer_id] * item.unit_cost for item in problem.supplier_offers]
            + [
                var * lane_by_id[lane_id].unit_cost
                for (unused_source, unused_demand, lane_id), var in allocation.items()
                if lane_id is not None
            ]
        )
        if problem.budget is not None:
            model.add(cost <= problem.budget)

        waste_terms: list[cp_model.LinearExpr] = []
        risk_terms: list[cp_model.LinearExpr] = []
        holding_terms: list[cp_model.LinearExpr] = []
        supplier_totals: dict[str, list[cp_model.LinearExpr]] = defaultdict(list)
        for offer in problem.supplier_offers:
            used = sum(
                var
                for (source_id, unused_demand, unused_lane), var in allocation.items()
                if source_id == offer.offer_id
            )
            waste_terms.append((order[offer.offer_id] - used) * max(1, offer.expected_waste_rate))
            risk_terms.append(order[offer.offer_id] * offer.risk)
            supplier_totals[offer.supplier_id].append(order[offer.offer_id])
        for (source_id, demand_id, _unused_lane), var in allocation.items():
            source = sources[source_id]
            delay = max(0, bucket_index[demands[demand_id].bucket_id] - source.available_index)
            holding_terms.append(var * delay)

        total_capacity = sum(item.capacity for item in problem.supplier_offers)
        concentration = model.new_int_var(0, total_capacity, "supplier_concentration")
        for expressions in supplier_totals.values():
            model.add(concentration >= sum(expressions))
        weighted_shortage = cp_model.LinearExpr.sum(
            [
                shortage[demand.demand_id]
                * demand.priority
                * problem.buckets[bucket_index[demand.bucket_id]].urgency
                for demand in problem.demands
            ]
        )
        metric_exprs: dict[str, cp_model.LinearExpr] = {
            "cost": cost,
            "waste": cp_model.LinearExpr.sum(waste_terms),
            "risk": cp_model.LinearExpr.sum(risk_terms),
            "redistribution": cp_model.LinearExpr.sum(
                [
                    var
                    for (unused_source, unused_demand, lane_id), var in allocation.items()
                    if lane_id is not None
                ]
            ),
            "holding": cp_model.LinearExpr.sum(holding_terms),
            "concentration": concentration,
        }
        return _BuiltModel(
            model=model,
            shortage=shortage,
            order=order,
            ordered=ordered,
            allocation=allocation,
            weighted_shortage=weighted_shortage,
            metric_exprs=metric_exprs,
            sources=sources,
        )

    def _sources(
        self, problem: OptimizationProblem, bucket_index: dict[str, int]
    ) -> dict[str, _Source]:
        result: dict[str, _Source] = {}
        for lot in problem.inventory_lots:
            if lot.status is not LotStatus.AVAILABLE or lot.quantity == 0:
                continue
            available_index = 0
            if lot.available_from is not None:
                available_index = next(
                    (
                        index
                        for index, item in enumerate(problem.buckets)
                        if item.end > lot.available_from
                    ),
                    len(problem.buckets),
                )
            expiry_index = next(
                (index for index, item in enumerate(problem.buckets) if item.end > lot.expires_at),
                len(problem.buckets),
            )
            result[lot.lot_id] = _Source(
                source_id=lot.lot_id,
                kind="lot",
                sku_id=lot.sku_id,
                warehouse_id=lot.warehouse_id,
                quantity=lot.quantity,
                available_index=available_index,
                expires_at_index=expiry_index,
                expires_sort_key=int(lot.expires_at.timestamp()),
                unit_cost=lot.unit_cost,
                risk=0,
                expected_waste_rate=0,
                supplier_id=None,
            )
        for offer in problem.supplier_offers:
            expiry_index = len(problem.buckets)
            expiry_sort_key = 2**62
            if offer.expires_at is not None:
                expiry_index = next(
                    (
                        index
                        for index, item in enumerate(problem.buckets)
                        if item.end > offer.expires_at
                    ),
                    len(problem.buckets),
                )
                expiry_sort_key = int(offer.expires_at.timestamp())
            result[offer.offer_id] = _Source(
                source_id=offer.offer_id,
                kind="offer",
                sku_id=offer.sku_id,
                warehouse_id=offer.destination_warehouse_id,
                quantity=offer.capacity,
                available_index=bucket_index[offer.arrival_bucket_id],
                expires_at_index=expiry_index,
                expires_sort_key=expiry_sort_key,
                unit_cost=offer.unit_cost,
                risk=offer.risk,
                expected_waste_rate=offer.expected_waste_rate,
                supplier_id=offer.supplier_id,
            )
        return result

    def _eligible_routes(
        self,
        source: _Source,
        demand: Demand,
        problem: OptimizationProblem,
        bucket_index: dict[str, int],
    ) -> tuple[str | None, ...]:
        demand_index = bucket_index[demand.bucket_id]
        if source.sku_id != demand.sku_id or demand_index >= source.expires_at_index:
            return ()
        if source.warehouse_id == demand.warehouse_id:
            return (None,) if source.available_index <= demand_index else ()
        if source.kind != "lot":
            return ()
        return tuple(
            lane.lane_id
            for lane in problem.transport_lanes
            if lane.source_warehouse_id == source.warehouse_id
            and lane.destination_warehouse_id == demand.warehouse_id
            and (not lane.sku_ids or source.sku_id in lane.sku_ids)
            and source.available_index + lane.transit_buckets <= demand_index
        )

    def _add_fefo(
        self,
        model: cp_model.CpModel,
        problem: OptimizationProblem,
        sources: dict[str, _Source],
        demands: dict[str, Demand],
        allocation: dict[tuple[str, str, str | None], cp_model.IntVar],
        bucket_index: dict[str, int],
    ) -> None:
        lots = sorted(
            (source for source in sources.values() if source.kind == "lot"),
            key=lambda item: (item.expires_sort_key, item.source_id),
        )
        for older, newer in product(lots, lots):
            if (
                older.sku_id != newer.sku_id
                or older.warehouse_id != newer.warehouse_id
                or older.expires_sort_key >= newer.expires_sort_key
            ):
                continue
            for (source_id, demand_id, lane_id), newer_var in allocation.items():
                if source_id != newer.source_id:
                    continue
                demand_index = bucket_index[demands[demand_id].bucket_id]
                use_index = self._use_index(demand_index, lane_id, problem.transport_lanes)
                if not (use_index < older.expires_at_index and use_index >= older.available_index):
                    continue
                newer_used = model.new_bool_var(
                    f"fefo_{older.source_id}_before_{newer.source_id}_{demand_id}"
                )
                model.add(newer_var == 0).only_enforce_if(newer_used.Not())
                model.add(newer_var >= 1).only_enforce_if(newer_used)
                older_used_by_then = sum(
                    var
                    for (
                        candidate_source,
                        candidate_demand,
                        candidate_lane,
                    ), var in allocation.items()
                    if candidate_source == older.source_id
                    and self._use_index(
                        bucket_index[demands[candidate_demand].bucket_id],
                        candidate_lane,
                        problem.transport_lanes,
                    )
                    <= use_index
                )
                model.add(older_used_by_then >= older.quantity).only_enforce_if(newer_used)

    def _add_capacities(
        self,
        model: cp_model.CpModel,
        problem: OptimizationProblem,
        sources: dict[str, _Source],
        demands: dict[str, Demand],
        allocation: dict[tuple[str, str, str | None], cp_model.IntVar],
        order: dict[str, cp_model.IntVar],
        bucket_index: dict[str, int],
    ) -> None:
        for limit in problem.warehouse_capacities:
            index = bucket_index[limit.bucket_id]
            remaining: list[cp_model.LinearExpr] = []
            for source in sources.values():
                if (
                    source.warehouse_id != limit.warehouse_id
                    or source.available_index > index
                    or source.expires_at_index <= index
                ):
                    continue
                supplied = (
                    order[source.source_id]
                    if source.kind == "offer"
                    else cp_model.LinearExpr.constant(source.quantity)
                )
                consumed = cp_model.LinearExpr.sum(
                    [
                        var
                        for (source_id, demand_id, lane_id), var in allocation.items()
                        if source_id == source.source_id
                        and self._use_index(
                            bucket_index[demands[demand_id].bucket_id],
                            lane_id,
                            problem.transport_lanes,
                        )
                        < index
                    ]
                )
                remaining.append((supplied - consumed) * problem.sku_volume.get(source.sku_id, 1))
            remaining.extend(
                var * problem.sku_volume.get(demands[demand_id].sku_id, 1)
                for (unused_source, demand_id, lane_id), var in allocation.items()
                if lane_id is not None
                and demands[demand_id].warehouse_id == limit.warehouse_id
                and bucket_index[demands[demand_id].bucket_id] == index
            )
            model.add(cp_model.LinearExpr.sum(remaining) <= limit.maximum_base_units)

    def _use_index(
        self,
        demand_index: int,
        lane_id: str | None,
        lanes: tuple[TransportLane, ...],
    ) -> int:
        if lane_id is None:
            return demand_index
        lane = next(item for item in lanes if item.lane_id == lane_id)
        return demand_index - lane.transit_buckets

    def _extract(
        self,
        problem: OptimizationProblem,
        built: _BuiltModel,
        solver: cp_model.CpSolver,
        profile_name: str,
        sequence: int,
    ) -> Alternative:
        procurement = tuple(
            ProcurementDecision(offer_id=offer_id, quantity=solver.value(var))
            for offer_id, var in sorted(built.order.items())
            if solver.value(var) > 0
        )
        allocations = tuple(
            AllocationDecision(
                demand_id=demand_id,
                source_id=source_id,
                source_kind=built.sources[source_id].kind,
                quantity=solver.value(var),
                lane_id=lane_id,
            )
            for (source_id, demand_id, lane_id), var in sorted(
                built.allocation.items(), key=lambda item: tuple(str(part) for part in item[0])
            )
            if solver.value(var) > 0
        )
        metrics = {
            name: Decimal(solver.value(expression))
            for name, expression in built.metric_exprs.items()
        }
        metrics["profile"] = Decimal(sequence)
        shortage = sum(solver.value(var) for var in built.shortage.values())
        return Alternative(
            alternative_id=f"{problem.planning_run_id}-{profile_name}-{sequence:02d}",
            feasibility=(
                FeasibilityStatus.FULLY_FEASIBLE
                if shortage == 0
                else FeasibilityStatus.PARTIALLY_FULFILLED
            ),
            weighted_shortage=solver.value(built.weighted_shortage),
            shortage=shortage,
            procurements=procurement,
            allocations=allocations,
            metrics=metrics,
        )

    def _objective_profiles(self) -> tuple[tuple[str, dict[str, int]], ...]:
        names = ("cost", "waste", "risk", "redistribution", "holding", "concentration")
        profiles: list[tuple[str, dict[str, int]]] = []
        for primary in names:
            profiles.append(
                (primary, {name: (1_000_000 if name == primary else 1) for name in names})
            )
        profiles.append(("balanced", {name: 1 for name in names}))
        return tuple(profiles)

    def _non_dominated(self, alternatives: list[Alternative]) -> list[Alternative]:
        metric_names = ("cost", "waste", "risk", "redistribution", "holding", "concentration")
        kept: list[Alternative] = []
        for candidate in alternatives:
            dominated = any(
                all(other.metrics[name] <= candidate.metrics[name] for name in metric_names)
                and any(other.metrics[name] < candidate.metrics[name] for name in metric_names)
                for other in alternatives
                if other is not candidate
            )
            if not dominated:
                kept.append(candidate)
        return sorted(kept, key=lambda item: item.alternative_id)

    def _solver(self) -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.log_search_progress = False
        return solver
