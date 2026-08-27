"""Evidence lineage, contradiction, Dissent, and Jury services."""

from civitas.evidence.claims import normalize_claim, normalized_claim_key, normalized_claim_value
from civitas.evidence.contradictions import (
    Contradiction,
    ContradictionSeverity,
    detect_contradictions,
)
from civitas.evidence.dissent import (
    DissentInvestigationPlan,
    DissentPhase,
    DissentProtocol,
    DissentReport,
)
from civitas.evidence.graph import (
    EdgeKind,
    EvidenceGraphProjector,
    LineageAnalyzer,
    LineageEdge,
    NodeKind,
)
from civitas.evidence.jury import (
    DecisionIntegrityCalculator,
    GateFacts,
    IntegrityCalculation,
    IntegrityPolicyV1,
    JuryEvaluator,
    JuryInputs,
    ReasonCode,
    required_investigation,
)
from civitas.evidence.source_identity import (
    CanonicalSourceGroup,
    canonical_source_group,
    evidence_identity_fingerprint,
)

__all__ = [
    "CanonicalSourceGroup",
    "Contradiction",
    "ContradictionSeverity",
    "DecisionIntegrityCalculator",
    "DissentInvestigationPlan",
    "DissentPhase",
    "DissentProtocol",
    "DissentReport",
    "EdgeKind",
    "EvidenceGraphProjector",
    "GateFacts",
    "IntegrityCalculation",
    "IntegrityPolicyV1",
    "JuryEvaluator",
    "JuryInputs",
    "LineageAnalyzer",
    "LineageEdge",
    "NodeKind",
    "ReasonCode",
    "canonical_source_group",
    "detect_contradictions",
    "evidence_identity_fingerprint",
    "normalize_claim",
    "normalized_claim_key",
    "normalized_claim_value",
    "required_investigation",
]
