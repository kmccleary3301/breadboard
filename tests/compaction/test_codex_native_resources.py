"""Prebuilt and missing-resource contracts for the shared native workspace."""
import json
from types import SimpleNamespace

import pytest

from breadboard_engine.execution import codex_native, codex_search


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
