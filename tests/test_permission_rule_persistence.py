from __future__ import annotations

import queue
from pathlib import Path

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner
from agentic_coder_prototype.permission_rules_store import load_permission_rules


@pytest.mark.asyncio
async def test_permission_decision_persists_project_rule(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    # Minimal v2 config (valid loader) with workspace root aligned to the tmp workspace.
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        f"""
version: 2
workspace:
  root: {workspace.as_posix()}
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
    prompt: "noop"
loop:
  sequence:
    - mode: build
""",
        encoding="utf-8",
    )

    registry = SessionRegistry()
    session = SessionRecord(session_id="sess_rules", status=SessionStatus.RUNNING)
    request = SessionCreateRequest(config_path=str(cfg_path), task="hi", workspace=str(workspace), stream=False)
    runner = SessionRunner(session=session, registry=registry, request=request)

    # Provide the queue expected by respond_permission so permission_decision can complete.
    runner._permission_queue = queue.Queue()
    runner._workspace_path = workspace

    runner._update_pending_permissions(
        "permission_request",
        {
            "request_id": "permission_1",
            "category": "shell",
            "items": [
                {"item_id": "permission_1_item_1", "category": "shell", "pattern": "npm install foo", "metadata": {}},
            ],
        },
        source="session",
    )

    await runner.handle_command(
        "permission_decision",
        {
            "request_id": "permission_1",
            "decision": "allow-always",
            "rule": "npm install *",
            "scope": "project",
        },
    )

    rules = load_permission_rules(workspace)
    assert any(rule.category == "shell" and rule.pattern == "npm install *" and rule.decision == "allow" for rule in rules)

