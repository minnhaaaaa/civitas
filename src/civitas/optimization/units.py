"""Exact conversion of business quantities into integer solver base units."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    unit: str
    base_unit: str
    base_units_per_unit: Decimal

    def __post_init__(self) -> None:
        if self.base_units_per_unit <= 0:
            raise ValueError("base_units_per_unit must be positive")


class UnitConverter:
    """Converts exact decimals and refuses lossy unit conversions."""

    def __init__(self, definitions: tuple[UnitDefinition, ...]) -> None:
        self._definitions = {definition.unit: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("unit definitions must be unique")

    def to_base_units(self, value: Decimal, unit: str) -> tuple[int, str]:
        if value < 0:
            raise ValueError("quantity cannot be negative")
        try:
            definition = self._definitions[unit]
        except KeyError as error:
            raise ValueError(f"unknown unit: {unit}") from error
        converted = value * definition.base_units_per_unit
        integral = converted.to_integral_value()
        if converted != integral:
            raise ValueError(
                f"{value} {unit} is not exactly representable in {definition.base_unit}"
            )
        return int(integral), definition.base_unit

    def from_base_units(self, value: int, unit: str) -> Decimal:
        if value < 0:
            raise ValueError("quantity cannot be negative")
        try:
            definition = self._definitions[unit]
        except KeyError as error:
            raise ValueError(f"unknown unit: {unit}") from error
        return Decimal(value) / definition.base_units_per_unit
