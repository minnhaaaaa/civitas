"""A deliberately small independent exhaustive oracle for golden unit cases."""

from dataclasses import dataclass
from itertools import product

from civitas.optimization.models import LotStatus, OptimizationProblem
from civitas.optimization.validation import validate_problem


@dataclass(frozen=True, slots=True)
class ExhaustiveOptimum:
    weighted_shortage: int
    landed_cost: int


def exhaustive_single_bucket_optimum(
    problem: OptimizationProblem, *, maximum_states: int = 1_000_000
) -> ExhaustiveOptimum:
    """Enumerate a small one-bucket case without sharing solver implementation.

    This oracle is intentionally narrow: it is useful for checking CP-SAT on
    generated small cases, while refusing to masquerade as a second optimizer
    for temporal, capacity, or transport scenarios.
    """

    validate_problem(problem)
    if len(problem.buckets) != 1:
        raise ValueError("the exhaustive oracle supports exactly one bucket")
    if problem.transport_lanes or problem.warehouse_capacities:
        raise ValueError("the exhaustive oracle does not support lanes or capacity limits")
    bucket = problem.buckets[0]
    order_domains: list[tuple[int, ...]] = []
    for offer in problem.supplier_offers:
        values = [0]
        values.extend(
            quantity
            for quantity in range(offer.pack_size, offer.capacity + 1, offer.pack_size)
            if quantity >= offer.minimum_order
        )
        order_domains.append(tuple(values))
    state_count = 1
    for domain in order_domains:
        state_count *= len(domain)
    for demand in problem.demands:
        state_count *= demand.quantity + 1
    if state_count > maximum_states:
        raise ValueError("exhaustive state limit exceeded")

    best: tuple[int, int] | None = None
    order_space = product(*order_domains) if order_domains else [()]
    for order_values in order_space:
        available: dict[tuple[str, str], int] = {}
        for lot in problem.inventory_lots:
            if (
                lot.status is LotStatus.AVAILABLE
                and lot.expires_at >= bucket.end
                and (lot.available_from is None or lot.available_from < bucket.end)
            ):
                key = (lot.sku_id, lot.warehouse_id)
                available[key] = available.get(key, 0) + lot.quantity
        cost = 0
        for offer, ordered in zip(problem.supplier_offers, order_values, strict=True):
            if offer.expires_at is not None and offer.expires_at < bucket.end:
                continue
            key = (offer.sku_id, offer.destination_warehouse_id)
            available[key] = available.get(key, 0) + ordered
            cost += ordered * offer.unit_cost
        if problem.budget is not None and cost > problem.budget:
            continue
        for fulfilled_values in product(*(range(item.quantity + 1) for item in problem.demands)):
            used: dict[tuple[str, str], int] = {}
            valid = True
            weighted_shortage = 0
            for demand, fulfilled in zip(problem.demands, fulfilled_values, strict=True):
                if fulfilled < demand.minimum_service:
                    valid = False
                    break
                key = (demand.sku_id, demand.warehouse_id)
                used[key] = used.get(key, 0) + fulfilled
                if used[key] > available.get(key, 0):
                    valid = False
                    break
                weighted_shortage += (
                    (demand.quantity - fulfilled) * demand.priority * bucket.urgency
                )
            if valid and (best is None or (weighted_shortage, cost) < best):
                best = (weighted_shortage, cost)
    if best is None:
        raise ValueError("problem has no feasible assignment")
    return ExhaustiveOptimum(weighted_shortage=best[0], landed_cost=best[1])
