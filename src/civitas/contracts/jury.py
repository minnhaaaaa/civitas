"""Deterministic Decision Integrity and Jury contracts."""

from datetime import datetime

from pydantic import Field

from civitas.contracts.common import Contract
from civitas.contracts.enums import JuryState
from civitas.contracts.optimization import CandidatePlan


class IntegrityComponents(Contract):
    critical_claim_coverage: float = Field(ge=0, le=100)
    evidence_independence: float = Field(ge=0, le=100)
    provenance_completeness: float = Field(ge=0, le=100)
    evidence_freshness: float = Field(ge=0, le=100)
    canonical_source_diversity: float = Field(ge=0, le=100)
    contradiction_resolution: float = Field(ge=0, le=100)
    dissent_robustness: float = Field(ge=0, le=100)


class JuryGateResult(Contract):
    gate_code: str
    passed: bool
    reason_codes: tuple[str, ...] = ()


class JuryRequest(Contract):
    planning_run_id: str
    candidate_plan: CandidatePlan
    supporting_claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    policy_version: str
    autonomy_budget_exhausted: bool = False
    require_critical_external_support: bool = False


class JuryEvaluation(Contract):
    evaluation_id: str
    planning_run_id: str
    plan_id: str
    policy_version: str
    implementation_version: str
    calculated_at: datetime
    components: IntegrityComponents
    integrity_score: float = Field(ge=0, le=100)
    gates: tuple[JuryGateResult, ...]
    state: JuryState
    reason_codes: tuple[str, ...]
    required_investigation: tuple[str, ...] = ()
