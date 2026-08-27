"""Versioned deterministic role scorecards and minimax-regret selection."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from civitas.optimization.models import Alternative

SCORECARD_VERSION = "role-scorecards-v1"


class Role(StrEnum):
    DEMAND = "demand"
    COST = "cost"
    FRESHNESS = "freshness"
    LOGISTICS = "logistics"
    SUPPLIER = "supplier"
    WASTE = "waste"


@dataclass(frozen=True, slots=True)
class SelectionResult:
    selected_alternative_id: str
    role_scores: dict[str, dict[Role, Decimal]]
    role_regrets: dict[str, dict[Role, Decimal]]
    maximum_regret: dict[str, Decimal]
    total_regret: dict[str, Decimal]
    scorecard_version: str = SCORECARD_VERSION


def score_alternatives(alternatives: tuple[Alternative, ...]) -> dict[str, dict[Role, Decimal]]:
    if not alternatives:
        raise ValueError("at least one alternative is required")
    raw: dict[str, dict[Role, Decimal]] = {}
    for item in alternatives:
        raw[item.alternative_id] = {
            Role.DEMAND: Decimal(item.weighted_shortage),
            Role.COST: item.metrics.get("cost", Decimal(0))
            + item.metrics.get("holding", Decimal(0)),
            Role.FRESHNESS: item.metrics.get("waste", Decimal(0)) * 2
            + item.metrics.get("holding", Decimal(0)),
            Role.LOGISTICS: item.metrics.get("redistribution", Decimal(0)) * 2
            + item.metrics.get("risk", Decimal(0)),
            Role.SUPPLIER: item.metrics.get("risk", Decimal(0)) * 2
            + item.metrics.get("concentration", Decimal(0)),
            Role.WASTE: item.metrics.get("waste", Decimal(0)),
        }
    result: dict[str, dict[Role, Decimal]] = {item.alternative_id: {} for item in alternatives}
    for role in Role:
        values = [raw[item.alternative_id][role] for item in alternatives]
        low, high = min(values), max(values)
        for item in alternatives:
            value = raw[item.alternative_id][role]
            result[item.alternative_id][role] = (
                Decimal(100) if high == low else (high - value) * Decimal(100) / (high - low)
            )
    return result


def select_minimax_regret(alternatives: tuple[Alternative, ...]) -> SelectionResult:
    scores = score_alternatives(alternatives)
    best = {role: max(plan[role] for plan in scores.values()) for role in Role}
    regrets = {
        alternative_id: {role: best[role] - plan_scores[role] for role in Role}
        for alternative_id, plan_scores in scores.items()
    }
    maximum = {key: max(value.values()) for key, value in regrets.items()}
    total = {key: sum(value.values(), Decimal(0)) for key, value in regrets.items()}
    by_id = {item.alternative_id: item for item in alternatives}
    selected = min(
        alternatives,
        key=lambda item: (
            maximum[item.alternative_id],
            total[item.alternative_id],
            item.weighted_shortage,
            item.metrics.get("waste", Decimal(0)),
            item.metrics.get("cost", Decimal(0)),
            item.alternative_id,
        ),
    )
    assert selected.alternative_id in by_id
    return SelectionResult(
        selected_alternative_id=selected.alternative_id,
        role_scores=scores,
        role_regrets=regrets,
        maximum_regret=maximum,
        total_regret=total,
    )
