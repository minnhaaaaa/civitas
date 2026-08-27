"""Typed claim normalization and comparison helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from civitas.contracts.claims import ClaimScope, TypedClaim, ValidityInterval
from civitas.contracts.common import JsonValue

type NormalizedScalar = Decimal | str | bool | None

_PREDICATE_ALIASES = {
    "leadtime": "lead_time",
    "lead_time_days": "lead_time",
    "unitprice": "unit_price",
    "price_per_unit": "unit_price",
    "suppliercapacity": "supplier_capacity",
}

_UNIT_ALIASES = {
    "days": "day",
    "d": "day",
    "hours": "hour",
    "hrs": "hour",
    "hr": "hour",
    "h": "hour",
    "kilograms": "kg",
    "kilogram": "kg",
    "kgs": "kg",
    "grams": "g",
    "gram": "g",
    "units": "unit",
    "each": "unit",
}

_UNIT_CONVERSIONS: dict[tuple[str, str], Decimal] = {
    ("hour", "day"): Decimal(1) / Decimal(24),
    ("g", "kg"): Decimal(1) / Decimal(1000),
}


def normalize_token(value: str) -> str:
    """Normalize identifiers used for typed equality without changing their identity."""

    return "_".join(value.strip().lower().replace("-", "_").split())


def normalize_predicate(predicate: str) -> str:
    normalized = normalize_token(predicate)
    return _PREDICATE_ALIASES.get(normalized, normalized)


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = normalize_token(unit)
    return _UNIT_ALIASES.get(normalized, normalized)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("claim datetimes must be timezone-aware")
    return value.astimezone(UTC)


def normalize_scalar(value: JsonValue, unit: str | None) -> tuple[NormalizedScalar, str | None]:
    normalized_unit = normalize_unit(unit)
    if isinstance(value, bool) or value is None:
        return value, normalized_unit
    if isinstance(value, (int, float, Decimal)):
        try:
            numeric = Decimal(str(value)).normalize()
        except InvalidOperation as error:  # pragma: no cover - defensive for exotic JSON values
            raise ValueError(f"invalid numeric claim value: {value!r}") from error
        target_unit = normalized_unit
        for (source, target), multiplier in _UNIT_CONVERSIONS.items():
            if normalized_unit == source:
                numeric = (numeric * multiplier).normalize()
                target_unit = target
                break
        return numeric, target_unit
    if isinstance(value, str):
        return " ".join(value.strip().casefold().split()), normalized_unit
    # Structured JSON values are compared through a stable, recursively normalized rendering.
    return _normalize_json(value), normalized_unit


def _normalize_json(value: JsonValue) -> str:
    if isinstance(value, dict):
        items = sorted((key, _normalize_json(item)) for key, item in value.items())
        return repr(items)
    if isinstance(value, list):
        return repr([_normalize_json(item) for item in value])
    return repr(value)


def normalized_claim_key(claim: TypedClaim) -> tuple[object, ...]:
    scope = claim.scope
    return (
        normalize_token(scope.organization_id),
        normalize_token(claim.subject),
        normalize_predicate(claim.predicate),
        _optional_token(scope.sku_id),
        _optional_token(scope.warehouse_id),
        _optional_token(scope.supplier_id),
    )


def normalized_claim_value(claim: TypedClaim) -> tuple[NormalizedScalar, str | None]:
    return normalize_scalar(claim.value, claim.unit)


def normalize_claim(claim: TypedClaim) -> TypedClaim:
    """Return a canonical claim while retaining its stable claim identifier."""

    value, unit = normalized_claim_value(claim)
    json_value: JsonValue
    if isinstance(value, Decimal):
        json_value = int(value) if value == value.to_integral_value() else float(value)
    else:
        json_value = value
    valid_at = normalize_datetime(claim.valid_at) if claim.valid_at is not None else None
    interval = claim.valid_during
    valid_during = (
        ValidityInterval(
            starts_at=normalize_datetime(interval.starts_at),
            ends_at=normalize_datetime(interval.ends_at),
        )
        if interval is not None
        else None
    )
    return TypedClaim(
        claim_id=claim.claim_id.strip(),
        subject=normalize_token(claim.subject),
        predicate=normalize_predicate(claim.predicate),
        value=json_value,
        unit=unit,
        valid_at=valid_at,
        valid_during=valid_during,
        scope=ClaimScope(
            organization_id=normalize_token(claim.scope.organization_id),
            sku_id=_optional_token(claim.scope.sku_id),
            warehouse_id=_optional_token(claim.scope.warehouse_id),
            supplier_id=_optional_token(claim.scope.supplier_id),
        ),
        human_summary=" ".join(claim.human_summary.split()),
    )


def validity_overlaps(left: TypedClaim, right: TypedClaim) -> bool:
    left_start, left_end = _bounds(left)
    right_start, right_end = _bounds(right)
    return left_start < right_end and right_start < left_end


def _bounds(claim: TypedClaim) -> tuple[datetime, datetime]:
    if claim.valid_at is not None:
        instant = normalize_datetime(claim.valid_at)
        return instant, instant + timedelta(microseconds=1)
    assert claim.valid_during is not None
    return (
        normalize_datetime(claim.valid_during.starts_at),
        normalize_datetime(claim.valid_during.ends_at),
    )


def _optional_token(value: str | None) -> str | None:
    return normalize_token(value) if value is not None else None
