import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
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
  EvidenceGraphNodeData,
  EvidenceGraphSnapshot,
  JuryCycleSnapshot,
  ScenarioSummary,
  WorkflowEvent,
} from "./contracts";
import { createMockRunStream, getPlaybackCycle, scenarioRecord } from "./mockPlayback";

gsap.registerPlugin(useGSAP, ScrollTrigger);

const STORY_CHAPTERS = ["Consensus", "Lineage", "Dissent", "Replan"] as const;
const WORKFLOW_EVENT_TYPES = [
  "run.started",
  "task.started",
  "task.completed",
  "evidence.recorded",
  "proposal.created",
  "jury.evaluated",
  "investigation.requested",
  "execution.updated",
  "run.completed",
] as const;

function labelForEvent(eventType: string): string {
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

function graphForCycle(cycle: number): EvidenceGraphSnapshot {
  return (
    scenarioRecord.evidenceGraphs.find((graph) => graph.cycle === cycle) ??
    scenarioRecord.evidenceGraphs[0]!
  );
}

function juryForCycle(cycle: number): JuryCycleSnapshot {
  return scenarioRecord.jury.find((item) => item.cycle === cycle) ?? scenarioRecord.jury[0]!;
}

function nodeClassName(node: Node<EvidenceGraphNodeData>): string {
  const classes = ["graph-node", `graph-node-${node.data.kind}`];
  if (node.data.shared) classes.push("is-shared");
  if (node.data.cleanRoom) classes.push("is-clean-room");
  if (node.data.contradicted) classes.push("is-contradicted");
  return classes.join(" ");
}

function EvidenceNode({ data }: NodeProps<Node<EvidenceGraphNodeData>>) {
  return (
    <div className="graph-node__content">
      <span>{data.kind}</span>
      <strong>{data.label}</strong>
      <small>{data.detail}</small>
    </div>
  );
}

const nodeTypes: NodeTypes = { evidenceNode: EvidenceNode };

type DemoScenarioResponse = { scenarios: ScenarioSummary[] };
type DemoRunResponse = { run_id: string; scenario_id: string; status: string; stream_url: string };
type EventSourceLike = {
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(
    type: string,
    listener: ((event: { data: WorkflowEvent }) => void) | EventListenerOrEventListenerObject,
  ): void;
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

  const rootRef = useRef<HTMLDivElement | null>(null);
  const storyRef = useRef<HTMLElement | null>(null);
  const streamRef = useRef<EventSourceLike | null>(null);

  const currentCycle = useMemo(() => getPlaybackCycle(events), [events]);
  const currentJury = useMemo(() => juryForCycle(currentCycle), [currentCycle]);
  const currentGraph = useMemo(() => graphForCycle(currentCycle), [currentCycle]);
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
        if (!response.ok) throw new Error(`Scenario request failed with ${response.status}`);
        const payload = (await response.json()) as DemoScenarioResponse;
        if (!cancelled && payload.scenarios.length > 0) {
          setAvailableScenarios(payload.scenarios);
          setSelectedScenarioId(payload.scenarios[0]!.scenario_id);
          setPlaybackMode("live");
        }
      } catch {
        if (!cancelled) setPlaybackMode("mock");
      }
    }
    void loadScenarios();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let active = true;
    void document.fonts.ready.then(() => {
      if (active) ScrollTrigger.refresh();
    });
    return () => {
      active = false;
    };
  }, [currentCycle]);

  function attachStream(source: EventSourceLike) {
    streamRef.current = source;
    const ingest = (message: { data: string | WorkflowEvent }) => {
      const event =
        typeof message.data === "string"
          ? (JSON.parse(message.data) as WorkflowEvent)
          : message.data;
      startTransition(() => {
        setEvents((current) =>
          current.some((item) => item.sequence === event.sequence) ? current : [...current, event],
        );
      });
    };
    source.onmessage = ingest;
    WORKFLOW_EVENT_TYPES.forEach((eventType) => source.addEventListener(eventType, ingest));
    source.addEventListener("run.completed", () => {
      setIsPlaying(false);
      source.close();
    });
    source.onerror = () => {
      setStreamError("The live stream stopped. Start the story again to reconnect.");
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
        if (!response.ok) throw new Error(`Run creation failed with ${response.status}`);
        const payload = (await response.json()) as DemoRunResponse;
        attachStream(new EventSource(payload.stream_url));
        document.getElementById("story")?.scrollIntoView({ behavior: "smooth" });
        return;
      } catch {
        setPlaybackMode("mock");
      }
    }

    const { source } = createMockRunStream(850);
    attachStream(source);
    source.play();
    document.getElementById("story")?.scrollIntoView({ behavior: "smooth" });
  }

  useGSAP(
    () => {
      const media = gsap.matchMedia();
      media.add(
        { desktop: "(min-width: 901px)", reduceMotion: "(prefers-reduced-motion: reduce)" },
        (context) => {
          const { desktop, reduceMotion } = context.conditions as {
            desktop: boolean;
            reduceMotion: boolean;
          };

          if (!reduceMotion) {
            const intro = gsap.timeline({ defaults: { ease: "power3.out" } });
            intro
              .from(".site-header", { y: -18, duration: 0.55 })
              .from(".hero__kicker", { y: 16, duration: 0.45 }, "-=0.2")
              .from(
                ".hero__line",
                { yPercent: 70, rotation: 2, stagger: 0.12, duration: 0.9 },
                "-=0.2",
              )
              .from(".hero__aside", { y: 18, duration: 0.55 }, "-=0.45")
              .from(".hero-orbit", { scale: 0.82, duration: 0.8 }, "-=0.55");

            gsap.to(".hero-orbit__ring", { rotation: 48, duration: 4, ease: "power1.out" });

            gsap.utils.toArray<HTMLElement>(".reveal").forEach((element) => {
              gsap.from(element, {
                autoAlpha: 0,
                y: 54,
                duration: 0.85,
                ease: "power3.out",
                scrollTrigger: {
                  trigger: element,
                  start: "clamp(top 86%)",
                  toggleActions: "play none none reverse",
                },
              });
            });
          }

          if (desktop && !reduceMotion && storyRef.current) {
            const track = storyRef.current.querySelector<HTMLElement>(".story-track");
            if (track) {
              gsap.to(track, {
                xPercent: -75,
                ease: "none",
                scrollTrigger: {
                  id: "civitas-story",
                  trigger: storyRef.current,
                  start: "top top",
                  end: "+=3600",
                  pin: true,
                  scrub: 0.8,
                  invalidateOnRefresh: true,
                },
              });
              gsap.to(".story-progress__fill", {
                scaleX: 1,
                transformOrigin: "left center",
                ease: "none",
                scrollTrigger: {
                  trigger: storyRef.current,
                  start: "top top",
                  end: "+=3600",
                  scrub: 0.8,
                },
              });
              gsap.to(".consensus-core", {
                scale: 0.76,
                rotation: -7,
                autoAlpha: 0.24,
                ease: "none",
                scrollTrigger: {
                  trigger: storyRef.current,
                  start: "top top",
                  end: "+=900",
                  scrub: true,
                },
              });
            }
          }
        },
      );
      return () => media.revert();
    },
    { scope: rootRef },
  );

  return (
    <div className="experience" ref={rootRef}>
      <a className="skip-link" href="#main">
        Skip to Story
      </a>

      <header className="site-header">
        <a className="wordmark" href="#top" translate="no" aria-label="Civitas Home">
          <span className="wordmark__seal" aria-hidden="true">
            C
          </span>
          <span>Civitas</span>
        </a>
        <nav className="site-nav" aria-label="Main Navigation">
          <a href="#story">Story</a>
          <a href="#evidence">Evidence</a>
          <a href="#execution">Execution</a>
        </nav>
        <span className={`live-mark ${isPlaying ? "is-running" : ""}`} aria-live="polite">
          <i aria-hidden="true" />
          {isPlaying ? "Live" : playbackMode === "live" ? "API Ready" : "Replay Ready"}
        </span>
      </header>

      <main id="main">
        <section className="hero" id="top">
          <div className="hero__copy">
            <p className="hero__kicker">Autonomous Procurement / Decision Integrity</p>
            <h1>
              <span className="hero__line">Agreement</span>
              <span className="hero__line hero__line--shift">Is Not</span>
              <span className="hero__line hero__line--outline">Evidence.</span>
            </h1>
          </div>

          <div className="hero__aside">
            <p>{selectedScenario.description}</p>
            <div className="run-control">
              <label htmlFor="scenario-select">Choose Scenario</label>
              <select
                id="scenario-select"
                name="scenario"
                value={selectedScenarioId}
                onChange={(event) => setSelectedScenarioId(event.target.value)}
                disabled={isPlaying}
                autoComplete="off"
              >
                {availableScenarios.map((scenario) => (
                  <option key={scenario.scenario_id} value={scenario.scenario_id}>
                    {scenario.title}
                  </option>
                ))}
              </select>
              <button type="button" onClick={() => void startPlayback()} disabled={isPlaying}>
                <span>{isPlaying ? "Running…" : "Run the Story"}</span>
                <span aria-hidden="true">↘</span>
              </button>
            </div>
            <p className="run-status" aria-live="polite">
              {latestEvent ? labelForEvent(latestEvent.event_type) : "Waiting for a run"}
            </p>
            {streamError ? (
              <p className="stream-error" role="status">
                {streamError}
              </p>
            ) : null}
          </div>

          <div className="hero-orbit" aria-hidden="true">
            <div className="hero-orbit__ring">
              {scenarioRecord.parliament.map((agent, index) => (
                <i key={agent.agent_id} style={{ "--i": index } as React.CSSProperties} />
              ))}
            </div>
            <span>
              Trust
              <br />
              The
              <br />
              Lineage
            </span>
          </div>

          <a className="scroll-cue" href="#story">
            Scroll to Investigate <span aria-hidden="true">↓</span>
          </a>
        </section>

        <section className="story" id="story" ref={storyRef} aria-label="Decision Story">
          <div className="story-progress" aria-hidden="true">
            <div className="story-progress__labels">
              {STORY_CHAPTERS.map((chapter) => (
                <span key={chapter}>{chapter}</span>
              ))}
            </div>
            <i>
              <b className="story-progress__fill" />
            </i>
          </div>

          <div className="story-track">
            <article className="story-frame story-frame--consensus">
              <div className="frame-index">01 / Parliament</div>
              <div className="frame-copy">
                <p className="eyebrow">The Vote</p>
                <h2>
                  5 Voices.
                  <br />1 Answer.
                </h2>
                <p>Supplier A appears to win.</p>
              </div>
              <div className="consensus-core">
                <strong>5/6</strong>
                <span>Supplier A</span>
                <div className="agent-ring" aria-label="Parliament Recommendations">
                  {scenarioRecord.parliament.map((agent, index) => (
                    <div
                      key={agent.agent_id}
                      className={agent.supplier === "Supplier A" ? "is-majority" : ""}
                      style={{ "--i": index } as React.CSSProperties}
                      title={`${agent.label}: ${agent.supplier}`}
                    >
                      {agent.label.slice(0, 1)}
                    </div>
                  ))}
                </div>
              </div>
            </article>

            <article className="story-frame story-frame--lineage">
              <div className="frame-index">02 / Jury</div>
              <div className="frame-copy">
                <p className="eyebrow">Trace the Source</p>
                <h2>
                  5 Voices.
                  <br />1 Origin.
                </h2>
                <p>Consensus collapses under lineage.</p>
              </div>
              <div className="lineage-web" aria-label="Shared Evidence Lineage">
                <div className="source-pulse">
                  <span>Shared Source</span>
                  <strong>
                    Supplier A<br />
                    Master
                  </strong>
                </div>
                <div className="lineage-spokes" aria-hidden="true">
                  {scenarioRecord.parliament.slice(0, 5).map((agent, index) => (
                    <i key={agent.agent_id} style={{ "--i": index } as React.CSSProperties} />
                  ))}
                </div>
                <div className="lineage-agents">
                  {scenarioRecord.parliament.slice(0, 5).map((agent) => (
                    <span key={agent.agent_id}>{agent.label}</span>
                  ))}
                </div>
                <div className="echo-stamp">+ 1 Agent Echo</div>
              </div>
            </article>

            <article className="story-frame story-frame--dissent">
              <div className="frame-index">03 / Clean Room</div>
              <div className="frame-copy frame-copy--light">
                <p className="eyebrow">Dissent Investigates</p>
                <h2>
                  The Story
                  <br />
                  Breaks.
                </h2>
                <p>A fresh audit contradicts the plan.</p>
              </div>
              <div className="contradiction">
                <div>
                  <span>Parliament Assumed</span>
                  <strong>1</strong>
                  <small>Day Lead Time</small>
                </div>
                <div className="contradiction__slash" aria-hidden="true" />
                <div>
                  <span>Clean Room Found</span>
                  <strong>10</strong>
                  <small>Days / Live Audit</small>
                </div>
                <p>
                  Integrity <b>41</b> / Investigate
                </p>
              </div>
            </article>

            <article className="story-frame story-frame--replan">
              <div className="frame-index">04 / Replan</div>
              <div className="frame-copy">
                <p className="eyebrow">Evidence Changes the Plan</p>
                <h2>
                  Trust,
                  <br />
                  Rebuilt.
                </h2>
                <p>Supplier B survives independent checks.</p>
              </div>
              <div className="approval-mark">
                <div className="approval-mark__score">
                  <strong>91</strong>
                  <span>Integrity</span>
                </div>
                <div className="approval-mark__route">
                  <span>Supplier B</span>
                  <i aria-hidden="true" />
                  <span>Approved</span>
                </div>
                <p>Independent evidence / Fresh inputs / All gates passed</p>
              </div>
            </article>
          </div>
        </section>

        <section className="evidence-section" id="evidence">
          <header className="evidence-heading reveal">
            <p className="eyebrow">Live Evidence Map / Cycle {hasStarted ? currentCycle : 1}</p>
            <h2>
              See What
              <br />
              They Saw.
            </h2>
            <div className="evidence-score">
              <span>Decision Integrity</span>
              <strong>{hasStarted ? currentJury.integrity_score : 41}</strong>
              <small>{hasStarted ? currentJury.state : "investigate"}</small>
            </div>
          </header>

          <div className="evidence-canvas reveal">
            <ReactFlow
              fitView
              nodes={graphNodes}
              edges={graphEdges}
              nodeTypes={nodeTypes}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              panOnDrag
              zoomOnScroll={false}
              zoomOnPinch
              minZoom={0.55}
              maxZoom={1.5}
              proOptions={{ hideAttribution: true }}
              aria-label="Evidence Lineage Graph"
            >
              <Background color="rgba(247,244,234,0.12)" gap={30} />
            </ReactFlow>
          </div>

          <div className="integrity-audit reveal">
            <section aria-labelledby="components-title">
              <header>
                <span>01</span>
                <h3 id="components-title">Integrity Components</h3>
              </header>
              <dl>
                {Object.entries(currentJury.components).map(([component, score]) => (
                  <div key={component}>
                    <dt>{component.replaceAll("_", " ")}</dt>
                    <dd>{score}</dd>
                  </div>
                ))}
              </dl>
            </section>
            <section aria-labelledby="gates-title">
              <header>
                <span>02</span>
                <h3 id="gates-title">Hard Gates</h3>
              </header>
              <ul>
                {currentJury.gates.map((gate) => (
                  <li key={gate.gate_code} className={gate.passed ? "is-passed" : "is-blocked"}>
                    <span>{gate.gate_code.replaceAll("-", " ")}</span>
                    <strong>{gate.passed ? "Pass" : "Block"}</strong>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <div className="evidence-legend reveal" aria-label="Graph Legend">
            <span>
              <i className="legend-shared" /> Shared Source
            </span>
            <span>
              <i className="legend-clean" /> Clean Room
            </span>
            <span>
              <i className="legend-conflict" /> Contradiction
            </span>
            <p>Drag to explore / Pinch to zoom</p>
          </div>
        </section>

        <section className="execution-section" id="execution">
          <header className="execution-heading reveal">
            <p className="eyebrow">Safe Execution Boundary</p>
            <h2>
              One Decision.
              <br />
              One Write.
            </h2>
            <p>Freshness is checked at the moment of action. Retries cannot duplicate the order.</p>
          </header>

          <div className="execution-path reveal">
            {scenarioRecord.execution.steps.map((step, index) => (
              <div key={step.label} className={`execution-node execution-node--${step.state}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{step.label}</strong>
                <small>{step.state.replaceAll("_", " ")}</small>
              </div>
            ))}
          </div>

          <div className="execution-proof reveal">
            <div className="order-stamp">
              <span>Approved MCP Write</span>
              <strong>ORDER / 001</strong>
              <small>Supplier B · 4 units</small>
            </div>
            <div className="duplicate-stamp">
              <span>Retry / Same Key</span>
              <strong>Duplicate Blocked</strong>
            </div>
          </div>

          <section className="live-trace reveal" aria-labelledby="trace-title">
            <header>
              <h3 id="trace-title">Live Trace</h3>
              <span>{events.length} Events</span>
            </header>
            {events.length === 0 ? (
              <p className="trace-empty">Run the story to watch the workflow arrive over SSE.</p>
            ) : (
              <ol>
                {events.slice(-8).map((event) => (
                  <li key={event.event_id}>
                    <time dateTime={event.occurred_at}>{formatClock(event.occurred_at)}</time>
                    <span>{labelForEvent(event.event_type)}</span>
                    <i aria-hidden="true" />
                  </li>
                ))}
              </ol>
            )}
          </section>
        </section>
      </main>

      <footer className="site-footer">
        <span translate="no">Civitas / Agent Parliament + Jury</span>
        <a href="#top">Back to Top ↑</a>
      </footer>
    </div>
  );
}
