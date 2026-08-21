from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable

from breadboard_engine.compilation.provider_schema import build_openai_tools_schema_from_yaml
from breadboard_engine.compilation.v2_loader import load_agent_config
from breadboard_engine.conductor_components import initialize_yaml_tools
from breadboard_engine.surface import build_tool_schema_snapshot


_RLM_TOOL_NAMES = {
    "blob.put",
    "blob.put_file_slice",
    "blob.get",
    "blob.search",
    "llm.query",
    "llm.batch_query",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _fixture_path() -> Path:
    return _repo_root() / "tests" / "fixtures" / "rlm_tool_schema_snapshots_v1.json"


def _load_fixture() -> Dict[str, Any]:
    return json.loads(_fixture_path().read_text(encoding="utf-8"))


def _compute_tool_schema_snapshot(config_rel_path: str) -> Dict[str, Any]:
    repo_root = _repo_root()
    previous_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        cfg = load_agent_config(config_rel_path)
    finally:
        os.chdir(previous_cwd)
    conductor = SimpleNamespace(
        config=cfg,
        workspace=str(repo_root),
        yaml_tools=[],
        yaml_tool_manipulations={},
    )
    initialize_yaml_tools(conductor)
    schema = build_openai_tools_schema_from_yaml(conductor.yaml_tools)
    snapshot = build_tool_schema_snapshot(schema, turn_index=1)
    assert snapshot is not None
    return snapshot


def _discover_rlm_enabled_configs() -> Iterable[str]:
    repo_root = _repo_root()
    previous_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        for cfg_path in sorted((repo_root / "agent_configs").rglob("*.yaml")):
            rel = str(cfg_path.relative_to(repo_root).as_posix())
            try:
                cfg = load_agent_config(rel)
            except Exception:
                continue
            if bool((((cfg.get("features") or {}).get("rlm") or {}).get("enabled"))):
                yield rel
    finally:
        os.chdir(previous_cwd)


def test_rlm_snapshot_fixture_matches_dedicated_configs(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    fixture = _load_fixture()
    entries = fixture.get("dedicated_rlm_configs") or []
    assert isinstance(entries, list) and entries
    for entry in entries:
        config_path = str(entry.get("config_path") or "")
        assert config_path
        snapshot = _compute_tool_schema_snapshot(config_path)
        assert snapshot["tool_count"] == int(entry["tool_count"])
        assert snapshot["schema_hash"] == str(entry["schema_hash"])
        assert snapshot["schema_hash_ordered"] == str(entry["schema_hash_ordered"])
        required_rlm_tools = {str(name) for name in (entry.get("required_rlm_tools") or [])}
        assert required_rlm_tools
        assert required_rlm_tools.issubset(set(snapshot.get("tool_names") or []))


def test_rlm_snapshot_fixture_only_covers_rlm_enabled_configs(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    fixture = _load_fixture()
    fixture_paths = {
        str(entry.get("config_path") or "")
        for entry in (fixture.get("dedicated_rlm_configs") or [])
        if entry.get("config_path")
    }
    discovered_paths = set(_discover_rlm_enabled_configs())
    assert discovered_paths == fixture_paths


def test_non_rlm_base_schema_excludes_rlm_tools_and_stays_stable(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    snapshot = _compute_tool_schema_snapshot("agent_configs/misc/base_v2.yaml")
    assert _RLM_TOOL_NAMES.isdisjoint(set(snapshot.get("tool_names") or []))
    assert snapshot["tool_count"] == 24
    assert snapshot["schema_hash"] == "db58034385a13d58e98dc0747b64fd92a19d8ee84eeb1cdb2acd2bd995bb4619"
    assert snapshot["schema_hash_ordered"] == "89b58690a3fc53da7f1947e25dbbe5ec3a318ff645f53e57e2b30c44ce73d241"
