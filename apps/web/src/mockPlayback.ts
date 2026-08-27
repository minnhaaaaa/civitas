import type {
  AlternativePlan,
  EvidenceGraphSnapshot,
  ExecutionSnapshot,
  JsonObject,
  JuryCycleSnapshot,
  ParliamentAgent,
  ScenarioRecord,
  SseEnvelope,
  WorkflowEvent,
  WorkflowEventType,
} from "./contracts";

type StreamListener = (event: MessageEvent<string>) => void;
type TypedListener = (event: { data: WorkflowEvent }) => void;

function createEvent<TPayload extends JsonObject>(
  runId: string,
  sequence: number,
  eventType: WorkflowEventType,
  occurredAt: string,
  payload: TPayload,
  actorId?: string,
): SseEnvelope<TPayload> {
  return {
    id: String(sequence),
    event: eventType,
    data: {
      event_id: `event-${sequence}`,
      planning_run_id: runId,
      sequence,
      event_type: eventType,
      occurred_at: occurredAt,
      actor_id: actorId,
      payload,
    },
  };
}

const runId = "planning-run-false-consensus-demo";

const parliament: ParliamentAgent[] = [
  {
    agent_id: "demand",
    label: "Demand",
    objective: "Protect day-one fulfillment for warehouse north.",
    stance: "Push for an immediate inbound that closes the remaining 4-unit gap.",
    supplier: "Supplier A",
    quantity: 4,
    evidence_ids: ["evidence.inventory.local", "evidence.supplier_a.stale_offer"],
    source_groups: ["inventory_api:warehouse-north-ledger", "supplier_api:supplier-a-master"],
    uses_shared_source: true,
    agent_echo: false,
  },
  {
    agent_id: "cost",
    label: "Cost",
    objective: "Minimize landed cost without breaking minimum service.",
    stance: "Supplier A is cheapest and appears immediately feasible.",
    supplier: "Supplier A",
    quantity: 4,
    evidence_ids: ["evidence.supplier_a.stale_offer", "evidence.supplier_a.echo"],
    source_groups: ["supplier_api:supplier-a-master", "agent_summary:agent-synthesis"],
    uses_shared_source: true,
    agent_echo: true,
  },
  {
    agent_id: "freshness",
    label: "Freshness",
    objective: "Avoid day-two overhang and unnecessary spoilage.",
    stance: "A small top-up is acceptable if the arrival is genuinely day one.",
    supplier: "Supplier A",
    quantity: 4,
    evidence_ids: ["evidence.inventory.local", "evidence.supplier_a.stale_offer"],
    source_groups: ["inventory_api:warehouse-north-ledger", "supplier_api:supplier-a-master"],
    uses_shared_source: true,
    agent_echo: false,
  },
  {
    agent_id: "logistics",
    label: "Logistics",
    objective: "Honor arrival buckets and avoid infeasible transfers.",
    stance: "Supplier A looks operationally easiest before the audit arrives.",
    supplier: "Supplier A",
    quantity: 4,
    evidence_ids: ["evidence.warehouse.capacity", "evidence.supplier_a.stale_offer"],
    source_groups: ["warehouse_api:warehouse-north-capacity", "supplier_api:supplier-a-master"],
    uses_shared_source: true,
    agent_echo: false,
  },
  {
    agent_id: "supplier",
    label: "Supplier",
    objective: "Prefer the most reliable feasible supplier.",
    stance: "Supplier B is safer if the live lead time is confirmed.",
    supplier: "Supplier B",
    quantity: 4,
    evidence_ids: ["evidence.supplier_b.stale_lead_time"],
    source_groups: ["supplier_api:supplier-b-master"],
    uses_shared_source: false,
    agent_echo: false,
  },
  {
    agent_id: "waste",
    label: "Waste",
    objective: "Hold the order at the exact shortage quantity.",
    stance: "No surplus. Order only what closes the exposed demand.",
    supplier: "Supplier A",
    quantity: 4,
    evidence_ids: ["evidence.inventory.local", "evidence.supplier_a.stale_offer"],
    source_groups: ["inventory_api:warehouse-north-ledger", "supplier_api:supplier-a-master"],
    uses_shared_source: true,
    agent_echo: false,
  },
];

const alternatives: AlternativePlan[] = [
  {
    plan_id: `${runId}-balanced-01`,
    label: "Balanced A",
    supplier_mix: "Supplier A tops up 4 units for day one.",
    fulfillment: 100,
    landed_cost: 16,
    waste_risk: 8,
    supplier_risk: 72,
    max_role_regret: 38,
    selected_in_cycle: 1,
    status: "investigate",
  },
  {
    plan_id: `${runId}-risk-01`,
    label: "Risk-Reduced B",
    supplier_mix: "Supplier B covers the 4-unit gap after investigation.",
    fulfillment: 100,
    landed_cost: 28,
    waste_risk: 10,
    supplier_risk: 18,
    max_role_regret: 14,
    selected_in_cycle: 2,
    status: "approved",
  },
  {
    plan_id: `${runId}-holding-01`,
    label: "Hold and Wait",
    supplier_mix: "Defer procurement and absorb a day-one shortfall.",
    fulfillment: 67,
    landed_cost: 0,
    waste_risk: 4,
    supplier_risk: 61,
    max_role_regret: 49,
    selected_in_cycle: null,
    status: "discarded",
  },
];

const jury: JuryCycleSnapshot[] = [
  {
    cycle: 1,
    state: "investigate",
    integrity_score: 41,
    summary:
      "Consensus formed quickly, but most support collapses to one stale supplier source plus an agent echo.",
    reasons: [
      "Critical lead-time support is not independent.",
      "Dissent found a contradictory live audit for supplier A.",
      "Execution freshness gate cannot pass on stale lead-time evidence.",
    ],
    components: {
      critical_claim_coverage: 78,
      evidence_independence: 24,
      provenance_completeness: 86,
      evidence_freshness: 22,
      canonical_source_diversity: 46,
      contradiction_resolution: 18,
      dissent_robustness: 70,
    },
    gates: [
      {
        gate_code: "critical-claim-support",
        passed: false,
        reason_codes: ["critical_claim_missing_external_support"],
      },
      {
        gate_code: "high-severity-contradiction",
        passed: false,
        reason_codes: ["supplier_a_live_lead_time_conflict"],
      },
      {
        gate_code: "execution-freshness",
        passed: false,
        reason_codes: ["lead_time_evidence_stale"],
      },
    ],
    required_investigation: [
      "Verify current supplier lead time from an independent source.",
      "Refresh the offer set before solver comparison.",
    ],
  },
  {
    cycle: 2,
    state: "approve",
    integrity_score: 91,
    summary:
      "Replanning replaces shared stale support with fresh offer and audit evidence for supplier B.",
    reasons: [
      "Supplier B now has independent public and audit support.",
      "No unresolved contradiction remains on the critical lead-time claim.",
      "Execution revalidation can proceed within freshness TTLs.",
    ],
    components: {
      critical_claim_coverage: 96,
      evidence_independence: 92,
      provenance_completeness: 95,
      evidence_freshness: 90,
      canonical_source_diversity: 88,
      contradiction_resolution: 94,
      dissent_robustness: 82,
    },
    gates: [
      { gate_code: "critical-claim-support", passed: true, reason_codes: [] },
      { gate_code: "high-severity-contradiction", passed: true, reason_codes: [] },
      { gate_code: "execution-freshness", passed: true, reason_codes: [] },
    ],
    required_investigation: [],
  },
];

const evidenceGraphs: EvidenceGraphSnapshot[] = [
  {
    cycle: 1,
    nodes: [
      {
        id: "source-a",
        position: { x: 40, y: 80 },
        data: {
          label: "Supplier A master",
          kind: "source",
          detail: "Shared public source",
          shared: true,
        },
      },
      {
        id: "source-inventory",
        position: { x: 40, y: 260 },
        data: { label: "Inventory ledger", kind: "source", detail: "Warehouse north balance" },
      },
      {
        id: "evidence-a",
        position: { x: 280, y: 70 },
        data: {
          label: "Stale offer",
          kind: "evidence",
          detail: "Lead time 1 day + cheapest price",
          shared: true,
          contradicted: true,
        },
      },
      {
        id: "echo-a",
        position: { x: 280, y: 170 },
        data: {
          label: "Agent echo",
          kind: "evidence",
          detail: "Cost agent repeated A's assumption",
          shared: true,
        },
      },
      {
        id: "claim-a",
        position: { x: 540, y: 70 },
        data: {
          label: "Lead time claim",
          kind: "claim",
          detail: "Supplier A can arrive day one",
          shared: true,
          contradicted: true,
        },
      },
      {
        id: "claim-price",
        position: { x: 540, y: 170 },
        data: {
          label: "Unit price claim",
          kind: "claim",
          detail: "Supplier A is cheapest",
          shared: true,
        },
      },
      {
        id: "dissent-source",
        position: { x: 40, y: 430 },
        data: {
          label: "Partner audit",
          kind: "source",
          detail: "Clean-room retrieval",
          cleanRoom: true,
        },
      },
      {
        id: "dissent-evidence",
        position: { x: 280, y: 430 },
        data: {
          label: "Live audit",
          kind: "evidence",
          detail: "Supplier A lead time is 10 days",
          cleanRoom: true,
          contradicted: true,
        },
      },
      {
        id: "dissent-claim",
        position: { x: 540, y: 430 },
        data: {
          label: "Contradiction",
          kind: "claim",
          detail: "Supplier A is not day-one feasible",
          cleanRoom: true,
          contradicted: true,
        },
      },
      {
        id: "agents",
        position: { x: 820, y: 120 },
        data: {
          label: "Parliament bloc",
          kind: "agent",
          detail: "5 agents converge on Supplier A",
        },
      },
      {
        id: "jury",
        position: { x: 820, y: 350 },
        data: {
          label: "Jury gate",
          kind: "decision",
          detail: "State: investigate",
          contradicted: true,
        },
      },
    ],
    edges: [
      {
        id: "s1",
        source: "source-a",
        target: "evidence-a",
        data: { kind: "retrieved_from", shared: true },
      },
      {
        id: "s2",
        source: "evidence-a",
        target: "echo-a",
        data: { kind: "derived_from", shared: true },
      },
      {
        id: "s3",
        source: "evidence-a",
        target: "claim-a",
        data: { kind: "supports", shared: true },
      },
      {
        id: "s4",
        source: "evidence-a",
        target: "claim-price",
        data: { kind: "supports", shared: true },
      },
      { id: "s5", source: "echo-a", target: "claim-a", data: { kind: "supports", shared: true } },
      { id: "s6", source: "source-inventory", target: "agents", data: { kind: "used_in" } },
      { id: "s7", source: "claim-a", target: "agents", data: { kind: "used_in", shared: true } },
      {
        id: "s8",
        source: "claim-price",
        target: "agents",
        data: { kind: "used_in", shared: true },
      },
      {
        id: "s9",
        source: "dissent-source",
        target: "dissent-evidence",
        data: { kind: "retrieved_from" },
      },
      {
        id: "s10",
        source: "dissent-evidence",
        target: "dissent-claim",
        data: { kind: "supports" },
      },
      { id: "s11", source: "dissent-claim", target: "claim-a", data: { kind: "contradicts" } },
      { id: "s12", source: "agents", target: "jury", data: { kind: "used_in" } },
      { id: "s13", source: "dissent-claim", target: "jury", data: { kind: "used_in" } },
    ],
  },
  {
    cycle: 2,
    nodes: [
      {
        id: "source-b-offer",
        position: { x: 40, y: 90 },
        data: { label: "Supplier B live offer", kind: "source", detail: "Fresh public offer" },
      },
      {
        id: "source-b-audit",
        position: { x: 40, y: 300 },
        data: { label: "Supplier B audit", kind: "source", detail: "Independent public audit" },
      },
      {
        id: "source-dissent-b",
        position: { x: 40, y: 510 },
        data: {
          label: "Clean-room audit",
          kind: "source",
          detail: "Dissent re-check",
          cleanRoom: true,
        },
      },
      {
        id: "evidence-b-offer",
        position: { x: 300, y: 80 },
        data: {
          label: "Live offer evidence",
          kind: "evidence",
          detail: "Price 7, arrival day one",
        },
      },
      {
        id: "evidence-b-audit",
        position: { x: 300, y: 300 },
        data: { label: "Audit evidence", kind: "evidence", detail: "Lead time confirmed at 1 day" },
      },
      {
        id: "evidence-b-dissent",
        position: { x: 300, y: 510 },
        data: {
          label: "Dissent evidence",
          kind: "evidence",
          detail: "Independent clean-room confirmation",
          cleanRoom: true,
        },
      },
      {
        id: "claim-b-lead",
        position: { x: 560, y: 220 },
        data: { label: "Lead time claim", kind: "claim", detail: "Supplier B is day-one feasible" },
      },
      {
        id: "claim-b-price",
        position: { x: 560, y: 80 },
        data: { label: "Price claim", kind: "claim", detail: "Supplier B costs more but is valid" },
      },
      {
        id: "agents-b",
        position: { x: 830, y: 160 },
        data: {
          label: "Parliament convergence",
          kind: "agent",
          detail: "Solver comparison favors Supplier B",
        },
      },
      {
        id: "jury-b",
        position: { x: 830, y: 380 },
        data: { label: "Jury approve", kind: "decision", detail: "Integrity 91, all gates passed" },
      },
    ],
    edges: [
      {
        id: "b1",
        source: "source-b-offer",
        target: "evidence-b-offer",
        data: { kind: "retrieved_from" },
      },
      {
        id: "b2",
        source: "source-b-audit",
        target: "evidence-b-audit",
        data: { kind: "retrieved_from" },
      },
      {
        id: "b3",
        source: "source-dissent-b",
        target: "evidence-b-dissent",
        data: { kind: "retrieved_from" },
      },
      { id: "b4", source: "evidence-b-offer", target: "claim-b-price", data: { kind: "supports" } },
      { id: "b5", source: "evidence-b-offer", target: "claim-b-lead", data: { kind: "supports" } },
      { id: "b6", source: "evidence-b-audit", target: "claim-b-lead", data: { kind: "supports" } },
      {
        id: "b7",
        source: "evidence-b-dissent",
        target: "claim-b-lead",
        data: { kind: "supports" },
      },
      { id: "b8", source: "claim-b-price", target: "agents-b", data: { kind: "used_in" } },
      { id: "b9", source: "claim-b-lead", target: "agents-b", data: { kind: "used_in" } },
      { id: "b10", source: "agents-b", target: "jury-b", data: { kind: "used_in" } },
    ],
  },
];

const execution: ExecutionSnapshot = {
  approved_plan_id: `${runId}-risk-01`,
  current_state: "duplicate",
  detail:
    "The approved write succeeds once. The repeated write is downgraded by idempotency protection.",
  freshness_ttls: [
    { label: "Inventory balances", ttl: "2 min", state: "fresh" },
    { label: "Warehouse capacity", ttl: "2 min", state: "fresh" },
    { label: "Supplier lead time", ttl: "10 min", state: "fresh" },
    { label: "Supplier offers", ttl: "10 min", state: "fresh" },
  ],
  steps: [
    { label: "Jury approval", state: "approved", detail: "Cycle 2 plan cleared every hard gate." },
    {
      label: "Freshness revalidation",
      state: "succeeded",
      detail: "Mutable inputs were refreshed before write.",
    },
    {
      label: "Execution write",
      state: "succeeded",
      detail: "Procurement order was created for Supplier B.",
    },
    {
      label: "Duplicate retry",
      state: "duplicate",
      detail: "Second write attempt was blocked by the execution ledger.",
    },
  ],
};

const events: SseEnvelope[] = [
  createEvent(runId, 1, "run.started", "2026-08-27T10:30:00Z", {
    phase: "planning",
    cycle: 1,
    note: "The seven-day procurement case opens with day-one demand pressure at warehouse north.",
  }),
  createEvent(runId, 2, "evidence.recorded", "2026-08-27T10:30:02Z", {
    phase: "evidence",
    cycle: 1,
    evidence_id: "evidence.inventory.local",
    claim_ids: ["claim.inventory.local"],
    origin: "external",
    source_group: "inventory_api:warehouse-north-ledger",
    summary: "Warehouse North has 2 units on hand.",
    note: "Inventory imported for planning.",
  }),
  createEvent(runId, 3, "evidence.recorded", "2026-08-27T10:30:04Z", {
    phase: "evidence",
    cycle: 1,
    evidence_id: "evidence.supplier_a.stale_offer",
    claim_ids: ["claim.supplier_a.lead_time", "claim.supplier_a.unit_price"],
    origin: "external",
    source_group: "supplier_api:supplier-a-master",
    summary: "Supplier A appears to arrive in 1 day at the cheapest price.",
    note: "Shared supplier-offer evidence made supplier A look immediately feasible.",
  }),
  createEvent(
    runId,
    4,
    "evidence.recorded",
    "2026-08-27T10:30:05Z",
    {
      phase: "evidence",
      cycle: 1,
      evidence_id: "evidence.supplier_a.echo",
      claim_ids: ["claim.supplier_a.lead_time"],
      origin: "agent_derived",
      source_group: "agent_summary:agent-synthesis",
      summary: "Cost agent repeated supplier A's lead-time assumption.",
      note: "An agent-derived echo reused the same upstream assumption.",
    },
    "cost-agent",
  ),
  createEvent(runId, 5, "proposal.created", "2026-08-27T10:30:07Z", {
    phase: "parliament",
    cycle: 1,
    proposal_count: 5,
    repeated_evidence_ids: ["evidence.supplier_a.stale_offer", "evidence.supplier_a.echo"],
    summary: "Five of six roles initially converge on Supplier A.",
  }),
  createEvent(
    runId,
    6,
    "task.started",
    "2026-08-27T10:30:09Z",
    {
      phase: "jury",
      cycle: 1,
      task: "clean_room_dissent",
      context_id: "clean-room-1",
      memory_namespace: "dissent.false-consensus.cycle-1",
      tool_cache_namespace: "dissent-cache.false-consensus.cycle-1",
      note: "Dissent isolates itself from Parliament memory before re-checking lead time.",
    },
    "dissent",
  ),
  createEvent(
    runId,
    7,
    "evidence.recorded",
    "2026-08-27T10:30:11Z",
    {
      phase: "jury",
      cycle: 1,
      evidence_id: "evidence.dissent.initial",
      claim_ids: ["claim.supplier_a.lead_time.live"],
      origin: "external",
      source_group: "partner_audit:supplier-a-live-audit",
      summary: "Fresh Dissent retrieval shows supplier A is actually slow.",
      note: "Clean-room audit retrieved an independent contradiction.",
    },
    "dissent",
  ),
  createEvent(runId, 8, "jury.evaluated", "2026-08-27T10:30:14Z", {
    phase: "jury",
    cycle: 1,
    state: "investigate",
    plan_id: `${runId}-balanced-01`,
    integrity_score: 41,
    note: "Jury finds false consensus built on one shared stale source and an echo chain.",
    required_investigation: [
      "Verify current supplier lead time from an independent source.",
      "Refresh the offer set before solver comparison.",
    ],
  }),
  createEvent(runId, 9, "investigation.requested", "2026-08-27T10:30:16Z", {
    phase: "investigation",
    cycle: 1,
    required_investigation: [
      "Verify current supplier lead time from an independent source.",
      "Refresh the offer set before solver comparison.",
    ],
    note: "Planner reopens the case instead of executing the initial consensus.",
  }),
  createEvent(runId, 10, "evidence.recorded", "2026-08-27T10:30:20Z", {
    phase: "investigation",
    cycle: 2,
    evidence_id: "evidence.supplier_b.live_offer",
    claim_ids: ["claim.supplier_b.lead_time", "claim.supplier_b.unit_price"],
    origin: "external",
    source_group: "supplier_api:supplier-b-live-offer",
    summary: "Supplier B is costlier but now arrives in the day-one bucket.",
    note: "Investigation refreshed the public offer set and replaced supplier A with supplier B.",
  }),
  createEvent(runId, 11, "evidence.recorded", "2026-08-27T10:30:22Z", {
    phase: "investigation",
    cycle: 2,
    evidence_id: "evidence.supplier_b.live_audit",
    claim_ids: ["claim.supplier_b.lead_time"],
    origin: "external",
    source_group: "partner_audit:supplier-b-public-audit",
    summary: "An independent audit confirms Supplier B can still arrive in 1 day.",
    note: "Investigation added an independent public audit for supplier B lead time.",
  }),
  createEvent(
    runId,
    12,
    "task.completed",
    "2026-08-27T10:30:24Z",
    {
      phase: "jury",
      cycle: 2,
      task: "clean_room_dissent",
      checked_claims: ["claim.supplier_b.lead_time.live"],
      note: "Dissent completed its fresh verification for Supplier B.",
    },
    "dissent",
  ),
  createEvent(runId, 13, "jury.evaluated", "2026-08-27T10:30:27Z", {
    phase: "jury",
    cycle: 2,
    state: "approve",
    plan_id: `${runId}-risk-01`,
    integrity_score: 91,
    note: "The replanned Supplier B option clears every integrity component and hard gate.",
    required_investigation: [],
  }),
  createEvent(runId, 14, "execution.updated", "2026-08-27T10:30:30Z", {
    phase: "execution",
    cycle: 2,
    state: "succeeded",
    approved_plan_id: `${runId}-risk-01`,
    detail: "Freshness checks passed and the procurement order was created.",
  }),
  createEvent(runId, 15, "execution.updated", "2026-08-27T10:30:32Z", {
    phase: "execution",
    cycle: 2,
    state: "duplicate",
    approved_plan_id: `${runId}-risk-01`,
    detail: "A repeated write attempt was downgraded to duplicate by the execution ledger.",
  }),
  createEvent(runId, 16, "run.completed", "2026-08-27T10:30:34Z", {
    phase: "terminal",
    cycle: 2,
    final_state: "approve",
    reason: "Supplier B was verified, approved, executed, and duplicate-protected.",
  }),
];

export const scenarioRecord: ScenarioRecord = {
  scenario: {
    scenario_id: "false-consensus-demo",
    title: "False consensus with clean-room dissent",
    description:
      "Supplier A initially wins on shared stale evidence, Dissent finds the contradiction, the plan is reopened, Supplier B is approved, then execution is revalidated and duplicate-protected.",
  },
  run_id: runId,
  procurement_goal: {
    title: "Autonomous food procurement",
    thesis:
      "Make Parliament visible, then make the Jury prove whether that agreement deserves to execute.",
    demandWindow: "7-day horizon, with a day-one shortage of 4 units at warehouse north.",
    warehouses: ["Warehouse North"],
    suppliers: ["Supplier A", "Supplier B"],
  },
  parliament,
  alternatives,
  jury,
  evidenceGraphs,
  execution,
  events,
};

export class MockEventSource {
  onmessage: StreamListener | null = null;
  onerror: (() => void) | null = null;

  readonly url: string;

  #closed = false;
  #listeners = new Map<WorkflowEventType, Set<TypedListener>>();
  #timers: number[] = [];

  constructor(
    url: string,
    private readonly frames: SseEnvelope[],
    private readonly paceMs = 850,
  ) {
    this.url = url;
  }

  play() {
    this.close();
    this.#closed = false;
    this.frames.forEach((frame, index) => {
      const timer = window.setTimeout(() => {
        if (this.#closed) {
          return;
        }
        const payload = JSON.stringify(frame.data);
        this.onmessage?.(
          new MessageEvent("message", {
            data: payload,
          }),
        );
        const typedListeners = this.#listeners.get(frame.event);
        typedListeners?.forEach((listener) => listener({ data: frame.data }));
      }, index * this.paceMs);
      this.#timers.push(timer);
    });
  }

  addEventListener(type: WorkflowEventType, listener: TypedListener) {
    const current = this.#listeners.get(type) ?? new Set<TypedListener>();
    current.add(listener);
    this.#listeners.set(type, current);
  }

  removeEventListener(type: WorkflowEventType, listener: TypedListener) {
    this.#listeners.get(type)?.delete(listener);
  }

  close() {
    this.#closed = true;
    this.#timers.forEach((timer) => window.clearTimeout(timer));
    this.#timers = [];
  }
}

export function createMockRunStream(paceMs?: number) {
  const source = new MockEventSource("/mock/demo-runs/false-consensus-demo/events", events, paceMs);
  return {
    source,
    snapshot: {
      run_id: runId,
      scenario_id: scenarioRecord.scenario.scenario_id,
      title: scenarioRecord.scenario.title,
      events,
    },
  };
}

export function getPlaybackCycle(visibleEvents: WorkflowEvent[]) {
  return visibleEvents.reduce((latest, event) => {
    const cycle = Number(event.payload.cycle ?? latest);
    return Number.isFinite(cycle) ? Math.max(latest, cycle) : latest;
  }, 1);
}
