"""Canonical identity and approval limits for solver-produced plans."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from civitas.contracts.mcp_product import ApprovedTotals
from civitas.contracts.optimization import CandidatePlan


def selected_plan_hash(plan: CandidatePlan) -> str:
    """Return the immutable hash used by decisions, approvals, and execution."""
    payload = json.dumps(
        _canonical(plan.model_dump(mode="python")),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    """Normalize persistence-equivalent values before approval hashing."""

    if isinstance(value, Decimal):
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def approved_totals(plan: CandidatePlan) -> ApprovedTotals:
    """Derive approval ceilings from the exact selected solver plan."""
    return ApprovedTotals(
        currency=str(plan.metrics.get("currency", "USD")),
        maximum_landed_cost=sum((line.landed_cost for line in plan.procurement), Decimal("0")),
        maximum_procurement_lines=len(plan.procurement),
        maximum_distribution_lines=len(plan.distribution),
    )
