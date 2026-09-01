import { useEffect, useMemo, useRef, useState } from "react";

type Client = "codex" | "claude" | "json";

const repository = "git+https://github.com/minnhaaaaa/civitas";

const installers: Record<Client, { label: string; command: string }> = {
  codex: {
    label: "Codex",
    command: `codex mcp add civitas -- uvx --from ${repository} civitas-mcp-demo`,
  },
  claude: {
    label: "Claude Code",
    command: `claude mcp add civitas --scope user -- uvx --from ${repository} civitas-mcp-demo`,
  },
  json: {
    label: "MCP JSON",
    command: `{"mcpServers":{"civitas":{"command":"uvx","args":["--from","${repository}","civitas-mcp-demo"]}}}`,
  },
};

const resultLines = [
  ["resolve", "Civitas package fetched from GitHub"],
  ["connect", "STDIO server registered as civitas"],
  ["ready", "12 intent-level tools available"],
] as const;

export function InstallTerminal() {
  const host = useRef<HTMLDivElement>(null);
  const [client, setClient] = useState<Client>("codex");
  const [visible, setVisible] = useState(false);
  const [typedLength, setTypedLength] = useState(0);
  const [copied, setCopied] = useState(false);
  const command = installers[client].command;
  const prefersReducedMotion = useMemo(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.35 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setTypedLength(prefersReducedMotion ? command.length : 0);
    if (!visible || prefersReducedMotion) return;
    const timer = window.setInterval(() => {
      setTypedLength((length) => {
        if (length >= command.length) {
          window.clearInterval(timer);
          return length;
        }
        return Math.min(command.length, length + 2);
      });
    }, 16);
    return () => window.clearInterval(timer);
  }, [client, command, prefersReducedMotion, visible]);

  async function handleCopy() {
    await navigator.clipboard.writeText(command);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="install-terminal" ref={host}>
      <div className="terminal-chrome">
        <span className="terminal-lights" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span>civitas / installer</span>
        <span>stdio</span>
      </div>
      <div className="terminal-tabs" role="tablist" aria-label="MCP client">
        {(Object.keys(installers) as Client[]).map((key) => (
          <button
            type="button"
            role="tab"
            aria-selected={client === key}
            className={client === key ? "is-active" : ""}
            onClick={() => setClient(key)}
            key={key}
          >
            {installers[key].label}
          </button>
        ))}
      </div>
      <div className="terminal-body">
        <div className="terminal-command">
          <span aria-hidden="true">$</span>
          <code>{command.slice(0, typedLength)}</code>
          <i className="terminal-cursor" aria-hidden="true" />
          <button
            type="button"
            onClick={handleCopy}
            aria-label={`Copy ${installers[client].label} setup`}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <div
          className={`terminal-output ${typedLength === command.length ? "is-visible" : ""}`}
          aria-live="polite"
        >
          {resultLines.map(([status, message]) => (
            <p key={status}>
              <span>{status}</span>
              {message}
            </p>
          ))}
          <p className="terminal-success">
            <span>ready</span>
            Ask your agent: “Plan tomorrow’s food procurement with Civitas.”
          </p>
        </div>
      </div>
      <p className="terminal-note">
        Requires{" "}
        <a
          href="https://docs.astral.sh/uv/getting-started/installation/"
          target="_blank"
          rel="noreferrer"
        >
          uv
        </a>
        . This command installs the side-effect-safe sandbox. Production execution remains
        approval-gated and separately configured.
      </p>
    </div>
  );
}
