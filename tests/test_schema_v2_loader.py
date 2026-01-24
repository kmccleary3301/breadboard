import os
from pathlib import Path

import pytest

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


def test_v2_loader_extends_and_validation(tmp_path, monkeypatch):
    base = tmp_path / "base_v2.yaml"
    child = tmp_path / "child_v2.yaml"
    base.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
  lsp: { enabled: false }
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
tools:
  registry:
    paths: [implementations/tools/defs]
        """.strip()
    )
    child.write_text(
        """
extends: base_v2.yaml
version: 2
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
        """.strip()
    )

    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    os.chdir(tmp_path)
    conf = load_agent_config(str(child))
    assert conf["version"] == 2
    assert conf["tools"]["defs_dir"].endswith("implementations/tools/defs")


def test_v2_loader_legacy_fallback(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("providers: {}\n")
    monkeypatch.delenv("AGENT_SCHEMA_V2_ENABLED", raising=False)
    conf = load_agent_config(str(legacy))
    assert isinstance(conf, dict)

