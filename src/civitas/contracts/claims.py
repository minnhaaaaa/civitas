"""Machine-verifiable factual claim contracts."""

from datetime import datetime

from pydantic import Field, model_validator

from civitas.contracts.common import Contract, JsonValue


class ValidityInterval(Contract):
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def ordered(self) -> "ValidityInterval":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class ClaimScope(Contract):
    organization_id: str = Field(min_length=1)
    sku_id: str | None = None
    warehouse_id: str | None = None
    supplier_id: str | None = None


class TypedClaim(Contract):
    claim_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: JsonValue
    unit: str | None = None
    valid_at: datetime | None = None
    valid_during: ValidityInterval | None = None
    scope: ClaimScope
    human_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def exactly_one_validity_form(self) -> "TypedClaim":
        if (self.valid_at is None) == (self.valid_during is None):
            raise ValueError("exactly one of valid_at or valid_during is required")
        return self
