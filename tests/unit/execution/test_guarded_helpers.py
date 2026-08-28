from datetime import UTC, datetime
from decimal import Decimal

from civitas.contracts.common import Quantity
from civitas.contracts.optimization import DistributionLine
from civitas.execution.guarded import _distribution_reservation_key


def _line(destination: str) -> DistributionLine:
    return DistributionLine(
        sku_id="sku-1",
        source_warehouse_id="warehouse-source",
        destination_warehouse_id=destination,
        departure_bucket_start=datetime(2026, 8, 28, tzinfo=UTC),
        arrival_bucket_start=datetime(2026, 8, 29, tzinfo=UTC),
        quantity=Quantity(value=Decimal("1"), unit="kg"),
        source_lot_ids=("lot-1",),
    )


def test_distribution_reservation_keys_are_unique_per_plan_line() -> None:
    first = _distribution_reservation_key("execution-key", 0, _line("warehouse-a"))
    second = _distribution_reservation_key("execution-key", 1, _line("warehouse-b"))

    assert first != second
    assert first == _distribution_reservation_key("execution-key", 0, _line("warehouse-a"))
