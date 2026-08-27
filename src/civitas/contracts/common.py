"""Common immutable value contracts."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import JsonValue as PydanticJsonValue

type JsonValue = PydanticJsonValue


class Contract(BaseModel):
    """Base class for strict, immutable boundary data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Quantity(Contract):
    """Exact business quantity with an explicit unit of measure."""

    value: Decimal = Field(ge=0)
    unit: str = Field(min_length=1, max_length=32)


JsonObject = dict[str, JsonValue]
