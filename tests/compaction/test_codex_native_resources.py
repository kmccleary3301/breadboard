"""Installed-source and prebuilt contracts for the shared native workspace."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from breadboard_engine.execution import codex_native, codex_search


def _installed_sources(tmp_path, monkeypatch):
    installed = tmp_path / "site-packages" / "breadboard_engine" / "execution"
    installed.mkdir(parents=True)
    for name in ("Cargo.toml", "Cargo.lock"):
        shutil.copyfile(codex_native.SOURCE_ROOT / name, installed / name)
    for helper in codex_native.HELPERS:
        member = installed / f"codex_{helper}"
        member.mkdir()
        for name in ("Cargo.toml", "main.rs"):
            shutil.copyfile(codex_native.SOURCE_ROOT / f"codex_{helper}" / name, member / name)
        monkeypatch.delenv(f"BREADBOARD_CODEX_{helper.upper()}_BINARY", raising=False)
    monkeypatch.setattr(codex_native, "SOURCE_ROOT", installed)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    monkeypatch.chdir(outside)
    return installed


def test_installed_sources_share_one_target_and_invalidate_on_any_member(tmp_path, monkeypatch):
    installed = _installed_sources(tmp_path, monkeypatch)
    paths = [codex_native.native_binary(helper) for helper in codex_native.HELPERS]
    assert len({path.parent for path in paths}) == 1
    assert {path.name for path in paths} == {f"breadboard-codex-{helper}" for helper in codex_native.HELPERS}
    original = paths[0].parent
    with (installed / "codex_search/main.rs").open("a") as source:
        source.write("\n// fingerprint fixture\n")
    assert codex_native.native_binary("patch").parent != original
    second = codex_native.workspace_target()
    with (installed / "Cargo.lock").open("a") as lock:
        lock.write("\n# lock fingerprint fixture\n")
    assert codex_native.workspace_target() != second


@pytest.mark.parametrize("helper", codex_native.HELPERS)
def test_prebuilt_overrides_do_not_require_packaged_sources(tmp_path, monkeypatch, helper):
    monkeypatch.setattr(codex_native, "SOURCE_ROOT", tmp_path / "absent-sources")
    binary = tmp_path / f"prebuilt-{helper}"
    binary.write_bytes(b"prebuilt")
    monkeypatch.setenv(f"BREADBOARD_CODEX_{helper.upper()}_BINARY", str(binary))
    assert codex_native.require_native_binary(helper) == binary
    explicit = tmp_path / "explicit"
    assert codex_native.native_binary(helper, binary_path=explicit) == explicit


def test_missing_packaged_sources_report_resource_error(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_native, "SOURCE_ROOT", tmp_path / "missing")
    monkeypatch.delenv("BREADBOARD_CODEX_SEARCH_BINARY", raising=False)
    with pytest.raises(codex_native.NativeHelperUnavailable):
        codex_native.native_binary("search")


def test_unknown_helper_is_rejected_before_resource_lookup():
    with pytest.raises(ValueError):
        codex_native.native_binary("other")


def test_missing_search_helper_returns_tool_error_and_closed_search_output(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_native, "native_binary", lambda helper: tmp_path / "absent")
    owner = SimpleNamespace(config={"tools": {"native_semantics": "codex_0_139"}})
    result = codex_search.execute_codex_search_tool(owner, {"query": "spawn agent"})
    assert isinstance(result["error"], str) and result["error"]
    assert json.loads(result["__mvi_text_output"]) == {
        "status": "completed", "execution": "client", "tools": [],
    }
