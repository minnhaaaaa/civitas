import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

import "@xyflow/react/dist/style.css";

import type {
  AlternativePlan,
  EvidenceGraphNodeData,
  EvidenceGraphSnapshot,
  ExecutionStep,
  IntegrityComponents,
  JuryCycleSnapshot,
  ScenarioSummary,
  WorkflowEvent,
} from "./contracts";
import { createMockRunStream, getPlaybackCycle, scenarioRecord } from "./mockPlayback";

gsap.registerPlugin(ScrollTrigger, useGSAP);

function labelForEvent(eventType: string): string {
  return eventType.replaceAll(".", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatClock(isoTimestamp: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(isoTimestamp));
}

function graphForCycle(cycle: number): EvidenceGraphSnapshot {
  return (
    scenarioRecord.evidenceGraphs.find((graph) => graph.cycle === cycle) ??
    scenarioRecord.evidenceGraphs[0]!
  );
}

function juryForCycle(cycle: number): JuryCycleSnapshot {
  return scenarioRecord.jury.find((item) => item.cycle === cycle) ?? scenarioRecord.jury[0]!;
}

function executionProgress(events: WorkflowEvent[]) {
  const executionEvents = events.filter((event) => event.event_type === "execution.updated");
  if (executionEvents.length === 0) {
    return 1;
  }
  return Math.min(4, executionEvents.length + 2);
}

function alternativeState(plan: AlternativePlan, cycle: number) {
  if (plan.selected_in_cycle === null) {
    return plan.status;
  }
  if (cycle < plan.selected_in_cycle) {
    return "standby";
  }
  return plan.status;
}

function componentEntries(components: IntegrityComponents) {
  return [
    { key: "critical_claim_coverage", label: "Coverage", value: components.critical_claim_coverage },
    { key: "evidence_independence", label: "Independence", value: components.evidence_independence },
    { key: "provenance_completeness", label: "Provenance", value: components.provenance_completeness },
    { key: "evidence_freshness", label: "Freshness", value: components.evidence_freshness },
    { key: "canonical_source_diversity", label: "Source diversity", value: components.canonical_source_diversity },
    { key: "contradiction_resolution", label: "Contradictions", value: components.contradiction_resolution },
    { key: "dissent_robustness", label: "Dissent", value: components.dissent_robustness },
  ];
}

function nodeClassName(node: Node<EvidenceGraphNodeData>) {
  const classes = ["graph-node", `graph-node-${node.data.kind}`];
  if (node.data.shared) {
    classes.push("is-shared");
  }
  if (node.data.cleanRoom) {
    classes.push("is-clean-room");
  }
  if (node.data.contradicted) {
    classes.push("is-contradicted");
  }
  return classes.join(" ");
}

function EvidenceNode({ data }: NodeProps<Node<EvidenceGraphNodeData>>) {
  return (
    <div>
      <p className="graph-node-label">{data.label}</p>
      <p className="graph-node-detail">{data.detail}</p>
    </div>
  );
}

const nodeTypes: NodeTypes = {
  evidenceNode: EvidenceNode,
};

function statusTone(value: string) {
  if (value === "approve" || value === "approved" || value === "succeeded" || value === "fresh") {
    return "is-positive";
  }
  if (value === "investigate" || value === "warning" || value === "standby") {
    return "is-warning";
  }
  if (value === "duplicate") {
    return "is-neutral";
  }
  return "is-critical";
}

function ExecutionRail({ steps, progress }: { steps: ExecutionStep[]; progress: number }) {
  return (
    <ol className="execution-rail">
      {steps.map((step, index) => {
        const active = index < progress;
        return (
          <li key={step.label} className={`execution-step ${active ? "is-active" : ""}`}>
            <div className="execution-step-index">{String(index + 1).padStart(2, "0")}</div>
            <div>
              <div className="execution-step-head">
                <p>{step.label}</p>
                <span className={`tone-chip ${statusTone(step.state)}`}>{step.state.replaceAll("_", " ")}</span>
              </div>
              <p className="muted-copy">{step.detail}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

type DemoScenarioResponse = {
  scenarios: ScenarioSummary[];
};

type DemoRunResponse = {
  run_id: string;
  scenario_id: string;
  status: string;
  stream_url: string;
};

type EventSourceLike = {
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: ((event: { data: WorkflowEvent }) => void) | EventListenerOrEventListenerObject): void;
  close(): void;
};

export function App() {
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackMode, setPlaybackMode] = useState<"live" | "mock">("live");
  const [availableScenarios, setAvailableScenarios] = useState<ScenarioSummary[]>([
    scenarioRecord.scenario,
  ]);
  const [selectedScenarioId, setSelectedScenarioId] = useState(scenarioRecord.scenario.scenario_id);
  const [streamError, setStreamError] = useState<string | null>(null);
  const rootRef = useRef<HTMLElement | null>(null);
  const streamRef = useRef<EventSourceLike | null>(null);

  const currentCycle = useMemo(() => getPlaybackCycle(events), [events]);
  const currentJury = useMemo(() => juryForCycle(currentCycle), [currentCycle]);
  const currentGraph = useMemo(() => graphForCycle(currentCycle), [currentCycle]);
  const visibleEventIds = useMemo(() => new Set(events.map((event) => event.payload.evidence_id).filter(Boolean)), [events]);
  const executionStepProgress = useMemo(() => executionProgress(events), [events]);
  const latestEvent = events.at(-1) ?? null;
  const hasStarted = events.length > 0;
  const selectedScenario =
    availableScenarios.find((scenario) => scenario.scenario_id === selectedScenarioId) ??
    scenarioRecord.scenario;

  const graphNodes = useMemo<Node<EvidenceGraphNodeData>[]>(
    () =>
      currentGraph.nodes.map((node) => ({
        ...node,
        type: "evidenceNode",
        className: nodeClassName(node),
      })),
    [currentGraph],
  );

  const graphEdges = useMemo<Edge[]>(
    () =>
      currentGraph.edges.map((edge) => ({
        ...edge,
        type: "smoothstep",
        animated: edge.data.kind === "contradicts",
        markerEnd: { type: MarkerType.ArrowClosed },
        className: `graph-edge graph-edge-${edge.data.kind} ${edge.data.shared ? "is-shared" : ""}`,
      })),
    [currentGraph],
  );

  useEffect(() => () => streamRef.current?.close(), []);

  useEffect(() => {
    let cancelled = false;

    async function loadScenarios() {
      try {
        const response = await fetch("/api/demo-scenarios");
        if (!response.ok) {
          throw new Error(`Scenario request failed with ${response.status}`);
        }
        const payload = (await response.json()) as DemoScenarioResponse;
        if (!cancelled && payload.scenarios.length > 0) {
          setAvailableScenarios(payload.scenarios);
          setSelectedScenarioId(payload.scenarios[0]!.scenario_id);
          setPlaybackMode("live");
        }
      } catch {
        if (!cancelled) {
          setPlaybackMode("mock");
        }
      }
    }

    void loadScenarios();
    return () => {
      cancelled = true;
    };
  }, []);

  function attachStream(source: EventSourceLike) {
    streamRef.current = source;

    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as WorkflowEvent;
      startTransition(() => {
        setEvents((current) => {
          if (current.some((item) => item.sequence === event.sequence)) {
            return current;
          }
          return [...current, event];
        });
      });
    };

    source.addEventListener("run.completed", () => {
      setIsPlaying(false);
      source.close();
    });

    source.onerror = () => {
      setStreamError("The event stream terminated unexpectedly.");
      setIsPlaying(false);
      source.close();
    };
  }

  async function startPlayback() {
    streamRef.current?.close();
    setStreamError(null);
    setEvents([]);
    setIsPlaying(true);

    if (playbackMode === "live") {
      try {
        const response = await fetch("/api/demo-runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scenario_id: selectedScenarioId }),
        });
        if (!response.ok) {
          throw new Error(`Run creation failed with ${response.status}`);
        }
        const payload = (await response.json()) as DemoRunResponse;
        attachStream(new EventSource(payload.stream_url));
        return;
      } catch {
        setPlaybackMode("mock");
      }
    }

    const { source } = createMockRunStream(850);
    attachStream(source);
    source.play();
  }

  useGSAP(
    () => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduceMotion) {
        return;
      }

      gsap.from(".hero-title-line", {
        yPercent: 110,
        opacity: 0,
        duration: 1,
        stagger: 0.12,
        ease: "power4.out",
      });

      gsap.from(".hero-stamp", {
        rotate: -12,
        scale: 0.85,
        opacity: 0,
        duration: 0.8,
        delay: 0.35,
        ease: "power3.out",
      });

      gsap.to(".orbital-ring", {
        rotate: 360,
        repeat: -1,
        duration: 24,
        ease: "none",
        transformOrigin: "50% 50%",
      });

      gsap.utils.toArray<HTMLElement>(".reveal-panel").forEach((panel, index) => {
        gsap.from(panel, {
          y: 56,
          opacity: 0,
          duration: 0.9,
          ease: "power3.out",
          scrollTrigger: {
            trigger: panel,
            start: "top 86%",
            once: true,
            id: `panel-${index}`,
          },
        });
      });

      gsap.to(".hero-visual-track", {
        yPercent: -18,
        ease: "none",
        scrollTrigger: {
          trigger: ".hero-stage",
          start: "top top",
          end: "bottom top",
          scrub: 0.8,
          id: "hero-parallax",
        },
      });

      gsap.utils.toArray<HTMLElement>(".metric-fill").forEach((bar, index) => {
        const value = Number(bar.dataset.value ?? "0");
        gsap.fromTo(
          bar,
          { scaleX: 0 },
          {
            scaleX: value / 100,
            duration: 1,
            ease: "power2.out",
            scrollTrigger: {
              trigger: bar,
              start: "top 92%",
              once: true,
              id: `metric-${index}`,
            },
          },
        );
      });
    },
    { scope: rootRef },
  );

  return (
    <main className="page-shell" ref={rootRef}>
      <section className="hero-stage">
        <div className="ambient-grid" aria-hidden="true" />
        <div className="hero-layout">
          <div className="hero-copy">
            <p className="utility-kicker">
              Agent 9 · End-to-End Integration ·{" "}
              {playbackMode === "live" ? "FastAPI/SSE" : "Mock fallback"}
            </p>
            <div className="hero-title-lockup" aria-labelledby="page-title">
              <span className="hero-title-line">CIVITAS</span>
              <span className="hero-title-line hero-title-offset">JURY</span>
              <span className="hero-script">for autonomous food procurement</span>
              <span className="hero-stamp">AGENT</span>
            </div>
            <h1 id="page-title">{scenarioRecord.procurement_goal.thesis}</h1>
            <p className="hero-summary">
              Recorded workflow events drive the entire interface. Parliament, solver alternatives,
              Jury integrity, evidence lineage, replanning, and execution status all update from the
              same typed mock stream.
            </p>
          </div>

          <div className="hero-visual-track">
            <div className="hero-command-board">
              <div className="orbital-ring">
                <span>Problem statement five · evidence lineage · clean-room dissent · </span>
              </div>
              <div className="hero-goal-card">
                <p className="panel-kicker">Procurement Goal</p>
                <h2>{scenarioRecord.procurement_goal.title}</h2>
                <p>{scenarioRecord.procurement_goal.demandWindow}</p>
                <dl>
                  <div>
                    <dt>Warehouses</dt>
                    <dd>{scenarioRecord.procurement_goal.warehouses.join(", ")}</dd>
                  </div>
                  <div>
                    <dt>Suppliers</dt>
                    <dd>{scenarioRecord.procurement_goal.suppliers.join(", ")}</dd>
                  </div>
                </dl>
              </div>

              <div className="hero-run-card">
                <div className="hero-run-head">
                  <div>
                    <p className="panel-kicker">Run Control</p>
                    <h3>{selectedScenario.title}</h3>
                  </div>
                  <span className={`tone-chip ${hasStarted ? statusTone(currentJury.state) : "is-neutral"}`}>
                    {hasStarted ? currentJury.state : "idle"}
                  </span>
                </div>
                <p className="muted-copy">{selectedScenario.description}</p>
                <div className="hero-actions">
                  <label className="visually-hidden" htmlFor="scenario-select">
                    Scenario
                  </label>
                  <select
                    id="scenario-select"
                    className="scenario-select"
                    value={selectedScenarioId}
                    onChange={(event) => setSelectedScenarioId(event.target.value)}
                    disabled={isPlaying}
                  >
                    {availableScenarios.map((scenario) => (
                      <option key={scenario.scenario_id} value={scenario.scenario_id}>
                        {scenario.title}
                      </option>
                    ))}
                  </select>
                  <button type="button" className="launch-button" onClick={() => void startPlayback()} disabled={isPlaying}>
                    {isPlaying
                      ? playbackMode === "live"
                        ? "Streaming live run"
                        : "Streaming recorded run"
                      : hasStarted
                        ? playbackMode === "live"
                          ? "Run again"
                          : "Replay recorded run"
                        : playbackMode === "live"
                          ? "Start live run"
                          : "Start recorded run"}
                  </button>
                  <div className="playback-meta">
                    <span className="tone-chip is-neutral">{playbackMode}</span>
                    <span className="tone-chip is-neutral">{events.length}/{scenarioRecord.events.length} events</span>
                  </div>
                </div>
                {streamError ? <p className="message-critical">{streamError}</p> : null}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="reveal-panel status-ribbon" aria-label="Run status">
        <article>
          <p className="panel-kicker">Current cycle</p>
          <strong>{hasStarted ? currentCycle : 0}</strong>
        </article>
        <article>
          <p className="panel-kicker">Latest event</p>
          <strong>{latestEvent ? labelForEvent(latestEvent.event_type) : "Awaiting playback"}</strong>
        </article>
        <article>
          <p className="panel-kicker">Integrity score</p>
          <strong>{hasStarted ? currentJury.integrity_score : 0}</strong>
        </article>
        <article>
          <p className="panel-kicker">Execution state</p>
          <strong>{hasStarted ? scenarioRecord.execution.current_state.replaceAll("_", " ") : "pending"}</strong>
        </article>
      </section>

      <section className="section-grid reveal-panel">
        <article className="surface-card">
          <div className="section-heading">
            <div>
              <p className="utility-kicker">Live Parliament</p>
              <h2>Consensus is visible. Dependency is visible too.</h2>
            </div>
            <p className="muted-copy">Shared-source reliance is marked in sand. Agent echoes are flagged separately.</p>
          </div>
          <div className="agent-grid">
            {scenarioRecord.parliament.map((agent) => (
              <article key={agent.agent_id} className={`agent-card ${agent.uses_shared_source ? "is-shared" : ""}`}>
                <div className="agent-head">
                  <div>
                    <p className="panel-kicker">{agent.label}</p>
                    <h3>{agent.supplier}</h3>
                  </div>
                  <span className={`tone-chip ${agent.uses_shared_source ? "is-warning" : "is-positive"}`}>
                    {agent.quantity} units
                  </span>
                </div>
                <p>{agent.stance}</p>
                <p className="muted-copy">{agent.objective}</p>
                <div className="agent-tags">
                  {agent.uses_shared_source ? <span className="micro-tag is-shared">shared source</span> : null}
                  {agent.agent_echo ? <span className="micro-tag is-echo">agent echo</span> : null}
                  <span className="micro-tag">{agent.evidence_ids.length} evidence links</span>
                </div>
              </article>
            ))}
          </div>
        </article>

        <article className="surface-card">
          <div className="section-heading">
            <div>
              <p className="utility-kicker">Solver Alternatives</p>
              <h2>Parliament compares validated plans, not invented quantities.</h2>
            </div>
          </div>
          <div className="alternative-list">
            {scenarioRecord.alternatives.map((plan) => {
              const state = alternativeState(plan, currentCycle);
              return (
                <article key={plan.plan_id} className={`alternative-card is-${state}`}>
                  <div className="alternative-head">
                    <div>
                      <p className="panel-kicker">{plan.label}</p>
                      <h3>{plan.supplier_mix}</h3>
                    </div>
                    <span className={`tone-chip ${statusTone(state)}`}>{state.replaceAll("_", " ")}</span>
                  </div>
                  <div className="mini-metrics">
                    <div>
                      <span>Fulfillment</span>
                      <strong>{plan.fulfillment}%</strong>
                    </div>
                    <div>
                      <span>Landed cost</span>
                      <strong>${plan.landed_cost}</strong>
                    </div>
                    <div>
                      <span>Supplier risk</span>
                      <strong>{plan.supplier_risk}</strong>
                    </div>
                    <div>
                      <span>Max regret</span>
                      <strong>{plan.max_role_regret}</strong>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </article>
      </section>

      <section className="section-grid reveal-panel">
        <article className="surface-card">
          <div className="section-heading">
            <div>
              <p className="utility-kicker">Jury Integrity</p>
              <h2>Components and gates are separate on purpose.</h2>
            </div>
            <div className="jury-score">
              <strong>{currentJury.integrity_score}</strong>
              <span className={`tone-chip ${statusTone(currentJury.state)}`}>{currentJury.state}</span>
            </div>
          </div>
          <p className="muted-copy">{currentJury.summary}</p>
          <div className="integrity-grid">
            {componentEntries(currentJury.components).map((component) => (
              <article key={component.key} className="metric-card">
                <div className="metric-head">
                  <span>{component.label}</span>
                  <strong>{component.value}</strong>
                </div>
                <div className="metric-bar">
                  <span className="metric-fill" data-value={component.value} />
                </div>
              </article>
            ))}
          </div>
        </article>

        <article className="surface-card">
          <div className="section-heading">
            <div>
              <p className="utility-kicker">Hard Gates</p>
              <h2>Failed gates stop execution even when scores rise.</h2>
            </div>
          </div>
          <div className="gate-list">
            {currentJury.gates.map((gate) => (
              <article key={gate.gate_code} className={`gate-card ${gate.passed ? "is-passed" : "is-failed"}`}>
                <div className="gate-head">
                  <h3>{gate.gate_code.replaceAll("-", " ")}</h3>
                  <span className={`tone-chip ${gate.passed ? "is-positive" : "is-critical"}`}>
                    {gate.passed ? "passed" : "failed"}
                  </span>
                </div>
                <p className="muted-copy">
                  {gate.reason_codes.length > 0 ? gate.reason_codes.join(" · ") : "No blocking reason codes."}
                </p>
              </article>
            ))}
          </div>
          <div className="investigation-card">
            <p className="panel-kicker">Required investigation</p>
            {currentJury.required_investigation.length > 0 ? (
              currentJury.required_investigation.map((item) => <p key={item}>{item}</p>)
            ) : (
              <p>No additional investigation is required in the current cycle.</p>
            )}
          </div>
        </article>
      </section>

      <section className="surface-card reveal-panel">
        <div className="section-heading">
          <div>
            <p className="utility-kicker">Evidence Graph</p>
            <h2>Shared source groups and clean-room dissent are structurally distinct.</h2>
          </div>
          <div className="graph-legend">
            <span className="micro-tag is-shared">shared support</span>
            <span className="micro-tag is-clean-room">clean room</span>
            <span className="micro-tag is-contradiction">contradiction</span>
          </div>
        </div>
        <div className="graph-shell">
          <ReactFlow
            fitView
            nodes={graphNodes}
            edges={graphEdges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            panOnDrag={false}
            zoomOnScroll={false}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="rgba(244, 239, 228, 0.08)" gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <div className="graph-footnotes">
          <p>
            Visible evidence records:{" "}
            {Array.from(visibleEventIds)
              .map(String)
              .join(", ") || "None yet"}
          </p>
          <p>{currentCycle === 1 ? "Cycle 1 exposes false consensus around Supplier A." : "Cycle 2 shows independent support for Supplier B."}</p>
        </div>
      </section>

      <section className="section-grid reveal-panel">
        <article className="surface-card">
          <div className="section-heading">
            <div>
              <p className="utility-kicker">Timeline</p>
              <h2>Replanning stays legible across cycles.</h2>
            </div>
          </div>
          <ol className="timeline-list">
            {events.length === 0 ? (
              <li className="timeline-empty">
                {playbackMode === "live"
                  ? "Start the live run to populate the workflow ledger."
                  : "Start the recorded run to populate the workflow ledger."}
              </li>
            ) : (
              events.map((event) => (
                <li key={event.event_id} className="timeline-row">
                  <span className="timeline-sequence">{String(event.sequence).padStart(2, "0")}</span>
                  <div className="timeline-card">
                    <div className="timeline-meta">
                      <span>{labelForEvent(event.event_type)}</span>
                      <span>Cycle {String(event.payload.cycle ?? "-")}</span>
                      <span>{formatClock(event.occurred_at)} UTC</span>
                    </div>
                    <h3>{String(event.payload.note ?? event.payload.summary ?? event.payload.reason ?? "Recorded transition")}</h3>
                    <p className="muted-copy">
                      {event.actor_id ? `${event.actor_id} · ` : ""}
                      {JSON.stringify(event.payload)}
                    </p>
                  </div>
                </li>
              ))
            )}
          </ol>
        </article>

        <article className="surface-card">
          <div className="section-heading">
            <div>
              <p className="utility-kicker">Execution Boundary</p>
              <h2>Approval, freshness revalidation, and duplicate protection each get their own state.</h2>
            </div>
            <span className={`tone-chip ${statusTone(scenarioRecord.execution.current_state)}`}>
              {scenarioRecord.execution.current_state.replaceAll("_", " ")}
            </span>
          </div>
          <p className="muted-copy">{scenarioRecord.execution.detail}</p>
          <div className="ttl-grid">
            {scenarioRecord.execution.freshness_ttls.map((item) => (
              <article key={item.label} className="ttl-card">
                <p className="panel-kicker">{item.label}</p>
                <div className="ttl-meta">
                  <strong>{item.ttl}</strong>
                  <span className={`tone-chip ${statusTone(item.state)}`}>{item.state}</span>
                </div>
              </article>
            ))}
          </div>
          <ExecutionRail steps={scenarioRecord.execution.steps} progress={executionStepProgress} />
        </article>
      </section>
    </main>
  );
}
