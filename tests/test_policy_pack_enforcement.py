from __future__ import annotations

from pathlib import Path

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner


def _write_config(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.asyncio
async def test_model_allowlist_filters_catalog(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "cfg.yaml",
        """
version: 2
workspace:
  root: .
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
    - id: openai/gpt-4.1
      adapter: openai
modes:
  - name: build
    prompt: "noop"
loop:
  sequence:
    - mode: build
policies:
  models:
    allow:
      - "openrouter/openai/*"
""",
    )
    service = SessionService(SessionRegistry())
    catalog = await service.list_models(cfg_path)
    ids = [entry.id for entry in catalog.models]
    assert ids == ["openrouter/openai/gpt-5-nano"]
    assert catalog.default_model == "openrouter/openai/gpt-5-nano"


@pytest.mark.asyncio
async def test_set_model_enforced_by_policy(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path / "cfg.yaml",
        """
version: 2
workspace:
  root: .
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
policies:
  models:
    allow:
      - "openrouter/openai/*"
""",
    )
    registry = SessionRegistry()
    record = SessionRecord(session_id="sess-policy", status=SessionStatus.STARTING)
    request = SessionCreateRequest(config_path=cfg_path, task="hi")
    runner = SessionRunner(session=record, registry=registry, request=request)

    with pytest.raises(ValueError):
        await runner.handle_command("set_model", {"model": "openai/gpt-4.1"})

    result = await runner.handle_command("set_model", {"model": "openrouter/openai/gpt-5-nano"})
    assert result["status"] == "ok"
