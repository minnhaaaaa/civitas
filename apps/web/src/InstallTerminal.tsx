import { useEffect, useMemo, useRef, useState } from "react";

type Setup = "sandbox" | "connect";
type Option = "codex" | "claude" | "json" | "stdio" | "http";

const repository = "git+https://github.com/minnhaaaaa/civitas";

const commands: Record<Setup, Partial<Record<Option, { label: string; command: string }>>> = {
  sandbox: {
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
  },
  connect: {
    stdio: {
      label: "Local STDIO",
      command: `uvx --from ${repository} civitas providers add operations --name "Operations MCP" --transport stdio --command your-mcp-command`,
    },
    http: {
      label: "Private HTTP",
      command: `uvx --from ${repository} civitas providers add operations --name "Operations MCP" --transport http --url https://your-mcp.example/mcp`,
    },
  },
};

const options: Record<Setup, readonly [Option, ...Option[]]> = {
  sandbox: ["codex", "claude", "json"],
  connect: ["stdio", "http"],
};

const outputs: Record<Setup, readonly (readonly [string, string])[]> = {
  sandbox: [
    ["install", "Civitas registered locally"],
    ["ready", "Simulated procurement tools available"],
  ],
  connect: [
    ["saved", "Provider reference stored on this machine"],
    ["next", "Map its tools, then enable live mode"],
  ],
};

export function InstallTerminal() {
  const host = useRef<HTMLDivElement>(null);
  const [setup, setSetup] = useState<Setup>("sandbox");
  const [option, setOption] = useState<Option>("codex");
  const [visible, setVisible] = useState(false);
  const [typedLength, setTypedLength] = useState(0);
  const [copied, setCopied] = useState(false);
  const selected = commands[setup][option] ?? commands[setup][options[setup][0]];
  if (!selected) throw new Error("Missing installer command");
  const { command } = selected;
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
        return Math.min(command.length, length + 3);
      });
    }, 14);
    return () => window.clearInterval(timer);
  }, [command, prefersReducedMotion, visible]);

  function chooseSetup(next: Setup) {
    setSetup(next);
    setOption(options[next][0]);
  }

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
        <span>civitas / local setup</span>
        <span>{setup}</span>
      </div>
      <div className="terminal-mode-tabs" role="tablist" aria-label="Setup type">
        {(["sandbox", "connect"] as Setup[]).map((key) => (
          <button
            type="button"
            role="tab"
            aria-selected={setup === key}
            className={setup === key ? "is-active" : ""}
            onClick={() => chooseSetup(key)}
            key={key}
          >
            {key === "sandbox" ? "Try sandbox" : "Connect your MCP"}
          </button>
        ))}
      </div>
      <div className="terminal-tabs" role="tablist" aria-label="Setup option">
        {options[setup].map((key) => {
          const item = commands[setup][key];
          if (!item) return null;
          return (
            <button
              type="button"
              role="tab"
              aria-selected={option === key}
              className={option === key ? "is-active" : ""}
              onClick={() => setOption(key)}
              key={key}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      <div className="terminal-body">
        <div className="terminal-command">
          <span aria-hidden="true">$</span>
          <code>{command.slice(0, typedLength)}</code>
          <i className="terminal-cursor" aria-hidden="true" />
          <button type="button" onClick={handleCopy} aria-label={`Copy ${selected.label} setup`}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <div
          className={`terminal-output ${typedLength === command.length ? "is-visible" : ""}`}
          aria-live="polite"
        >
          {outputs[setup].map(([status, message]) => (
            <p key={status}>
              <span>{status}</span>
              {message}
            </p>
          ))}
          <p className="terminal-success">
            <span>local</span>
            {setup === "sandbox"
              ? "Run the full simulated workflow without purchase authority."
              : "Credentials stay in your environment and never enter the config file."}
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
        .{" "}
        <a href="https://github.com/minnhaaaaa/civitas/blob/main/docs/PROVIDER_ONBOARDING.md">
          Provider setup guide
        </a>
      </p>
    </div>
  );
}
