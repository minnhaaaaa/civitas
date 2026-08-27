from decimal import Decimal

from civitas.contracts.enums import FeasibilityStatus
from civitas.optimization import Alternative, select_minimax_regret


def _alternative(identifier: str, cost: int, waste: int, risk: int) -> Alternative:
    return Alternative(
        alternative_id=identifier,
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        weighted_shortage=0,
        shortage=0,
        procurements=(),
        allocations=(),
        metrics={
            "cost": Decimal(cost),
            "waste": Decimal(waste),
            "risk": Decimal(risk),
            "redistribution": Decimal(0),
            "holding": Decimal(0),
            "concentration": Decimal(0),
        },
    )


def test_minimax_regret_prefers_balanced_plan() -> None:
    alternatives = (
        _alternative("cheap", 0, 10, 10),
        _alternative("balanced", 5, 5, 5),
        _alternative("fresh", 10, 0, 0),
    )

    selection = select_minimax_regret(alternatives)

    assert selection.selected_alternative_id == "balanced"


def test_tie_breaking_uses_stable_plan_id() -> None:
    alternatives = (_alternative("plan-b", 1, 1, 1), _alternative("plan-a", 1, 1, 1))

    assert select_minimax_regret(alternatives).selected_alternative_id == "plan-a"
