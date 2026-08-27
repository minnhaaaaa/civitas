"""Resumable Parliament workflow with a LangGraph-compatible topology."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from typing import TypedDict

from civitas.agents.parliament import (
    RoleAgent,
    default_role_agents,
    select_consensus_plan,
    support_counts,
)
from civitas.contracts import WorkflowEvent, WorkflowEventType
from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.enums import JuryState
from civitas.contracts.jury import JuryRequest
from civitas.contracts.optimization import CandidatePlan, OptimizationRequest, OptimizationResult
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.ports.jury import JuryPort
from civitas.ports.optimizer import Optimizer
from civitas.workflow.events import (
    ChallengeRoundPayload,
    ConcessionRoundPayload,
    InvestigationPayload,
    ProposalRoundPayload,
    RunStartedPayload,
    TerminalPayload,
    jury_payload,
    make_event,
)
from civitas.workflow.langgraph_compat import END, START, StateGraph
from civitas.workflow.models import (
    ParliamentContext,
    ParliamentSession,
    WorkflowCheckpoint,
    WorkflowLimits,
    WorkflowPhase,
    WorkflowResult,
)


class TopologyState(TypedDict, total=False):
    jury_state: str
    next_phase: str


class ParliamentWorkflow:
    def __init__(
        self,
        *,
        optimizer: Optimizer,
        jury: JuryPort,
        ids: IDGenerator,
        clock: Clock,
        role_agents: Sequence[RoleAgent] | None = None,
        replanner: Callable[[WorkflowCheckpoint], OptimizationRequest] | None = None,
    ) -> None:
        self._optimizer = optimizer
        self._jury = jury
        self._ids = ids
        self._clock = clock
        self._role_agents = tuple(role_agents or default_role_agents())
        self._replanner = replanner or (lambda checkpoint: checkpoint.optimization_request)

    def start(
        self,
        *,
        planning_run_id: str,
        optimization_request: OptimizationRequest,
    ) -> WorkflowCheckpoint:
        return WorkflowCheckpoint(
            planning_run_id=planning_run_id,
            phase=WorkflowPhase.PROPOSAL,
            cycle=1,
            optimization_request=optimization_request,
        )

    async def run(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        limits: WorkflowLimits,
    ) -> WorkflowResult:
        events: list[WorkflowEvent] = []
        if checkpoint.event_sequence == 0:
            checkpoint, event = self._record(
                checkpoint,
                WorkflowEventType.RUN_STARTED,
                RunStartedPayload(phase=checkpoint.phase, cycle=checkpoint.cycle),
            )
            events.append(event)
        while not checkpoint.completed:
            checkpoint, new_events = await self.advance(checkpoint, limits=limits)
            events.extend(new_events)
        return WorkflowResult(
            checkpoint=checkpoint,
            events=tuple(event.model_dump(mode="python") for event in events),
        )

    async def advance(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        limits: WorkflowLimits,
    ) -> tuple[WorkflowCheckpoint, tuple[WorkflowEvent, ...]]:
        if checkpoint.completed:
            return checkpoint, ()
        if self._bounds_exhausted(checkpoint, limits):
            checkpoint, event = self._terminate(
                checkpoint, WorkflowPhase.ESCALATE, "bounds_exhausted"
            )
            return checkpoint, (event,)
        if checkpoint.phase == WorkflowPhase.PROPOSAL:
            return await self._proposal_round(checkpoint)
        if checkpoint.phase == WorkflowPhase.CHALLENGE:
            return await self._challenge_round(checkpoint)
        if checkpoint.phase == WorkflowPhase.CONCESSION:
            return await self._concession_round(checkpoint)
        if checkpoint.phase == WorkflowPhase.JURY:
            return await self._jury_round(checkpoint, limits=limits)
        if checkpoint.phase == WorkflowPhase.INVESTIGATION:
            return self._investigate(checkpoint, limits=limits)
        raise ValueError(f"unsupported phase {checkpoint.phase}")

    def compile_langgraph(self) -> object:
        graph = StateGraph(TopologyState)

        async def passthrough(state: TopologyState) -> TopologyState:
            return state

        graph.add_node(WorkflowPhase.PROPOSAL.value, passthrough)
        graph.add_node(WorkflowPhase.CHALLENGE.value, passthrough)
        graph.add_node(WorkflowPhase.CONCESSION.value, passthrough)
        graph.add_node(WorkflowPhase.JURY.value, passthrough)
        graph.add_node(WorkflowPhase.INVESTIGATION.value, passthrough)
        graph.add_node(WorkflowPhase.APPROVE.value, passthrough)
        graph.add_node(WorkflowPhase.REJECT.value, passthrough)
        graph.add_node(WorkflowPhase.ESCALATE.value, passthrough)
        graph.add_edge(START, WorkflowPhase.PROPOSAL.value)
        graph.add_edge(WorkflowPhase.PROPOSAL.value, WorkflowPhase.CHALLENGE.value)
        graph.add_edge(WorkflowPhase.CHALLENGE.value, WorkflowPhase.CONCESSION.value)
        graph.add_edge(WorkflowPhase.CONCESSION.value, WorkflowPhase.JURY.value)
        graph.add_conditional_edges(
            WorkflowPhase.JURY.value,
            lambda state: str(state["jury_state"]),
            {
                WorkflowPhase.APPROVE.value: WorkflowPhase.APPROVE.value,
                WorkflowPhase.REJECT.value: WorkflowPhase.REJECT.value,
                WorkflowPhase.ESCALATE.value: WorkflowPhase.ESCALATE.value,
                WorkflowPhase.INVESTIGATION.value: WorkflowPhase.INVESTIGATION.value,
            },
        )
        graph.add_conditional_edges(
            WorkflowPhase.INVESTIGATION.value,
            lambda state: str(state["next_phase"]),
            {
                WorkflowPhase.PROPOSAL.value: WorkflowPhase.PROPOSAL.value,
                WorkflowPhase.ESCALATE.value: WorkflowPhase.ESCALATE.value,
            },
        )
        graph.add_edge(WorkflowPhase.APPROVE.value, END)
        graph.add_edge(WorkflowPhase.REJECT.value, END)
        graph.add_edge(WorkflowPhase.ESCALATE.value, END)
        return graph.compile()

    async def _proposal_round(
        self, checkpoint: WorkflowCheckpoint
    ) -> tuple[WorkflowCheckpoint, tuple[WorkflowEvent, ...]]:
        optimization_result = checkpoint.optimization_result
        if optimization_result is None:
            optimization_result = await self._optimizer.solve(checkpoint.optimization_request)
        context = ParliamentContext(
            cycle=checkpoint.cycle,
            optimization_request=checkpoint.optimization_request,
            optimization_result=optimization_result,
            prior_investigations=checkpoint.investigation_backlog,
            plan_annotations=_plan_annotations(checkpoint.optimization_request),
        )
        proposals = tuple(agent.propose(context) for agent in self._role_agents)
        repeated_evidence_ids = _repeated_evidence_ids(proposals, checkpoint.seen_evidence_ids)
        parliament = ParliamentSession(
            proposals=proposals,
            repeated_evidence_ids=repeated_evidence_ids,
        )
        updated = checkpoint.model_copy(
            update={
                "phase": WorkflowPhase.CHALLENGE,
                "optimization_result": optimization_result,
                "parliament": parliament,
                "tool_calls_used": checkpoint.tool_calls_used + 1,
                "seen_evidence_ids": tuple(
                    sorted(set(checkpoint.seen_evidence_ids).union(_all_evidence_ids(proposals)))
                ),
                "repeated_evidence_hits": checkpoint.repeated_evidence_hits
                + (1 if repeated_evidence_ids else 0),
            }
        )
        updated, event = self._record(
            updated,
            WorkflowEventType.PROPOSAL_CREATED,
            ProposalRoundPayload(
                phase=WorkflowPhase.PROPOSAL,
                cycle=updated.cycle,
                proposal_count=len(proposals),
                repeated_evidence_ids=repeated_evidence_ids,
            ),
        )
        return updated, (event,)

    async def _challenge_round(
        self, checkpoint: WorkflowCheckpoint
    ) -> tuple[WorkflowCheckpoint, tuple[WorkflowEvent, ...]]:
        if checkpoint.parliament is None or checkpoint.optimization_result is None:
            raise ValueError("proposal round must run before challenge round")
        context = ParliamentContext(
            cycle=checkpoint.cycle,
            optimization_request=checkpoint.optimization_request,
            optimization_result=checkpoint.optimization_result,
            prior_investigations=checkpoint.investigation_backlog,
            plan_annotations=_plan_annotations(checkpoint.optimization_request),
        )
        challenges = tuple(
            challenge
            for agent in self._role_agents
            for challenge in agent.challenge(context, checkpoint.parliament.proposals)
        )
        updated = checkpoint.model_copy(
            update={
                "phase": WorkflowPhase.CONCESSION,
                "parliament": checkpoint.parliament.model_copy(update={"challenges": challenges}),
            }
        )
        updated, event = self._record(
            updated,
            WorkflowEventType.TASK_COMPLETED,
            ChallengeRoundPayload(
                phase=WorkflowPhase.CHALLENGE,
                cycle=updated.cycle,
                challenge_count=len(challenges),
            ),
        )
        return updated, (event,)

    async def _concession_round(
        self, checkpoint: WorkflowCheckpoint
    ) -> tuple[WorkflowCheckpoint, tuple[WorkflowEvent, ...]]:
        if checkpoint.parliament is None or checkpoint.optimization_result is None:
            raise ValueError("challenge round must run before concession round")
        counts = support_counts(checkpoint.parliament.proposals)
        context = ParliamentContext(
            cycle=checkpoint.cycle,
            optimization_request=checkpoint.optimization_request,
            optimization_result=checkpoint.optimization_result,
            prior_investigations=checkpoint.investigation_backlog,
            plan_annotations=_plan_annotations(checkpoint.optimization_request),
        )
        concessions = tuple(
            item
            for agent in self._role_agents
            if (item := agent.concede(context, checkpoint.parliament.proposals, counts)) is not None
        )
        selected_plan_id = select_consensus_plan(
            checkpoint.optimization_result,
            checkpoint.parliament.proposals,
            concessions,
        )
        updated = checkpoint.model_copy(
            update={
                "phase": WorkflowPhase.JURY,
                "parliament": checkpoint.parliament.model_copy(
                    update={"concessions": concessions, "selected_plan_id": selected_plan_id}
                ),
            }
        )
        updated, event = self._record(
            updated,
            WorkflowEventType.TASK_COMPLETED,
            ConcessionRoundPayload(
                phase=WorkflowPhase.CONCESSION,
                cycle=updated.cycle,
                concession_count=len(concessions),
                selected_plan_id=selected_plan_id,
            ),
        )
        return updated, (event,)

    async def _jury_round(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        limits: WorkflowLimits,
    ) -> tuple[WorkflowCheckpoint, tuple[WorkflowEvent, ...]]:
        if checkpoint.parliament is None or checkpoint.optimization_result is None:
            raise ValueError("concession round must run before jury")
        selected = _selected_plan(
            checkpoint.optimization_result,
            checkpoint.parliament.selected_plan_id,
        )
        request = JuryRequest(
            planning_run_id=checkpoint.planning_run_id,
            candidate_plan=selected,
            supporting_claim_ids=_supporting_claim_ids(checkpoint.parliament),
            evidence_ids=_supporting_evidence_ids(checkpoint.parliament),
            policy_version="decision-integrity-v1",
            autonomy_budget_exhausted=self._bounds_exhausted(checkpoint, limits),
        )
        evaluation = await self._jury.evaluate(request)
        if evaluation.state == JuryState.APPROVE:
            updated = checkpoint.model_copy(update={"jury_evaluation": evaluation})
            updated, jury_event = self._record(
                updated,
                WorkflowEventType.JURY_EVALUATED,
                jury_payload(evaluation, checkpoint.cycle),
            )
            completed, terminal_event = self._terminate(
                updated, WorkflowPhase.APPROVE, "jury_approved"
            )
            return completed, (jury_event, terminal_event)
        if evaluation.state == JuryState.REJECT:
            updated = checkpoint.model_copy(update={"jury_evaluation": evaluation})
            updated, jury_event = self._record(
                updated,
                WorkflowEventType.JURY_EVALUATED,
                jury_payload(evaluation, checkpoint.cycle),
            )
            completed, terminal_event = self._terminate(
                updated, WorkflowPhase.REJECT, "jury_rejected"
            )
            return completed, (jury_event, terminal_event)
        if evaluation.state == JuryState.ESCALATE:
            updated = checkpoint.model_copy(update={"jury_evaluation": evaluation})
            updated, jury_event = self._record(
                updated,
                WorkflowEventType.JURY_EVALUATED,
                jury_payload(evaluation, checkpoint.cycle),
            )
            completed, terminal_event = self._terminate(
                updated, WorkflowPhase.ESCALATE, "jury_escalated"
            )
            return completed, (jury_event, terminal_event)
        updated = checkpoint.model_copy(
            update={
                "phase": WorkflowPhase.INVESTIGATION,
                "jury_evaluation": evaluation,
                "investigation_backlog": evaluation.required_investigation,
            }
        )
        updated, jury_event = self._record(
            updated,
            WorkflowEventType.JURY_EVALUATED,
            jury_payload(evaluation, checkpoint.cycle),
        )
        updated, investigation_event = self._record(
            updated,
            WorkflowEventType.INVESTIGATION_REQUESTED,
            InvestigationPayload(
                phase=WorkflowPhase.INVESTIGATION,
                cycle=updated.cycle,
                required_investigation=evaluation.required_investigation,
            ),
        )
        return updated, (jury_event, investigation_event)

    def _investigate(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        limits: WorkflowLimits,
    ) -> tuple[WorkflowCheckpoint, tuple[WorkflowEvent, ...]]:
        if checkpoint.repeated_evidence_hits > limits.max_repeated_evidence:
            updated, event = self._terminate(
                checkpoint, WorkflowPhase.ESCALATE, "repeated_evidence"
            )
            return updated, (event,)
        if checkpoint.cycle >= limits.max_cycles:
            updated, event = self._terminate(checkpoint, WorkflowPhase.ESCALATE, "cycle_limit")
            return updated, (event,)
        next_request = self._replanner(checkpoint)
        updated = checkpoint.model_copy(
            update={
                "phase": WorkflowPhase.PROPOSAL,
                "cycle": checkpoint.cycle + 1,
                "optimization_request": next_request,
                "optimization_result": None,
                "parliament": None,
                "jury_evaluation": checkpoint.jury_evaluation,
            }
        )
        updated, event = self._record(
            updated,
            WorkflowEventType.TASK_STARTED,
            InvestigationPayload(
                phase=WorkflowPhase.PROPOSAL,
                cycle=updated.cycle,
                required_investigation=checkpoint.investigation_backlog,
            ),
        )
        return updated, (event,)

    def _bounds_exhausted(self, checkpoint: WorkflowCheckpoint, limits: WorkflowLimits) -> bool:
        now = self._clock.now()
        cost_exhausted = limits.max_cost > 0 and checkpoint.estimated_cost_used > limits.max_cost
        tool_exhausted = (
            limits.max_tool_calls > 0 and checkpoint.tool_calls_used >= limits.max_tool_calls
        )
        return (
            checkpoint.cycle > limits.max_cycles
            or tool_exhausted
            or cost_exhausted
            or now >= limits.deadline_at
        )

    def _terminate(
        self,
        checkpoint: WorkflowCheckpoint,
        phase: WorkflowPhase,
        reason: str,
    ) -> tuple[WorkflowCheckpoint, WorkflowEvent]:
        updated = checkpoint.model_copy(
            update={"phase": phase, "final_state": phase.value, "completed": True}
        )
        return self._record(
            updated,
            WorkflowEventType.RUN_COMPLETED,
            TerminalPayload(
                phase=phase,
                cycle=updated.cycle,
                final_state=phase.value,
                reason=reason,
            ),
        )

    def _record(
        self,
        checkpoint: WorkflowCheckpoint,
        event_type: WorkflowEventType,
        payload: Contract,
    ) -> tuple[WorkflowCheckpoint, WorkflowEvent]:
        sequence = checkpoint.event_sequence + 1
        updated = checkpoint.model_copy(update={"event_sequence": sequence})
        event = make_event(
            event_id=self._ids.new_id("event"),
            planning_run_id=checkpoint.planning_run_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=self._clock.now(),
            payload=payload,
        )
        return updated, event


def _selected_plan(result: OptimizationResult, selected_plan_id: str | None) -> CandidatePlan:
    for plan in result.alternatives:
        if plan.plan_id == selected_plan_id:
            return plan
    raise ValueError("selected plan not found")


def _supporting_claim_ids(parliament: ParliamentSession) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                claim_id
                for proposal in parliament.proposals
                for claim_id in proposal.supporting_claim_ids
            }
        )
    )


def _supporting_evidence_ids(parliament: ParliamentSession) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                evidence_id
                for proposal in parliament.proposals
                for evidence_id in proposal.evidence_ids
            }
        )
    )


def _plan_annotations(request: OptimizationRequest) -> JsonObject:
    value = request.constraints.get("plan_annotations", {})
    if not isinstance(value, dict):
        return {}
    return value


def _all_evidence_ids(proposals: Sequence[object]) -> tuple[str, ...]:
    evidence_ids: set[str] = set()
    for proposal in proposals:
        if hasattr(proposal, "evidence_ids"):
            evidence_ids.update(proposal.evidence_ids)
    return tuple(sorted(evidence_ids))


def _repeated_evidence_ids(
    proposals: Sequence[object], seen_evidence_ids: Sequence[str]
) -> tuple[str, ...]:
    counts = Counter(
        evidence_id
        for proposal in proposals
        if hasattr(proposal, "evidence_ids")
        for evidence_id in proposal.evidence_ids
    )
    repeated = {evidence_id for evidence_id, count in counts.items() if count > 1}
    repeated.update(evidence_id for evidence_id in seen_evidence_ids if evidence_id in counts)
    return tuple(sorted(repeated))
