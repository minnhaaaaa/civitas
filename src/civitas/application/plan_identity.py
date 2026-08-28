"""Canonical identity and approval limits for solver-produced plans."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from civitas.contracts.mcp_product import ApprovedTotals
from civitas.contracts.optimization import CandidatePlan


def selected_plan_hash(plan: CandidatePlan) -> str:
    """Return the immutable hash used by decisions, approvals, and execution."""
    payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approved_totals(plan: CandidatePlan) -> ApprovedTotals:
    """Derive approval ceilings from the exact selected solver plan."""
    return ApprovedTotals(
        currency=str(plan.metrics.get("currency", "USD")),
        maximum_landed_cost=sum((line.landed_cost for line in plan.procurement), Decimal("0")),
        maximum_procurement_lines=len(plan.procurement),
        maximum_distribution_lines=len(plan.distribution),
    )
