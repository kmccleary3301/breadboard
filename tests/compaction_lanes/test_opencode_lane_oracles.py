"""OpenCode lane oracles behavioral regressions."""
from __future__ import annotations
from breadboard_engine.compaction_lanes import opencode_stock as _lane_opencode_stock
from breadboard_engine.compaction_lanes import opencode_stock as _lane_opencode_stock
import pytest
from .opencode_test_support import record

@pytest.mark.parametrize("part,summary,reject", [
    ("text", False, False), ("compaction", False, True), ("text", True, True),
])
def test_stock_oracle_selection_uses_native_control_boundaries_only(part, summary, reject):
    from breadboard_engine.compaction_lanes.opencode_comparison import stock_control_flow_ties
    evidence = {"session": {"id": "root"}, "messages": [
        {"info": {"id": "user", "role": "user", "time": {"created": 100}}, "parts": [{"type": part}]},
        {"info": {"id": "assistant", "role": "assistant", "summary": summary, "time": {"created": 100}},
         "parts": [{"type": "text", "text": "Native content"}]},
    ]}
    assert bool(stock_control_flow_ties(evidence)) is reject
    evidence["messages"][1]["info"]["time"]["created"] = 101
    assert stock_control_flow_ties(evidence) == []

def test_stock_oracle_all_five_native_ties_fail_without_running_bb(monkeypatch, tmp_path):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0,
                "native_evidence": {"session": {"id": "root"}, "messages": [
                    {"info": {"id": "u", "role": "user", "time": {"created": 100}},
                     "parts": [{"type": "compaction"}]},
                    {"info": {"id": "a", "role": "assistant", "summary": True,
                              "finish": "stop", "time": {"created": 100}}, "parts": []},
                ]}}, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock"] * 5
    assert report["stock_oracle_selection"]["selected_attempt"] is None
    assert len(report["stock_oracle_selection"]["attempts"]) == 5
    assert not report["passed"]

def test_stock_oracle_never_retries_a_tie_free_byte_difference(monkeypatch, tmp_path):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        evidence = {"session": {"id": "root"}, "messages": [
            {"info": {"id": "u", "role": "user", "time": {"created": 100}}, "parts": []},
            {"info": {"id": "a", "role": "assistant", "summary": True, "finish": "stop",
                      "time": {"created": 101}}, "parts": []}]}
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0, "native_evidence": evidence}, [
                    record({"model": "stock" if side == "stock" else "different", "input": []})]
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock", "bb"]
    assert report["stock_oracle_selection"]["selected_attempt"] == 1
    assert len(report["stock_oracle_selection"]["attempts"]) == 1
    assert len(report["diffs"]) == 1
    assert report["admitted_created_time_tie_groups_per_request"] == [
        {"request": 0, "tie_groups": 0}]
    assert not report["passed"]

@pytest.mark.parametrize("outcome", ["match", "bytes", "counts", "error", "ordinary_tie"])
def test_bb_sidecar_selector_rejects_only_native_control_ties(monkeypatch, tmp_path, outcome):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        bb_attempt = called.count("bb")
        control_tied = side == "bb" and bb_attempt == 1
        ordinary_tied = side == "bb" and bb_attempt == 2 and outcome == "ordinary_tie"
        evidence = {"session": {"id": "root"}, "messages": [
            {"info": {"id": "u", "role": "user", "time": {"created": 100}},
             "parts": [{"type": "compaction" if control_tied else "text"}]},
            {"info": {"id": "a", "role": "assistant", "summary": not ordinary_tied,
                      "finish": "stop", "time": {"created": 100 if control_tied or ordinary_tied else 101}},
             "parts": []},
        ]}
        result = {"session_id": "root", "unconsumed_responses": 0,
                  "unconsumed_auxiliary_responses": 0, "native_evidence": evidence}
        if side == "bb" and bb_attempt == 2 and outcome == "error":
            result["error"] = "Original tie-free production failure"
        rows = [record({"model": "different" if side == "bb" and bb_attempt == 2
                        and outcome in {"bytes", "ordinary_tie"} else "same", "input": []})]
        if side == "bb" and bb_attempt == 2 and outcome == "counts":
            rows.append(rows[0])
        return result, rows
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock", "bb", "bb"]
    selection = report["bb_sidecar_oracle_selection"]
    assert selection["selected_attempt"] == 2
    assert selection["attempts"][0]["control_flow_ties"]
    assert selection["attempts"][1]["control_flow_ties"] == []
    assert report["passed"] is (outcome == "match")
    assert report["stock_oracle_selection"]["bb_runs"] == 2
    assert selection["bytes_or_counts_can_reject"] is False

def test_bb_sidecar_all_five_native_ties_fail(monkeypatch, tmp_path):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0,
                "native_evidence": {"session": {"id": "root"}, "messages": [
                    {"info": {"id": "u", "role": "user", "time": {"created": 100}},
                     "parts": [{"type": "compaction"}]},
                    {"info": {"id": "a", "role": "assistant", "summary": True,
                              "finish": "stop", "time": {"created": 100 if side == "bb" else 101}},
                     "parts": []},
                ]}}, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock"] + ["bb"] * 5
    selection = report["bb_sidecar_oracle_selection"]
    assert selection["selected_attempt"] is None
    assert len(selection["attempts"]) == 5
    assert all(attempt["control_flow_ties"] for attempt in selection["attempts"])
    assert not report["passed"]
    assert any("All five BB sidecar" in item.get("error", "") for item in report["unresolved"])

def test_bb_sidecar_missing_evidence_never_retries(monkeypatch, tmp_path):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        result = {"session_id": "root", "unconsumed_responses": 0,
                  "unconsumed_auxiliary_responses": 0}
        if side == "stock":
            result["native_evidence"] = {"session": {"id": "root"}, "messages": [
                {"info": {"id": "a", "role": "assistant", "summary": True,
                          "finish": "stop", "time": {"created": 101}}, "parts": []}]}
        return result, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock", "bb"]
    selection = report["bb_sidecar_oracle_selection"]
    assert selection["selected_attempt"] is None
    assert len(selection["attempts"]) == 1
    assert not report["passed"]
    assert any("BB sidecar oracle omitted" in item.get("error", "") for item in report["unresolved"])

def test_bb_oracle_archives_only_the_closed_owned_session_store(tmp_path):
    from pathlib import Path
    from breadboard.product.runtime.session_store import session_directory
    from breadboard_engine.compaction_lanes.opencode_lane import archive_bb_session
    workspace, out = tmp_path / "workspace", tmp_path / "attempt"
    owned = session_directory(workspace) / "root" / "stock-opencode"
    owned.mkdir(parents=True)
    out.mkdir()
    (owned / "opencode.db").write_bytes(b"retained-native-evidence")
    unrelated = session_directory(workspace) / "other"
    unrelated.mkdir()
    (unrelated / "user-file").write_text("Untouched")
    archived = archive_bb_session(workspace, out, "root")
    assert Path(archived, "stock-opencode", "opencode.db").read_bytes() == b"retained-native-evidence"
    assert not owned.parent.exists()
    assert (unrelated / "user-file").read_text() == "Untouched"
    assert archive_bb_session(workspace, out, "absent") is None
    with pytest.raises(ValueError, match="portable"):
        archive_bb_session(workspace, out, "../other")

@pytest.mark.parametrize("observation_fails", [False, True])
def test_foreground_timeout_keeps_original_failure_and_reads_live_stock_evidence(
    observation_fails, monkeypatch, tmp_path,
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    live = {"active": False, "closed": False}
    calls = []
    def api(method, path, body=None):
        assert live["active"]
        calls.append((method, path))
        if method == "POST":
            raise TimeoutError("original foreground failure")
        if observation_fails:
            raise OSError("original source observation failure")
        return {
            "/session/root/children": [],
            "/session/root": {"id": "root"},
            "/session/root/message": [],
            "/session/status": {"root": {"type": "retry"}},
        }[path]
    @contextmanager
    def server(*args, **kwargs):
        live["active"] = True
        try:
            yield api
        finally:
            live["active"] = False
            live["closed"] = True
    def imported(command, **kwargs):
        assert command[1] == "import"
        return SimpleNamespace(returncode=0, stdout="Imported session: root", stderr="")
    monkeypatch.setattr(_lane_opencode_stock, "stock_server", server)
    monkeypatch.setattr(_lane_opencode_stock, "resolve_stock", lambda: tmp_path / "pinned-stock")
    monkeypatch.setattr(lane.subprocess, "run", imported)
    result = _lane_opencode_stock.run_stock("opencode", "final_threshold", tmp_path, tmp_path / "runtime",
                            "http://unused", [{"info": {"id": "root"}, "messages": []}])
    assert "TimeoutError: original foreground failure" in result["error"]
    assert result["failure_kind"] == "foreground_post_timeout"
    assert calls[0] == ("POST", "/session/root/message")
    assert live == {"active": False, "closed": True}
    if observation_fails:
        assert result["native_evidence_error"] == "OSError: original source observation failure"
        assert "native_evidence" not in result
    else:
        assert result["native_evidence"] == {"session": {"id": "root"}, "messages": [], "children": []}
        assert result["native_status"] == {"type": "retry"}
        assert all(method == "GET" for method, _ in calls[1:])

@pytest.mark.parametrize("tied", [False, True])
def test_stock_timeout_attempt_retries_only_a_genuine_native_control_boundary_tie(
    tied, monkeypatch, tmp_path,
):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        first = not called
        called.append(side)
        evidence = {"session": {"id": "root"}, "messages": [
            {"info": {"id": "u", "role": "user", "time": {"created": 10}}, "parts": []},
            {"info": {"id": "a", "role": "assistant", "summary": True, "finish": "stop",
                      "time": {"created": 10 if first and tied else 11}}, "parts": []}]}
        result = {"session_id": "root", "native_evidence": evidence,
                  "unconsumed_responses": 0, "unconsumed_auxiliary_responses": 0}
        if first:
            result.update(error="TimeoutError: original foreground failure",
                          failure_kind="foreground_post_timeout")
        return result, [record({"input": [], "prompt_cache_key": "root"})]
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    attempts = report["stock_oracle_selection"]["attempts"]
    assert attempts[0]["error"] == "TimeoutError: original foreground failure"
    if tied:
        assert called == ["stock", "stock", "bb"]
        assert report["stock_oracle_selection"]["selected_attempt"] == 2
        assert len(attempts) == 2
        assert attempts[0]["control_flow_ties"]
        assert not attempts[1]["control_flow_ties"]
    else:
        assert called == ["stock"]
        assert report["stock_oracle_selection"]["selected_attempt"] == 1
        assert len(attempts) == 1
        assert not attempts[0]["control_flow_ties"]
        assert not report["passed"]
        assert any("original foreground failure" in item.get("error", "")
                   for item in report["unresolved"])

def test_stock_oracle_missing_native_evidence_fails_without_retry_or_bb(monkeypatch, tmp_path):
    from breadboard_engine.compaction_lanes import opencode_lane as lane
    monkeypatch.setattr(_lane_opencode_stock, "create_empty_stock_sessions",
                        lambda *args, **kwargs: [{"info": {"id": "root"}, "messages": []}])
    called = []
    def capture(side, *args, **kwargs):
        called.append(side)
        return {"session_id": "root", "unconsumed_responses": 0,
                "unconsumed_auxiliary_responses": 0}, []
    monkeypatch.setattr(lane, "capture_side", capture)
    report = lane.run_scenario("opencode", "final_threshold", tmp_path)
    assert called == ["stock"]
    selection = report["stock_oracle_selection"]
    assert selection["selected_attempt"] is None
    assert selection["bb_runs"] == 0
    assert len(selection["attempts"]) == 1
    assert selection["attempts"][0]["error"] == "Stock oracle omitted genuine native message evidence"
    assert not report["passed"]
