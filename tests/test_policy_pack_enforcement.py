from __future__ import annotations

from pathlib import Path

import pytest

from breadboard_engine.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord, SessionRegistry
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.api.cli_bridge.session_runner import SessionRunner


def _write_config(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.asyncio
async def test_model_allowlist_filters_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.service.provider_router.get_credential_origin",
        lambda _route, **_kwargs: None,
    )
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
    assert catalog.discovery_policy == "configured_only"
    assert catalog.issues == []
    assert catalog.models[0].canonical_provider == "openrouter"
    assert catalog.models[0].available is False
    assert catalog.models[0].availability_reason == "missing_auth"


@pytest.mark.asyncio
async def test_model_catalog_resolves_environment_credentials_through_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []

    def credential_origin(route, *, session_id):
        observed.append((route, session_id))
        return {"kind": "env", "env_var": "OPENAI_API_KEY"}

    monkeypatch.setattr(
        "breadboard_engine.api.cli_bridge.service.provider_router.get_credential_origin",
        credential_origin,
    )
    cfg_path = _write_config(
        tmp_path / "catalog.yaml",
        """
version: 2
workspace:
  root: .
providers:
  default_model: openai/gpt-4.1
  models:
    - id: openai/gpt-4.1
      adapter: openai
modes:
  - name: build
    prompt: noop
loop:
  sequence:
    - mode: build
""",
    )

    catalog = await SessionService(SessionRegistry()).list_models(cfg_path)

    assert observed == [("openai/gpt-4.1", "model_catalog")]
    assert catalog.models[0].available is True


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

    result = await runner.handle_command(
        "set_model", {"model": "openrouter/openai/gpt-5-nano"}
    )
    assert result["status"] == "ok"
