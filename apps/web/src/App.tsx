import { useEffect, useMemo, useState } from "react";
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
  AuditViewSnapshot,
  EvidenceGraphNodeData,
  JuryCycleSnapshot,
  WorkflowEvent,
} from "./contracts";
import { createMockAuditSnapshot } from "./mockPlayback";

const PUBLIC_REFERENCE_PATTERN = /^[A-Za-z0-9_-]{12,128}$/;
type AuditRoute = { publicReference: string; runId: string; planId: string; cursor: number };

function readAuditRoute(location: Location): AuditRoute | null {
  const match = /^\/audit\/([^/]+)\/?$/.exec(location.pathname);
  if (!match || !PUBLIC_REFERENCE_PATTERN.test(match[1]!)) return null;
  const query = new URLSearchParams(location.search);
  const runId = query.get("run");
  const planId = query.get("plan");
  const cursor = Number(query.get("cursor") ?? "0");
  if (!runId || !planId || !Number.isSafeInteger(cursor) || cursor < 0) return null;
  return { publicReference: match[1]!, runId, planId, cursor };
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

function snapshotForRoute(route: AuditRoute): AuditViewSnapshot | null {
  // Offline playback intentionally supports only its documented opaque reference.
  if (route.publicReference !== "demo_false_consensus") return null;
  const snapshot = createMockAuditSnapshot();
  const maximumCursor = Math.max(...snapshot.events.map((event) => event.sequence));
  return snapshot.run_id === route.runId &&
    snapshot.selected_plan_id === route.planId &&
    route.cursor <= maximumCursor
    ? snapshot
    : null;
}

async function fetchAuditSnapshot(
  route: AuditRoute,
  signal: AbortSignal,
): Promise<AuditViewSnapshot> {
  const response = await fetch(
    `/api/audit/${encodeURIComponent(route.publicReference)}?cursor=${route.cursor}`,
    { credentials: "same-origin", signal },
  );
  // Collapse authorization and lookup failures to avoid revealing protected record existence.
  if (!response.ok) throw new Error("audit_unavailable");
  const snapshot = (await response.json()) as AuditViewSnapshot;
  const maximumCursor = Math.max(...snapshot.events.map((event) => event.sequence));
  if (
    snapshot.run_id !== route.runId ||
    snapshot.selected_plan_id !== route.planId ||
    route.cursor > maximumCursor
  )
    throw new Error("audit_unavailable");
  return snapshot;
}

function AuditUnavailable() {
  return (
    <main className="audit-unavailable" aria-labelledby="audit-unavailable-title">
      <p className="eyebrow">Civitas / Read-only audit</p>
      <h1 id="audit-unavailable-title">This audit view is unavailable.</h1>
      <p>
        Check that you opened the complete audit link while signed in to the organization that owns
        the decision.
      </p>
    </main>
  );
}

export function App() {
  const route = useMemo(() => readAuditRoute(window.location), []);
  const mockSnapshot = route ? snapshotForRoute(route) : null;
  const [snapshot, setSnapshot] = useState<AuditViewSnapshot | null>(mockSnapshot);
  const [loading, setLoading] = useState(Boolean(route && !mockSnapshot));

  useEffect(() => {
    if (!route || mockSnapshot) return;
    const controller = new AbortController();
    void fetchAuditSnapshot(route, controller.signal)
      .then(setSnapshot)
      .catch(() => setSnapshot(null))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [mockSnapshot, route]);

  const visibleEvents = useMemo(
    () =>
      snapshot && route ? snapshot.events.filter((event) => event.sequence <= route.cursor) : [],
    [route, snapshot],
  );
  const currentCycle = useMemo(
    () => Math.max(1, ...visibleEvents.map((event) => Number(event.payload.cycle) || 1)),
    [visibleEvents],
  );
  const jury = useMemo<JuryCycleSnapshot | null>(
    () =>
      snapshot?.jury.find((item) => item.cycle === currentCycle) ?? snapshot?.jury.at(-1) ?? null,
    [currentCycle, snapshot],
  );
  const graph = useMemo(
    () =>
      snapshot?.evidence_graphs.find((item) => item.cycle === currentCycle) ??
      snapshot?.evidence_graphs.at(-1),
    [currentCycle, snapshot],
  );
  const graphNodes = useMemo<Node<EvidenceGraphNodeData>[]>(
    () => (graph?.nodes ?? []).map((node) => ({ ...node, type: "evidenceNode" })),
    [graph],
  );
  const graphEdges = useMemo<Edge[]>(
    () =>
      (graph?.edges ?? []).map((edge) => ({
        ...edge,
        type: "smoothstep",
        animated: edge.data.kind === "contradicts",
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [graph],
  );

  if (!route || (!loading && !snapshot) || !jury) return <AuditUnavailable />;
  if (loading || !snapshot)
    return <main className="audit-unavailable">Loading read-only audit…</main>;

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
        <p>Read-only decision record</p>
      </header>
      <main id="audit-main" className="audit-main">
        <section className="audit-masthead" aria-labelledby="audit-title">
          <div>
            <p className="eyebrow">Procurement decision / Event {route.cursor}</p>
            <h1 id="audit-title">{snapshot.title}</h1>
            <p>{snapshot.summary}</p>
          </div>
          <dl className="audit-identifiers">
            <div>
              <dt>Run</dt>
              <dd>{snapshot.run_id}</dd>
            </div>
            <div>
              <dt>Selected plan</dt>
              <dd>{snapshot.selected_plan_id}</dd>
            </div>
            <div>
              <dt>Public record</dt>
              <dd>{route.publicReference}</dd>
            </div>
          </dl>
        </section>
        <section className="audit-overview" aria-label="Decision status">
          <div className={`audit-state audit-state--${jury.state}`}>
            <span>Jury state</span>
            <strong>{jury.state}</strong>
          </div>
          <div>
            <span>Decision Integrity</span>
            <strong>{jury.integrity_score}</strong>
            <small>Policy {snapshot.policy_version}</small>
          </div>
          <div>
            <span>Event cursor</span>
            <strong>{route.cursor}</strong>
            <small>{visibleEvents.length} visible events</small>
          </div>
          <div>
            <span>Execution</span>
            <strong>{snapshot.execution.current_state.replaceAll("_", " ")}</strong>
            <small>Receipt only</small>
          </div>
        </section>
        <section className="audit-section" aria-labelledby="lineage-title">
          <header>
            <p className="eyebrow">Evidence lineage / Cycle {currentCycle}</p>
            <h2 id="lineage-title">What supports this decision</h2>
          </header>
          <div className="audit-graph" role="img" aria-label="Read-only evidence lineage graph">
            <ReactFlow
              fitView
              nodes={graphNodes}
              edges={graphEdges}
              nodeTypes={nodeTypes}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              panOnDrag
              zoomOnScroll={false}
              zoomOnPinch
              minZoom={0.55}
              maxZoom={1.5}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="rgba(33, 55, 44, 0.12)" gap={28} />
            </ReactFlow>
          </div>
          <p className="audit-caption">
            Source groups and contradictions are shown for inspection only. This viewer cannot
            approve, refresh, or execute a plan.
          </p>
        </section>
        <div className="audit-grid">
          <section className="audit-section" aria-labelledby="integrity-title">
            <header>
              <p className="eyebrow">Integrity v1</p>
              <h2 id="integrity-title">Components and hard gates</h2>
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
                  <span>{gate.gate_code.replaceAll("-", " ")}</span>
                  <strong>{gate.passed ? "Pass" : "Blocked"}</strong>
                </li>
              ))}
            </ul>
          </section>
          <section className="audit-section" aria-labelledby="timeline-title">
            <header>
              <p className="eyebrow">Replanning timeline</p>
              <h2 id="timeline-title">How the case changed</h2>
            </header>
            <ol className="audit-timeline">
              {visibleEvents.map((event: WorkflowEvent) => (
                <li key={event.event_id}>
                  <time dateTime={event.occurred_at}>{formatClock(event.occurred_at)}</time>
                  <div>
                    <strong>{eventLabel(event.event_type)}</strong>
                    <p>
                      {String(
                        event.payload.note ??
                          event.payload.detail ??
                          "Recorded workflow transition.",
                      )}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>
        <section className="audit-section audit-receipt" aria-labelledby="receipt-title">
          <header>
            <p className="eyebrow">Immutable execution receipt</p>
            <h2 id="receipt-title">{snapshot.execution.approved_plan_id}</h2>
          </header>
          <p>{snapshot.execution.detail}</p>
          <ol>
            {snapshot.execution.steps.map((step) => (
              <li key={step.label}>
                <strong>{step.label}</strong>
                <span>{step.state.replaceAll("_", " ")}</span>
                <small>{step.detail}</small>
              </li>
            ))}
          </ol>
        </section>
      </main>
      <footer className="audit-footer">
        Civitas audit viewer · Read-only · Execution authority remains with the guarded service
      </footer>
    </div>
  );
}
