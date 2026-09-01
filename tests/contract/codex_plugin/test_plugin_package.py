from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "civitas"


def test_plugin_manifest_declares_only_checked_in_components() -> None:
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "civitas"
    assert manifest["version"] == "0.1.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["displayName"] == "Civitas Procurement"
    assert len(manifest["interface"]["defaultPrompt"]) <= 3
    assert not {"apps", "hooks"} & manifest.keys()


def test_stdio_configuration_is_portable_and_uses_the_inbound_server() -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())
    server = configuration["mcpServers"]["civitas"]

    assert server["command"] == "uvx"
    assert server["args"] == [
        "--from",
        "git+https://github.com/minnhaaaaa/civitas",
        "civitas-mcp-demo",
    ]
    serialized = json.dumps(configuration)
    assert "/home/" not in serialized
    assert "secret" not in serialized.lower()


def test_skill_preserves_the_guarded_execution_sequence() -> None:
    skill = (PLUGIN_ROOT / "skills" / "civitas-procurement" / "SKILL.md").read_text()

    assert "plan_procurement_goal" in skill
    assert "get_planning_run" in skill
    assert "get_decision_summary" in skill
    assert "prepare_execution" in skill
    assert "approve_execution" in skill
    assert "execute_approved_plan" in skill
    assert "idempotency key" in skill
    assert "Do not invent" in skill
    assert "A conversational “yes” alone is not authorization." in skill


def test_judge_document_covers_false_consensus_and_duplicate_retry() -> None:
    document = (REPOSITORY_ROOT / "docs" / "CODEX_DEMO.md").read_text()

    assert "false-consensus" in document
    assert "clean-room Dissent" in document
    assert "duplicate" in document
    assert "idempotency key" in document
    assert "/home/" not in document
