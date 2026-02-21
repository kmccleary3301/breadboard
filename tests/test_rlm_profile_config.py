from __future__ import annotations

import os
from pathlib import Path

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


def test_rlm_base_profile_loads_and_enables_rlm(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    repo_root = Path(__file__).resolve().parents[1]
    prev = Path.cwd()
    try:
        os.chdir(repo_root)
        cfg = load_agent_config("agent_configs/rlm_base_v1.yaml")
    finally:
        os.chdir(prev)
    assert cfg["version"] == 2
    assert cfg["features"]["rlm"]["enabled"] is True
    assert cfg["features"]["rlm"]["budget"]["max_subcalls"] == 64
    assert cfg["features"]["rlm"]["scheduling"]["batch"]["retries"] == 1
    assert cfg["features"]["rlm"]["scheduling"]["batch"]["max_concurrency_per_branch"] == 1
