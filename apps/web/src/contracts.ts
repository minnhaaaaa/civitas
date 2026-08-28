export type JuryState = "approve" | "investigate" | "escalate" | "reject";

export type EvidenceGraphNodeData = {
  label: string;
  kind: "source" | "evidence" | "claim";
  detail: string;
};

export type AuditJuryGate = {
  gate_code: string;
  passed: boolean;
  reason_codes: string[];
};

export type AuditJuryView = {
  cycle: number;
  state: JuryState;
  integrity_score: number;
  components: Record<string, number>;
  gates: AuditJuryGate[];
  reason_codes: string[];
};

export type AuditExecutionSummary = {
  approved_plan_id: string;
  current_state: string;
  detail: string;
  event_count: number;
};

export type AuditManifest = {
  run_id: string;
  selected_plan_id: string;
  policy_version: string;
  title: string;
  summary: string;
  captured_at: string;
  link_expires_at: string;
  maximum_event_sequence: number;
  jury: AuditJuryView[];
  execution: AuditExecutionSummary;
};

export type AuditEventItem = {
  event_id: string;
  sequence: number;
  event_type: string;
  occurred_at: string;
  phase?: string | null;
  message: string;
  reason_codes: string[];
};

export type AuditClaimReference = {
  claim_id: string;
  human_summary: string;
  predicate: string;
  materiality: string;
};

export type AuditEvidenceItem = {
  evidence_id: string;
  content_summary: string;
  origin: "external" | "agent_derived";
  source_group: string;
  source_type: string;
  retrieved_at: string;
  observation_version?: string | null;
  claims: AuditClaimReference[];
  derived_from: string[];
};

export type AuditExecutionEventItem = {
  sequence: number;
  occurred_at: string;
  state: string;
  reason_code?: string | null;
  detail?: string | null;
};

export type AuditPage<T> = {
  items: T[];
  next_cursor?: string | null;
};
