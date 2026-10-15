"""Local stock profiles reject remote Ray before creating an execution actor."""
import json

import pytest

from breadboard_engine.agent import AgenticCoder
from breadboard_engine.execution.stock_tools import native_concurrent_prompts
from tests.compaction.test_opencode_stock_interaction import runner_at


STOCK_LOCAL = ("opencode_1_2_17", "oh_my_opencode_3_10_0", "claude_code_2_1_63")


@pytest.mark.parametrize("semantics", STOCK_LOCAL)
def test_direct_stock_engine_rejects_remote_admission(tmp_path, monkeypatch, semantics):
    monkeypatch.delenv("RAY_SCE_LOCAL_MODE", raising=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"tools": {"native_semantics": semantics, "native_local_only": True}}))
    with pytest.raises(ValueError, match="remote Ray execution is unsupported"):
        AgenticCoder(str(config), str(tmp_path / "workspace"), force_local_mode=False)
    local = AgenticCoder(str(config), str(tmp_path / "workspace"), force_local_mode=True)
    assert local._local_mode and local.agent is None


@pytest.mark.parametrize("semantics", STOCK_LOCAL)
def test_api_stock_profile_rejects_remote_admission_without_freezing_bad_config(tmp_path, monkeypatch, semantics):
    monkeypatch.delenv("RAY_SCE_LOCAL_MODE", raising=False)
    runner = runner_at(tmp_path)
    runner._base_config_cache = {"tools": {"native_semantics": semantics, "native_local_only": True}}
    runner.session.metadata["cli_force_local_mode"] = False
    with pytest.raises(ValueError, match="remote Ray execution is unsupported"):
        runner.prepare_runtime_config()
    assert runner._prepared_runtime_config is None and runner._agent is None
    runner.session.metadata["cli_force_local_mode"] = True
    assert runner.prepare_runtime_config()["tools"]["native_semantics"] == semantics


@pytest.mark.parametrize("semantics", [None, "codex_0_139", "opencode_legacy"])
def test_other_profile_remote_admission_is_unchanged(tmp_path, monkeypatch, semantics):
    monkeypatch.delenv("RAY_SCE_LOCAL_MODE", raising=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"tools": {"native_semantics": semantics}}))
    remote = AgenticCoder(str(config), str(tmp_path / "workspace"), force_local_mode=False)
    assert not remote._local_mode and remote.agent is None
    runner = runner_at(tmp_path)
    runner._base_config_cache = {"tools": {"native_semantics": semantics}}
    runner.session.metadata["cli_force_local_mode"] = False
    assert runner.prepare_runtime_config()["tools"]["native_semantics"] == semantics


@pytest.mark.parametrize("value", [None, False, True, 0, 1, "true", {}, []])
def test_concurrent_prompt_capability_requires_explicit_boolean(value):
    assert native_concurrent_prompts({"tools": {"native_concurrent_prompts": value}}) is (value is True)


def test_concurrent_prompt_capability_does_not_derive_from_executor_identity():
    assert native_concurrent_prompts({}) is False
    assert native_concurrent_prompts({"tools": None}) is False
    for semantics in STOCK_LOCAL:
        assert native_concurrent_prompts({"tools": {"native_semantics": semantics}}) is False
    assert native_concurrent_prompts({"tools": {"native_concurrent_prompts": True}}) is True
