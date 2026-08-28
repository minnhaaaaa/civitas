"""Workers that execute durable planning transitions and read-only investigations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from civitas.contracts.evidence import EvidenceRecord
from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.evidence.dissent import DissentInvestigationPlan, DissentProtocol, DissentReport
from civitas.integrations.mcp import DissentMCPClient, evidence_from_tool_result
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.workflow.checkpointing import WorkflowCheckpointStore
from civitas.workflow.models import WorkflowCheckpoint, WorkflowLimits
from civitas.workflow.orchestrator import ParliamentWorkflow


class DurableWorkflowWorker:
    """Claims exactly one run and atomically checkpoints one completed transition."""

    def __init__(
        self,
        *,
        worker_id: str,
        workflow: ParliamentWorkflow,
        store: WorkflowCheckpointStore,
        clock: Clock,
        limits: WorkflowLimits | None = None,
        lease_for: timedelta = timedelta(minutes=1),
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not worker_id.strip() or lease_for <= timedelta(0):
            raise ValueError("worker_id and a positive lease duration are required")
        self._worker_id = worker_id
        self._workflow = workflow
        self._store = store
        self._clock = clock
        self._limits = limits
        self._lease_for = lease_for
        self._close = close

    async def enqueue(
        self, checkpoint: WorkflowCheckpoint, *, limits: WorkflowLimits | None = None
    ) -> None:
        await self._store.enqueue(checkpoint, limits=limits or self._limits)

    async def recover_abandoned(self) -> int:
        """Make expired leases eligible before the next polling cycle."""

        return await self._store.recover_abandoned(now=self._clock.now())

    async def process_next(self) -> bool:
        now = self._clock.now()
        lease = await self._store.claim(
            worker_id=self._worker_id, now=now, lease_for=self._lease_for
        )
        if lease is None:
            return False
        try:
            limits = lease.limits or self._limits
            if limits is None:
                raise RuntimeError("claimed workflow has no persisted autonomy limits")
            checkpoint, events = await self._workflow.advance(lease.checkpoint, limits=limits)
            await self._store.commit_transition(
                lease=lease,
                checkpoint=checkpoint,
                events=events,
                now=self._clock.now(),
            )
        except BaseException:
            await self._store.release(lease)
            raise
        return True

    async def close(self) -> None:
        if self._close is not None:
            await self._close()


@dataclass(frozen=True, slots=True)
class InvestigationCall:
    check: str
    call: MCPToolCall
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.call.access_mode is not MCPAccessMode.READ:
            raise ValueError("investigation calls must be read-only")


class InvestigationWorker:
    """Executes a predeclared clean-room Dissent investigation without writes."""

    def __init__(self, *, mcp: DissentMCPClient, ids: IDGenerator) -> None:
        self._mcp = mcp
        self._ids = ids

    async def investigate(
        self,
        *,
        checks: Sequence[InvestigationCall],
        checked_claim_ids: Sequence[str],
    ) -> tuple[DissentReport, tuple[EvidenceRecord, ...]]:
        plan = DissentInvestigationPlan(
            context_id=self._mcp.namespace.context_id,
            memory_namespace=self._mcp.namespace.memory_namespace,
            tool_cache_namespace=self._mcp.namespace.tool_cache_namespace,
            checks=tuple(item.check for item in checks),
            tool_budget=max(1, len(checks)),
        )
        report = DissentProtocol.record_plan(plan)
        evidence: list[EvidenceRecord] = []
        unavailable: list[str] = []
        for item in checks:
            try:
                result = await self._mcp.invoke(item.call)
            except Exception:
                unavailable.append(item.check)
                continue
            evidence.append(
                evidence_from_tool_result(
                    evidence_id=self._ids.new_id("dissent-evidence"),
                    call=item.call,
                    result=result,
                    claim_ids=item.claim_ids,
                    agent_id="dissent",
                )
            )
        report = DissentProtocol.record_fresh_retrieval(
            report,
            evidence_ids=tuple(item.evidence_id for item in evidence),
            unavailable_checks=tuple(unavailable),
        )
        report = DissentProtocol.compare_with_existing_graph(
            report, checked_claim_ids=tuple(checked_claim_ids)
        )
        return report, tuple(evidence)
