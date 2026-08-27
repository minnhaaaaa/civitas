export type WorkflowEventType =
  | "run.started"
  | "task.started"
  | "task.completed"
  | "evidence.recorded"
  | "proposal.created"
  | "jury.evaluated"
  | "investigation.requested"
  | "execution.updated"
  | "run.completed";

export type JuryState = "approve" | "investigate" | "escalate" | "reject";

export type ExecutionState =
  "pending" | "succeeded" | "failed" | "compensation_required" | "compensated" | "duplicate";

export type WorkflowPhase =
  "planning" | "parliament" | "jury" | "investigation" | "execution" | "terminal" | "evidence";

export type JsonValue =
  string | number | boolean | null | { [key: string]: JsonValue } | JsonValue[];

export type JsonObject = { [key: string]: JsonValue };

export type WorkflowEvent<TPayload extends JsonObject = JsonObject> = {
  event_id: string;
  planning_run_id: string;
  sequence: number;
  event_type: WorkflowEventType;
  occurred_at: string;
  actor_id?: string | null;
  payload: TPayload;
};

export type SseEnvelope<TPayload extends JsonObject = JsonObject> = {
  id: string;
  event: WorkflowEventType;
  data: WorkflowEvent<TPayload>;
  retry_milliseconds?: number;
};

export type ScenarioSummary = {
  scenario_id: string;
  title: string;
  description: string;
};

export type IntegrityComponents = {
  critical_claim_coverage: number;
  evidence_independence: number;
  provenance_completeness: number;
  evidence_freshness: number;
  canonical_source_diversity: number;
  contradiction_resolution: number;
  dissent_robustness: number;
};

export type JuryGate = {
  gate_code: string;
  passed: boolean;
  reason_codes: string[];
};

export type JuryCycleSnapshot = {
  cycle: number;
  state: JuryState;
  integrity_score: number;
  summary: string;
  reasons: string[];
  components: IntegrityComponents;
  gates: JuryGate[];
  required_investigation: string[];
};

export type ParliamentAgent = {
  agent_id: string;
  label: string;
  objective: string;
  stance: string;
  supplier: string;
  quantity: number;
  evidence_ids: string[];
  source_groups: string[];
  uses_shared_source: boolean;
  agent_echo: boolean;
};

export type AlternativePlan = {
  plan_id: string;
  label: string;
  supplier_mix: string;
  fulfillment: number;
  landed_cost: number;
  waste_risk: number;
  supplier_risk: number;
  max_role_regret: number;
  selected_in_cycle: number | null;
  status: "discarded" | "investigate" | "approved";
};

export type EvidenceGraphNodeData = {
  label: string;
  kind: "source" | "evidence" | "claim" | "agent" | "decision";
  detail: string;
  shared?: boolean;
  cleanRoom?: boolean;
  contradicted?: boolean;
};

export type EvidenceGraphEdgeData = {
  label?: string;
  kind: "retrieved_from" | "supports" | "derived_from" | "used_in" | "contradicts";
  shared?: boolean;
};

export type EvidenceGraphSnapshot = {
  cycle: number;
  nodes: Array<{
    id: string;
    position: { x: number; y: number };
    data: EvidenceGraphNodeData;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    data: EvidenceGraphEdgeData;
  }>;
};

export type ExecutionStep = {
  label: string;
  state: ExecutionState | "approved";
  detail: string;
};

export type ExecutionSnapshot = {
  approved_plan_id: string;
  current_state: ExecutionState | "approved";
  detail: string;
  freshness_ttls: Array<{ label: string; ttl: string; state: "fresh" | "warning" }>;
  steps: ExecutionStep[];
};

export type ScenarioRecord = {
  scenario: ScenarioSummary;
  run_id: string;
  procurement_goal: {
    title: string;
    thesis: string;
    demandWindow: string;
    warehouses: string[];
    suppliers: string[];
  };
  parliament: ParliamentAgent[];
  alternatives: AlternativePlan[];
  jury: JuryCycleSnapshot[];
  evidenceGraphs: EvidenceGraphSnapshot[];
  execution: ExecutionSnapshot;
  events: SseEnvelope[];
};
