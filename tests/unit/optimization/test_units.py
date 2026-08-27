from decimal import Decimal

import pytest

from civitas.optimization import UnitConverter, UnitDefinition


def test_exact_base_unit_conversion() -> None:
    converter = UnitConverter(
        (
            UnitDefinition(unit="kg", base_unit="g", base_units_per_unit=Decimal("1000")),
            UnitDefinition(unit="g", base_unit="g", base_units_per_unit=Decimal("1")),
        )
    )

    assert converter.to_base_units(Decimal("1.250"), "kg") == (1250, "g")
    assert converter.from_base_units(1250, "kg") == Decimal("1.25")


def test_lossy_conversion_is_rejected() -> None:
    converter = UnitConverter(
        (UnitDefinition(unit="case", base_unit="each", base_units_per_unit=Decimal("2.5")),)
    )

    with pytest.raises(ValueError, match="not exactly representable"):
        converter.to_base_units(Decimal("0.5"), "case")
