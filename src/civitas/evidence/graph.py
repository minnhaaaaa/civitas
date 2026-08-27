"""Disposable NetworkX projection of canonical evidence records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import networkx as nx  # type: ignore[import-untyped]

from civitas.contracts.claims import TypedClaim
from civitas.contracts.enums import EvidenceOrigin
from civitas.contracts.evidence import EvidenceRecord
from civitas.evidence.source_identity import canonical_source_group


class NodeKind(StrEnum):
    SOURCE = "source"
    EVIDENCE = "evidence"
    CLAIM = "claim"
    AGENT = "agent"
    PROPOSAL = "proposal"
    DECISION = "decision"
    MCP_CALL = "mcp_call"


class EdgeKind(StrEnum):
    SUPPORTS = "supports"
    DERIVED_FROM = "derived_from"
    RETRIEVED_FROM = "retrieved_from"
    PRODUCED = "produced"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    USED_IN = "used_in"


@dataclass(frozen=True, slots=True)
class LineageEdge:
    source_id: str
    target_id: str
    relationship: EdgeKind
    source_kind: NodeKind
    target_kind: NodeKind


def node_id(kind: NodeKind, identifier: str) -> str:
    return f"{kind.value}:{identifier}"


class EvidenceGraphProjector:
    """Build a read-only graph projection; PostgreSQL records remain authoritative."""

    def project(
        self,
        claims: Iterable[TypedClaim],
        evidence: Iterable[EvidenceRecord],
        lineage_edges: Iterable[LineageEdge] = (),
    ) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()
        claims_by_id = {claim.claim_id: claim for claim in claims}
        evidence_by_id = {record.evidence_id: record for record in evidence}

        for claim in claims_by_id.values():
            graph.add_node(
                node_id(NodeKind.CLAIM, claim.claim_id), kind=NodeKind.CLAIM, record=claim
            )

        for record in evidence_by_id.values():
            evidence_node = node_id(NodeKind.EVIDENCE, record.evidence_id)
            group = canonical_source_group(record.identity)
            source_node = node_id(NodeKind.SOURCE, group.key)
            graph.add_node(
                evidence_node,
                kind=NodeKind.EVIDENCE,
                record=record,
                origin=record.origin,
            )
            graph.add_node(
                source_node,
                kind=NodeKind.SOURCE,
                canonical_group=group,
            )
            graph.add_edge(
                evidence_node,
                source_node,
                relationship=EdgeKind.RETRIEVED_FROM,
            )
            if record.agent_id is not None:
                agent_node = node_id(NodeKind.AGENT, record.agent_id)
                graph.add_node(agent_node, kind=NodeKind.AGENT)
                graph.add_edge(agent_node, evidence_node, relationship=EdgeKind.PRODUCED)
            for claim_id in record.claim_ids:
                claim_node = node_id(NodeKind.CLAIM, claim_id)
                if claim_id not in claims_by_id:
                    graph.add_node(claim_node, kind=NodeKind.CLAIM, missing_record=True)
                graph.add_edge(evidence_node, claim_node, relationship=EdgeKind.SUPPORTS)
            for parent_id in record.derived_from:
                parent_node = node_id(NodeKind.EVIDENCE, parent_id)
                if parent_id not in evidence_by_id:
                    graph.add_node(parent_node, kind=NodeKind.EVIDENCE, missing_record=True)
                graph.add_edge(evidence_node, parent_node, relationship=EdgeKind.DERIVED_FROM)

        for edge in lineage_edges:
            source = node_id(edge.source_kind, edge.source_id)
            target = node_id(edge.target_kind, edge.target_id)
            if source not in graph:
                graph.add_node(source, kind=edge.source_kind, missing_record=True)
            if target not in graph:
                graph.add_node(target, kind=edge.target_kind, missing_record=True)
            graph.add_edge(source, target, relationship=edge.relationship)
        return graph


class LineageAnalyzer:
    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self._graph = graph

    def dependencies(self, evidence_id: str) -> frozenset[str]:
        """Return transitive parent evidence IDs, safely terminating on cycles."""

        start = node_id(NodeKind.EVIDENCE, evidence_id)
        found: set[str] = set()
        pending = [start]
        visited = {start}
        while pending:
            current = pending.pop()
            for _, target, data in self._graph.out_edges(current, data=True):
                if data.get("relationship") not in {EdgeKind.DERIVED_FROM, EdgeKind.DEPENDS_ON}:
                    continue
                if target in visited:
                    continue
                visited.add(target)
                pending.append(target)
                if self._graph.nodes[target].get("kind") == NodeKind.EVIDENCE:
                    found.add(target.removeprefix(f"{NodeKind.EVIDENCE.value}:"))
        return frozenset(found)

    def effective_source_groups(self, evidence_id: str) -> frozenset[str]:
        """Resolve external ancestors; agent echoes contribute no new source group."""

        start = node_id(NodeKind.EVIDENCE, evidence_id)
        groups: set[str] = set()
        pending = [start]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited or current not in self._graph:
                continue
            visited.add(current)
            attrs = self._graph.nodes[current]
            record = attrs.get("record")
            if isinstance(record, EvidenceRecord) and record.origin == EvidenceOrigin.EXTERNAL:
                for _, target, data in self._graph.out_edges(current, data=True):
                    if data.get("relationship") == EdgeKind.RETRIEVED_FROM:
                        group = self._graph.nodes[target].get("canonical_group")
                        if group is not None:
                            groups.add(group.key)
                # An externally observed record's derivation describes transformations, not
                # an agent echo; its canonical upstream identity remains authoritative.
                continue
            for _, target, data in self._graph.out_edges(current, data=True):
                if data.get("relationship") in {EdgeKind.DERIVED_FROM, EdgeKind.DEPENDS_ON}:
                    pending.append(target)
        return frozenset(groups)

    def claim_source_groups(self, claim_id: str) -> frozenset[str]:
        claim_node = node_id(NodeKind.CLAIM, claim_id)
        groups: set[str] = set()
        if claim_node not in self._graph:
            return frozenset()
        for source, _, data in self._graph.in_edges(claim_node, data=True):
            if data.get("relationship") != EdgeKind.SUPPORTS:
                continue
            evidence_id = source.removeprefix(f"{NodeKind.EVIDENCE.value}:")
            groups.update(self.effective_source_groups(evidence_id))
        return frozenset(groups)

    def has_complete_lineage(self, evidence_id: str) -> bool:
        evidence_node = node_id(NodeKind.EVIDENCE, evidence_id)
        if evidence_node not in self._graph:
            return False
        attrs = self._graph.nodes[evidence_node]
        record = attrs.get("record")
        if not isinstance(record, EvidenceRecord):
            return False
        if record.origin == EvidenceOrigin.AGENT_DERIVED and not record.derived_from:
            return False
        return all(
            node_id(NodeKind.EVIDENCE, parent) in self._graph
            and not self._graph.nodes[node_id(NodeKind.EVIDENCE, parent)].get(
                "missing_record", False
            )
            for parent in record.derived_from
        )
