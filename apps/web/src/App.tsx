import { useEffect, useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { InstallTerminal } from "./InstallTerminal";

gsap.registerPlugin(useGSAP, ScrollTrigger);

const agents = [
  { name: "Demand", note: "Protect service levels" },
  { name: "Cost", note: "Control landed cost" },
  { name: "Freshness", note: "Protect shelf life" },
  { name: "Logistics", note: "Keep delivery feasible" },
  { name: "Supplier", note: "Reduce supply risk" },
  { name: "Waste", note: "Prevent overbuying" },
];

const evidenceChecks = [
  ["Inventory", "Current"],
  ["Supplier capacity", "Verified"],
  ["Lead time", "Independent"],
  ["Warehouse space", "Available"],
] as const;

type DemoState = "ready" | "running" | "complete";

function Arrow() {
  return <span aria-hidden="true">↗</span>;
}

export function App() {
  const root = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [demoState, setDemoState] = useState<DemoState>("ready");

  useEffect(() => {
    if (demoState !== "running") return;
    const timer = window.setTimeout(() => setDemoState("complete"), 1800);
    return () => window.clearTimeout(timer);
  }, [demoState]);

  useGSAP(
    () => {
      const media = gsap.matchMedia();

      media.add(
        {
          motion: "(prefers-reduced-motion: no-preference)",
          desktop: "(min-width: 761px)",
        },
        (context) => {
          if (!context.conditions?.motion) return;

          gsap
            .timeline({ defaults: { ease: "power3.out" } })
            .from(".site-header", { y: -28, autoAlpha: 0, duration: 0.7 })
            .from(".eyebrow", { y: 18, autoAlpha: 0, duration: 0.5 }, "-=0.25")
            .from(
              ".hero h1 span",
              { yPercent: 105, autoAlpha: 0, duration: 0.85, stagger: 0.09 },
              "-=0.25",
            )
            .from(".hero-bottom", { y: 24, autoAlpha: 0, duration: 0.6 }, "-=0.42")
            .from(
              ".orbit-agent, .orbit-core, .jury-stamp",
              { scale: 0.55, autoAlpha: 0, duration: 0.6, stagger: 0.06 },
              "-=0.55",
            );

          gsap.to(".orbit-line-one", { rotation: 360, duration: 34, repeat: -1, ease: "none" });
          gsap.to(".orbit-line-two", { rotation: -360, duration: 25, repeat: -1, ease: "none" });
          gsap.to(".jury-stamp", {
            y: -9,
            duration: 1.8,
            repeat: -1,
            yoyo: true,
            ease: "sine.inOut",
          });

          gsap.utils.toArray<HTMLElement>(".reveal-heading").forEach((element) => {
            gsap.from(element.children, {
              y: 44,
              autoAlpha: 0,
              duration: 0.8,
              stagger: 0.08,
              ease: "power3.out",
              scrollTrigger: { trigger: element, start: "top 82%", once: true },
            });
          });

          gsap.from(".statement-lines > div", {
            x: (index) => (index % 2 === 0 ? -55 : 55),
            autoAlpha: 0,
            duration: 0.85,
            stagger: 0.12,
            ease: "power3.out",
            scrollTrigger: { trigger: ".statement-lines", start: "top 78%", once: true },
          });

          gsap.from(".agent-grid article", {
            y: 50,
            autoAlpha: 0,
            duration: 0.65,
            stagger: 0.08,
            ease: "power2.out",
            scrollTrigger: { trigger: ".agent-grid", start: "top 78%", once: true },
          });

          gsap.from(".integrity-card", {
            x: context.conditions.desktop ? 70 : 0,
            y: context.conditions.desktop ? 0 : 45,
            autoAlpha: 0,
            duration: 0.9,
            ease: "power3.out",
            scrollTrigger: { trigger: ".integrity-card", start: "top 80%", once: true },
          });

          gsap.from(".score-path", {
            strokeDasharray: "0 100",
            duration: 1.2,
            ease: "power2.out",
            scrollTrigger: { trigger: ".integrity-card", start: "top 72%", once: true },
          });

          gsap.from(".decision-console", {
            y: 65,
            autoAlpha: 0,
            duration: 0.9,
            ease: "power3.out",
            scrollTrigger: { trigger: ".decision-console", start: "top 84%", once: true },
          });
        },
      );

      return () => media.revert();
    },
    { scope: root },
  );

  useGSAP(
    () => {
      if (demoState === "running") {
        gsap.to(".evidence-list i", {
          scale: 1.7,
          backgroundColor: "#c9f36b",
          duration: 0.42,
          repeat: -1,
          yoyo: true,
          stagger: 0.11,
          ease: "sine.inOut",
        });
      }

      if (demoState === "complete") {
        gsap.from(".console-verdict strong", {
          y: 18,
          autoAlpha: 0,
          duration: 0.5,
          stagger: 0.1,
          ease: "back.out(1.5)",
        });
        gsap.from(".evidence-list b", { autoAlpha: 0, duration: 0.35, stagger: 0.06 });
      }
    },
    { dependencies: [demoState], scope: root, revertOnUpdate: true },
  );

  return (
    <div className="site-shell" ref={root}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <a
          className="brand"
          href="#top"
          onClick={() => setMenuOpen(false)}
          aria-label="Civitas home"
        >
          <span>Civitas</span>
        </a>
        <nav className={`pill-nav ${menuOpen ? "is-open" : ""}`} aria-label="Primary navigation">
          <a href="#why" onClick={() => setMenuOpen(false)}>
            Why
          </a>
          <a href="#parliament" onClick={() => setMenuOpen(false)}>
            Parliament
          </a>
          <a href="#proof" onClick={() => setMenuOpen(false)}>
            Jury
          </a>
          <a href="#install" onClick={() => setMenuOpen(false)}>
            Install
          </a>
          <a href="#demo" onClick={() => setMenuOpen(false)}>
            Demo
          </a>
        </nav>
        <a className="header-cta" href="#install">
          Install MCP <Arrow />
        </a>
        <button
          className="menu-button"
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span />
          <span />
        </button>
      </header>

      <main id="main">
        <section className="hero" id="top">
          <div className="hero-copy">
            <p className="eyebrow">
              <i /> Autonomous procurement
            </p>
            <h1>
              <span>Food buying</span>
              <span className="hero-indent">that can</span>
              <span className="hero-outline">prove itself.</span>
            </h1>
            <div className="hero-bottom">
              <p>Six agents debate. A Jury checks the proof before anything is bought.</p>
              <a className="primary-button" href="#install">
                Install in your agent <Arrow />
              </a>
            </div>
          </div>

          <div
            className="parliament-orbit"
            aria-label="Six procurement agents surrounding a solver-validated plan"
          >
            <div className="orbit-line orbit-line-one" />
            <div className="orbit-line orbit-line-two" />
            {agents.map((agent, index) => (
              <div className={`orbit-agent orbit-agent-${index + 1}`} key={agent.name}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{agent.name}</strong>
              </div>
            ))}
            <div className="orbit-core">
              <small>Solver plan</small>
              <strong>P–04</strong>
              <span>Feasible</span>
            </div>
            <div className="jury-stamp">
              <small>Independent</small>
              <strong>Jury</strong>
            </div>
          </div>
          <a className="scroll-note" href="#why">
            The case for Civitas <span>↓</span>
          </a>
        </section>

        <section className="proof-statement" id="why">
          <p className="section-tag">The problem</p>
          <div className="statement-lines">
            <div>
              <span>Agent agreement</span>
              <b>is not</b>
            </div>
            <div>
              <span>Independent evidence</span>
              <b>is not</b>
            </div>
            <div className="statement-final">
              <span>A correct decision.</span>
              <i>That gap is where Civitas works.</i>
            </div>
          </div>
        </section>

        <section className="parliament-section" id="parliament">
          <header className="section-heading reveal-heading">
            <p className="section-tag">The Parliament</p>
            <h2>
              Six objectives.
              <br />
              One feasible plan.
            </h2>
            <p>Agents debate trade-offs. OR-Tools builds the allocation.</p>
          </header>
          <div className="agent-grid">
            {agents.map((agent, index) => (
              <article key={agent.name}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{agent.name}</h3>
                <p>{agent.note}</p>
                <i aria-hidden="true">↗</i>
              </article>
            ))}
          </div>
        </section>

        <section className="jury-section" id="proof">
          <div className="jury-copy reveal-heading">
            <p className="section-tag">The Jury</p>
            <h2>
              Consensus
              <br />
              doesn’t get
              <br />a free pass.
            </h2>
            <p>Claims are checked for source, freshness, and contradiction.</p>
          </div>
          <div className="integrity-card">
            <div className="integrity-topline">
              <span>Decision integrity / v1</span>
              <i>Approve-eligible</i>
            </div>
            <div className="integrity-score">
              <strong>92</strong>
              <span>/100</span>
              <svg
                viewBox="0 0 180 90"
                role="img"
                aria-label="Decision integrity score 92 out of 100"
              >
                <path d="M10 85 A80 80 0 0 1 170 85" pathLength="100" />
                <path className="score-path" d="M10 85 A80 80 0 0 1 170 85" pathLength="100" />
              </svg>
            </div>
            <div className="integrity-rows">
              <div>
                <span>Evidence coverage</span>
                <b>96</b>
              </div>
              <div>
                <span>Source independence</span>
                <b>88</b>
              </div>
              <div>
                <span>Freshness</span>
                <b>94</b>
              </div>
              <div>
                <span>Dissent robustness</span>
                <b>90</b>
              </div>
            </div>
            <footer>
              <i /> All hard gates passed
            </footer>
          </div>
        </section>

        <section className="install-section" id="install">
          <header className="install-heading reveal-heading">
            <p className="section-tag">Run Civitas where you work</p>
            <h2>
              One command.
              <br />
              Your agent gets a Parliament.
            </h2>
            <p>
              Register the local sandbox in Codex, Claude Code, or any standard MCP client. No
              database, provider credentials, or purchase authority required.
            </p>
          </header>
          <InstallTerminal />
          <div className="install-facts" aria-label="Installation facts">
            <span>01 / Local STDIO</span>
            <span>02 / Side-effect-safe sandbox</span>
            <span>03 / Production path documented</span>
          </div>
        </section>

        <section className="demo-section" id="demo">
          <header className="section-heading demo-heading reveal-heading">
            <p className="section-tag">A decision, end to end</p>
            <h2>
              Ask once.
              <br />
              See the proof.
            </h2>
          </header>
          <div className={`decision-console state-${demoState}`}>
            <div className="console-bar">
              <span>
                <i /> Civitas / live simulation
              </span>
              <small>RUN–0828</small>
            </div>
            <div className="operator-request">
              <span>Operator</span>
              <p>Cover tomorrow’s demand. Minimize cost and waste. Ask before buying.</p>
            </div>
            <div className="console-body">
              <div className="evidence-list">
                {evidenceChecks.map(([label, status]) => (
                  <div key={label}>
                    <i />
                    <span>{label}</span>
                    <b>
                      {demoState === "ready"
                        ? "Queued"
                        : demoState === "running"
                          ? "Checking"
                          : status}
                    </b>
                  </div>
                ))}
              </div>
              <div className="console-verdict">
                <div>
                  <span>Candidate plan</span>
                  <strong>{demoState === "complete" ? "P–04" : "—"}</strong>
                  <small>
                    {demoState === "complete"
                      ? "220 kg · ₹8,140 ceiling"
                      : "Awaiting investigation"}
                  </small>
                </div>
                <div>
                  <span>Jury verdict</span>
                  <strong>
                    {demoState === "complete"
                      ? "Approve"
                      : demoState === "running"
                        ? "Reviewing"
                        : "—"}
                  </strong>
                  <small>
                    {demoState === "complete"
                      ? "Human approval still required"
                      : "No decision without evidence"}
                  </small>
                </div>
              </div>
            </div>
            <div className="console-footer">
              <span>
                {demoState === "complete"
                  ? "Decision ready · approval required"
                  : "Sandbox simulation"}
              </span>
              <button
                type="button"
                onClick={() => setDemoState("running")}
                disabled={demoState === "running"}
              >
                {demoState === "ready" && "Run the Parliament"}
                {demoState === "running" && "Investigating…"}
                {demoState === "complete" && "Run again"}
                <Arrow />
              </button>
            </div>
          </div>
        </section>

        <section className="closing-section">
          <h2>
            Let the agents argue.
            <br />
            <span>Trust the evidence.</span>
          </h2>
          <a
            className="closing-link"
            href="https://github.com/minnhaaaaa/civitas"
            target="_blank"
            rel="noreferrer"
          >
            Explore Civitas <Arrow />
          </a>
        </section>
      </main>

      <footer className="site-footer">
        <a className="brand" href="#top">
          <span>Civitas</span>
        </a>
        <p>Accountable autonomous procurement.</p>
        <span>2026</span>
      </footer>
    </div>
  );
}
