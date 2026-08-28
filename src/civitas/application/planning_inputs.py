"""Provider-backed preparation of solver inputs before the first Parliament round."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from civitas.application.investigation import EvidenceReader, claim_from_observation
from civitas.contracts.claims import TypedClaim
from civitas.contracts.common import JsonObject, JsonValue
from civitas.contracts.mcp_product import ProcurementGoal
from civitas.contracts.optimization import OptimizationRequest
from civitas.contracts.providers import ProviderEvidenceRead
from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.ports.ids import IDGenerator

_INITIAL_READ_TOOLS = (
    "get_inventory",
    "get_demand",
    "get_supplier_offers",
    "get_lead_times",
    "get_warehouse_capacity",
    "get_transport_capacity",
)


@dataclass(frozen=True, slots=True)
class PreparedPlanningInputs:
    optimization_request: OptimizationRequest
    reads_and_claims: tuple[tuple[ProviderEvidenceRead, tuple[TypedClaim, ...]], ...]


class ProviderPlanningInputAssembler:
    """Retrieve typed operational facts and translate them into OR-Tools inputs."""

    def __init__(
        self,
        *,
        reader: EvidenceReader,
        ids: IDGenerator,
        server_name: str,
    ) -> None:
        self._reader = reader
        self._ids = ids
        self._server_name = server_name

    async def prepare(
        self,
        *,
        organization_id: str,
        run_id: str,
        goal: ProcurementGoal,
        base_request: OptimizationRequest,
    ) -> PreparedPlanningInputs:
        async def retrieve(tool_name: str) -> tuple[ProviderEvidenceRead, tuple[TypedClaim, ...]]:
            call = MCPToolCall(
                call_id=self._ids.new_id("mcp-read"),
                server_name=self._server_name,
                tool_name=tool_name,
                arguments={"organization_id": organization_id, "planning_run_id": run_id},
                access_mode=MCPAccessMode.READ,
            )
            read = await self._reader.read(
                call=call,
                evidence_id=self._ids.new_id("evidence"),
                agent_id="planner-input-assembly",
            )
            claims = tuple(
                claim_from_observation(
                    observation,
                    claim_id=self._ids.new_id("claim"),
                    organization_id=organization_id,
                )
                for observation in read.observations
            )
            evidence = read.evidence.model_copy(
                update={"claim_ids": tuple(claim.claim_id for claim in claims)}
            )
            return read.model_copy(update={"evidence": evidence}), claims

        prepared = tuple(await asyncio.gather(*(retrieve(tool) for tool in _INITIAL_READ_TOOLS)))
        constraints = _solver_constraints(base_request.constraints, goal=goal, prepared=prepared)
        version = hashlib.sha256(
            json.dumps(
                [read.evidence.identity.raw_response_sha256 for read, _ in prepared],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return PreparedPlanningInputs(
            optimization_request=base_request.model_copy(
                update={
                    "input_data_version": f"provider-{version}",
                    "constraints": constraints,
                }
            ),
            reads_and_claims=prepared,
        )


def _solver_constraints(
    existing: JsonObject,
    *,
    goal: ProcurementGoal,
    prepared: tuple[tuple[ProviderEvidenceRead, tuple[TypedClaim, ...]], ...],
) -> JsonObject:
    constraints = dict(existing)
    buckets = _buckets(goal)
    constraints.setdefault("base_unit", "each")
    constraints.setdefault("unit_definitions", {str(constraints["base_unit"]): "1"})
    constraints["provider_evidence_required"] = True
    constraints["buckets"] = cast(JsonValue, buckets)
    first_bucket = str(buckets[0]["bucket_id"])
    for read, _ in prepared:
        payload = read.result.payload
        if read.call.tool_name == "get_demand":
            constraints["demands"] = [
                {
                    **record,
                    "demand_id": record.get("demand_id", f"demand-{index}"),
                    "bucket_id": record.get("bucket_id", first_bucket),
                }
                for index, record in enumerate(_records(payload, "demands"), 1)
            ]
        elif read.call.tool_name == "get_inventory":
            constraints["inventory_lots"] = [
                {
                    **record,
                    "quantity": record.get("quantity", record.get("available_quantity", 0)),
                    "expires_at": record.get(
                        "expires_at", (goal.horizon_ends_at + timedelta(days=1)).isoformat()
                    ),
                    "status": record.get("status", "available"),
                }
                for record in _records(payload, "lots")
            ]
        elif read.call.tool_name == "get_supplier_offers":
            constraints["supplier_offers"] = [
                {
                    **record,
                    "arrival_bucket_id": record.get("arrival_bucket_id", first_bucket),
                    "capacity": record.get("capacity", record.get("available_quantity", 0)),
                    "unit_cost": record.get("unit_cost", record.get("unit_price", 0)),
                }
                for record in _records(payload, "offers")
            ]
        elif read.call.tool_name == "get_warehouse_capacity":
            constraints["warehouse_capacities"] = [
                {
                    **record,
                    "bucket_id": record.get("bucket_id", first_bucket),
                    "maximum_base_units": record.get(
                        "maximum_base_units",
                        record.get(
                            "remaining_capacity_units", record.get("available_quantity", 0)
                        ),
                    ),
                }
                for record in _records(payload, "records")
            ]
        elif read.call.tool_name == "get_transport_capacity":
            constraints["transport_lanes"] = [
                {
                    **record,
                    "lane_id": record.get("lane_id", f"lane-{index}"),
                    "sku_ids": record.get("sku_ids", list(goal.sku_ids)),
                    "capacity": record.get("capacity", record.get("available_quantity", 0)),
                }
                for index, record in enumerate(_records(payload, "records"), 1)
            ]
    missing = [key for key in ("demands", "supplier_offers") if not constraints.get(key)]
    if missing:
        raise ValueError("provider did not supply required planning inputs: " + ", ".join(missing))
    return constraints


def _buckets(goal: ProcurementGoal) -> list[JsonObject]:
    result: list[JsonObject] = []
    cursor: datetime = goal.horizon_starts_at
    sequence = 0
    while cursor < goal.horizon_ends_at:
        ends_at = min(cursor + timedelta(days=1), goal.horizon_ends_at)
        result.append(
            {
                "bucket_id": f"bucket-{sequence}",
                "start": cursor.isoformat(),
                "end": ends_at.isoformat(),
                "urgency": max(1, len(result) + 1),
            }
        )
        cursor = ends_at
        sequence += 1
    return result


def _records(payload: JsonObject, key: str) -> list[JsonObject]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"provider payload {key} must contain objects")
    return [item for item in value if isinstance(item, dict)]
