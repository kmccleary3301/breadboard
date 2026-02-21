from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


def test_v2_loader_rejects_invalid_long_running_policy_profile(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "invalid_longrun_policy_profile_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  policy_profile: turbo
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.policy_profile"):
        load_agent_config(str(cfg))


def test_v2_loader_accepts_valid_long_running_policy_profile(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "valid_longrun_policy_profile_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  policy_profile: balanced
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    prev = Path.cwd()
    try:
        os.chdir(tmp_path)
        conf = load_agent_config(str(cfg))
    finally:
        os.chdir(prev)
    assert conf["long_running"]["policy_profile"] == "balanced"
