import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  Background,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type {
  AuditEventItem,
  AuditEvidenceItem,
  AuditExecutionEventItem,
  AuditManifest,
  AuditPage,
  EvidenceGraphNodeData,
} from "./contracts";

const SIGNED_REFERENCE_PATTERN = /^[A-Za-z0-9_-]{24,128}\.[A-Za-z0-9_-]{40,64}$/;
const PAGE_SIZE = 25;

function readAuditReference(location: Location): string | null {
  const match = /^\/audit\/([^/]+)\/?$/.exec(location.pathname);
  if (!match) return null;
  const reference = decodeURIComponent(match[1]!);
  return SIGNED_REFERENCE_PATTERN.test(reference) ? reference : null;
}

function eventLabel(eventType: string): string {
  return eventType.replaceAll(".", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatClock(isoTimestamp: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(isoTimestamp));
}

function formatDate(isoTimestamp: string): string {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(isoTimestamp));
}

function EvidenceNode({ data }: NodeProps<Node<EvidenceGraphNodeData>>) {
  return (
    <div className={`audit-graph-node audit-graph-node--${data.kind}`}>
      <span>{data.kind}</span>
      <strong>{data.label}</strong>
      <small>{data.detail}</small>
    </div>
  );
}
const nodeTypes: NodeTypes = { evidenceNode: EvidenceNode };

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error("audit_unavailable");
  return (await response.json()) as T;
}

function resourcePath(reference: string, resource: string, cursor?: string | null): string {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (cursor) query.set("cursor", cursor);
  return `/api/audit/${encodeURIComponent(reference)}/${resource}?${query}`;
}

function buildEvidenceGraph(evidence: AuditEvidenceItem[]): {
  nodes: Node<EvidenceGraphNodeData>[];
  edges: Edge[];
} {
  const nodes: Node<EvidenceGraphNodeData>[] = [];
  const edges: Edge[] = [];
  const sourceRows = new Map<string, number>();
  const claimRows = new Map<string, number>();
  const evidenceIds = new Set(evidence.map((item) => item.evidence_id));

  for (const [index, item] of evidence.entries()) {
    const sourceId = `source:${item.source_group}`;
    if (!sourceRows.has(sourceId)) {
      const sourceIndex = sourceRows.size;
      sourceRows.set(sourceId, sourceIndex);
      nodes.push({
        id: sourceId,
        type: "evidenceNode",
        position: { x: 20, y: 28 + sourceIndex * 150 },
        data: { label: item.source_group, kind: "source", detail: item.source_type },
      });
    }
    nodes.push({
      id: item.evidence_id,
      type: "evidenceNode",
      position: { x: 300, y: 28 + index * 150 },
      data: {
        label: item.content_summary,
        kind: "evidence",
        detail: item.origin === "external" ? "Externally observed" : "Agent-derived",
      },
    });
    edges.push({
      id: `${sourceId}:${item.evidence_id}`,
      source: sourceId,
      target: item.evidence_id,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      data: { kind: "retrieved_from" },
    });
    for (const claim of item.claims) {
      if (!claimRows.has(claim.claim_id)) {
        const claimIndex = claimRows.size;
        claimRows.set(claim.claim_id, claimIndex);
        nodes.push({
          id: claim.claim_id,
          type: "evidenceNode",
          position: { x: 590, y: 28 + claimIndex * 150 },
          data: {
            label: claim.human_summary,
            kind: "claim",
            detail: `${claim.predicate} · ${claim.materiality}`,
          },
        });
      }
      edges.push({
        id: `${item.evidence_id}:${claim.claim_id}`,
        source: item.evidence_id,
        target: claim.claim_id,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed },
        data: { kind: "supports" },
      });
    }
    for (const parent of item.derived_from) {
      if (!evidenceIds.has(parent)) continue;
      edges.push({
        id: `${item.evidence_id}:derived:${parent}`,
        source: parent,
        target: item.evidence_id,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed },
        data: { kind: "derived_from" },
      });
    }
  }
  return { nodes, edges };
}

function AuditUnavailable() {
  return (
    <main className="audit-unavailable" aria-labelledby="audit-unavailable-title">
      <p className="eyebrow">Civitas / Read-only audit</p>
      <h1 id="audit-unavailable-title">This audit view is unavailable.</h1>
      <p>The signed link is incomplete, expired, revoked, or no longer available.</p>
    </main>
  );
}

function LoadMore({ label, onLoad }: { label: string; onLoad: () => void }) {
  return (
    <button className="audit-load-more" type="button" onClick={onLoad}>
      {label}
    </button>
  );
}

export function AuditView() {
  const reference = useMemo(() => readAuditReference(window.location), []);
  const [manifest, setManifest] = useState<AuditManifest | null>(null);
  const [events, setEvents] = useState<AuditEventItem[]>([]);
  const [evidence, setEvidence] = useState<AuditEvidenceItem[]>([]);
  const [executionEvents, setExecutionEvents] = useState<AuditExecutionEventItem[]>([]);
  const [eventCursor, setEventCursor] = useState<string | null>(null);
  const [evidenceCursor, setEvidenceCursor] = useState<string | null>(null);
  const [executionCursor, setExecutionCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(reference));
  const [unavailable, setUnavailable] = useState(!reference);

  useEffect(() => {
    if (!reference) return;
    const controller = new AbortController();
    const encoded = encodeURIComponent(reference);
    void Promise.all([
      getJson<AuditManifest>(`/api/audit/${encoded}/manifest`, controller.signal),
      getJson<AuditPage<AuditEventItem>>(resourcePath(reference, "events"), controller.signal),
      getJson<AuditPage<AuditEvidenceItem>>(resourcePath(reference, "evidence"), controller.signal),
      getJson<AuditPage<AuditExecutionEventItem>>(
        resourcePath(reference, "execution"),
        controller.signal,
      ),
    ])
      .then(([nextManifest, eventPage, evidencePage, executionPage]) => {
        setManifest(nextManifest);
        setEvents(eventPage.items);
        setEvidence(evidencePage.items);
        setExecutionEvents(executionPage.items);
        setEventCursor(eventPage.next_cursor ?? null);
        setEvidenceCursor(evidencePage.next_cursor ?? null);
        setExecutionCursor(executionPage.next_cursor ?? null);
      })
      .catch(() => setUnavailable(true))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [reference]);

  const loadPage = async <T,>(
    resource: string,
    cursor: string,
    append: Dispatch<SetStateAction<T[]>>,
    updateCursor: Dispatch<SetStateAction<string | null>>,
  ) => {
    if (!reference) return;
    try {
      const page = await getJson<AuditPage<T>>(resourcePath(reference, resource, cursor));
      append((items) => [...items, ...page.items]);
      updateCursor(page.next_cursor ?? null);
    } catch {
      setUnavailable(true);
    }
  };

  const graph = useMemo(() => buildEvidenceGraph(evidence), [evidence]);
  const jury = manifest?.jury.at(-1) ?? null;

  if (unavailable || (!loading && (!manifest || !jury))) return <AuditUnavailable />;
  if (loading || !manifest || !jury)
    return <main className="audit-unavailable">Loading signed audit snapshot…</main>;

  return (
    <div className="audit-viewer">
      <a className="skip-link" href="#audit-main">
        Skip to audit record
      </a>
      <header className="audit-header">
        <a className="wordmark" href="#audit-main" translate="no" aria-label="Civitas audit record">
          <span className="wordmark__seal" aria-hidden="true">
            C
          </span>
          <span>Civitas</span>
        </a>
        <p>Signed · scoped · read-only</p>
      </header>
      <main id="audit-main" className="audit-main">
        <section className="audit-masthead" aria-labelledby="audit-title">
          <div>
            <p className="eyebrow">
              Procurement decision / Event {manifest.maximum_event_sequence}
            </p>
            <h1 id="audit-title">{manifest.title}</h1>
            <p>{manifest.summary}</p>
          </div>
          <dl className="audit-identifiers">
            <div>
              <dt>Run</dt>
              <dd>{manifest.run_id}</dd>
            </div>
            <div>
              <dt>Selected plan</dt>
              <dd>{manifest.selected_plan_id}</dd>
            </div>
            <div>
              <dt>Policy</dt>
              <dd>{manifest.policy_version}</dd>
            </div>
          </dl>
        </section>
        <aside className="audit-custody" aria-label="Immutable snapshot scope">
          <span>Snapshot seal</span>
          <strong>Captured {formatDate(manifest.captured_at)} UTC</strong>
          <p>Bound to one organization, solver-selected plan, and event ceiling.</p>
          <small>Link expires {formatDate(manifest.link_expires_at)} UTC</small>
        </aside>
        <section className="audit-overview" aria-label="Decision status">
          <div className={`audit-state audit-state--${jury.state}`}>
            <span>Jury state</span>
            <strong>{jury.state}</strong>
          </div>
          <div>
            <span>Decision Integrity</span>
            <strong>{jury.integrity_score}</strong>
            <small>Policy score</small>
          </div>
          <div>
            <span>Evidence records</span>
            <strong>{evidence.length}</strong>
            <small>{evidenceCursor ? "More available" : "Complete page set"}</small>
          </div>
          <div>
            <span>Execution</span>
            <strong>{manifest.execution.current_state.replaceAll("_", " ")}</strong>
            <small>Ledger projection only</small>
          </div>
        </section>
        <section className="audit-section" aria-labelledby="lineage-title">
          <header>
            <div>
              <p className="eyebrow">Evidence chain of custody</p>
              <h2 id="lineage-title">What supports this decision</h2>
            </div>
            {evidenceCursor && (
              <LoadMore
                label="Load more evidence"
                onLoad={() =>
                  void loadPage("evidence", evidenceCursor, setEvidence, setEvidenceCursor)
                }
              />
            )}
          </header>
          <div className="audit-graph" role="img" aria-label="Read-only evidence lineage graph">
            <ReactFlow
              fitView
              nodes={graph.nodes}
              edges={graph.edges}
              nodeTypes={nodeTypes}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              panOnDrag
              zoomOnScroll={false}
              zoomOnPinch
              minZoom={0.45}
              maxZoom={1.5}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="rgba(17, 44, 57, 0.13)" gap={28} />
            </ReactFlow>
          </div>
          <p className="audit-caption">
            Canonical source groups, external observations, typed claims, and derivations are
            projected without raw provider payloads. This viewer cannot approve, refresh, or execute
            a plan.
          </p>
        </section>
        <div className="audit-grid">
          <section className="audit-section" aria-labelledby="integrity-title">
            <header>
              <div>
                <p className="eyebrow">Integrity v1</p>
                <h2 id="integrity-title">Components and hard gates</h2>
              </div>
            </header>
            <dl className="audit-components">
              {Object.entries(jury.components).map(([name, score]) => (
                <div key={name}>
                  <dt>{name.replaceAll("_", " ")}</dt>
                  <dd>{score}</dd>
                </div>
              ))}
            </dl>
            <ul className="audit-gates">
              {jury.gates.map((gate) => (
                <li key={gate.gate_code} className={gate.passed ? "is-passed" : "is-blocked"}>
                  <span>{gate.gate_code.replaceAll("_", " ")}</span>
                  <strong>{gate.passed ? "Pass" : "Blocked"}</strong>
                </li>
              ))}
            </ul>
          </section>
          <section className="audit-section" aria-labelledby="timeline-title">
            <header>
              <div>
                <p className="eyebrow">Persisted workflow</p>
                <h2 id="timeline-title">How the case changed</h2>
              </div>
              {eventCursor && (
                <LoadMore
                  label="Load more events"
                  onLoad={() => void loadPage("events", eventCursor, setEvents, setEventCursor)}
                />
              )}
            </header>
            <ol className="audit-timeline">
              {events.map((event) => (
                <li key={event.event_id}>
                  <time dateTime={event.occurred_at}>{formatClock(event.occurred_at)}</time>
                  <div>
                    <strong>{eventLabel(event.event_type)}</strong>
                    <p>{event.message}</p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>
        <section className="audit-section audit-receipt" aria-labelledby="receipt-title">
          <header>
            <div>
              <p className="eyebrow">Immutable execution ledger</p>
              <h2 id="receipt-title">{manifest.execution.approved_plan_id}</h2>
            </div>
            {executionCursor && (
              <LoadMore
                label="Load more ledger entries"
                onLoad={() =>
                  void loadPage(
                    "execution",
                    executionCursor,
                    setExecutionEvents,
                    setExecutionCursor,
                  )
                }
              />
            )}
          </header>
          <p>{manifest.execution.detail}</p>
          <ol>
            {executionEvents.length ? (
              executionEvents.map((entry) => (
                <li key={entry.sequence}>
                  <strong>Entry {entry.sequence}</strong>
                  <span>{entry.state.replaceAll("_", " ")}</span>
                  <small>{entry.detail ?? entry.reason_code ?? "Recorded state transition."}</small>
                </li>
              ))
            ) : (
              <li>
                <strong>No execution entries</strong>
                <span>Read-only</span>
                <small>No guarded execution existed at snapshot time.</small>
              </li>
            )}
          </ol>
        </section>
      </main>
      <footer className="audit-footer">
        Civitas audit viewer · Immutable snapshot · Execution authority remains with the guarded
        service
      </footer>
    </div>
  );
}
