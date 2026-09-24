from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from conformance.comparators.pi_coding_agent_0_73_1 import (
    EFFECT_CONTENT_UTF8_MAX_BYTES,
    PiCodingAgent0731Comparator,
    project_bb_trace,
    project_supplier_case,
)
from breadboard.rl.harness.pi_native_tools import dispatch_native_tools
from breadboard.rl.harness.runners.pi_semantics import PiSemanticsState
from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPLIER_CASE = REPO_ROOT / "tests" / "e4_parity" / "fixtures" / "pi_0_73_1_supplier_case"
SUPPLIER_MANIFEST = SUPPLIER_CASE / "manifest.json"
NATIVE_CONFIG = REPO_ROOT / "config" / "e4_targets" / "pi" / "0.73.1" / "native-config.json"
BB_RUNTIME_INPUTS = {
    "cwd": "/leases/pi-0731/workspace-abc",
    "home": "/leases/pi-0731/home-abc",
    "current_date": "2027-04-05",
    "package_dir": "/srv/pi-0731",
}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_supplier_fixture_manifest_matches_pinned_packet_members() -> None:
    manifest = json.loads(SUPPLIER_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["source_packet_sha256"] == (
        "sha256:c18adae060f9785753991f87d5acc1a03de45845e8afa0a0ed67c5812766a3f1"
    )
    for entry in manifest["files"]:
        path = SUPPLIER_CASE / entry["path"]
        assert path.is_file()
        assert _sha256(path) == entry["sha256"]
        assert entry["member_path"].startswith("cases/normal_workspace_episode/")


def _packet_trace_and_requests() -> tuple[dict, list[dict]]:
    trace = json.loads((SUPPLIER_CASE / "trace.json").read_text(encoding="utf-8"))
    requests = [
        json.loads(line)["body"]
        for line in (SUPPLIER_CASE / "receiver" / "http-transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return trace, requests


def _inverse_runtime_values(value):
    if isinstance(value, str):
        value = value.replace("/capture/workspace", BB_RUNTIME_INPUTS["cwd"])
        value = value.replace("/capture/home", BB_RUNTIME_INPUTS["home"])
        value = value.replace("2026-09-23", BB_RUNTIME_INPUTS["current_date"])
        for suffix in ("README.md", "docs", "examples"):
            value = value.replace("/opt/pi/app/" + suffix, BB_RUNTIME_INPUTS["package_dir"] + "/" + suffix)
        return value
    if isinstance(value, list):
        return [_inverse_runtime_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _inverse_runtime_values(item) for key, item in value.items()}
    return value


def _bb_requests(requests: list[dict]) -> list[dict]:
    config = json.loads(NATIVE_CONFIG.read_text(encoding="utf-8"))
    advertisement = config["advertisement"]
    narrowed_read_description = advertisement["tools"]["read"]["description"]
    remove_exact = advertisement["prompt"]["remove_exact"]
    bb_requests = []
    for request in requests:
        request = _inverse_runtime_values(deepcopy(request))
        for tool in request.get("tools", []):
            function = tool.get("function", {})
            if function.get("name") == "read":
                function["description"] = narrowed_read_description
        for message in request["messages"]:
            if message.get("role") == "system":
                for exact in remove_exact:
                    assert message["content"].count(exact) == 1
                    message["content"] = message["content"].replace(exact, "", 1)
        bb_requests.append(request)
    return bb_requests


def _bb_trace_from_packet() -> dict:
    trace, requests = _packet_trace_and_requests()
    return {
        **trace,
        "role": "replay",
        "requests": _bb_requests(requests),
        "runtime_inputs": dict(BB_RUNTIME_INPUTS),
    }

def _report(bb_trace: dict) -> dict:
    return PiCodingAgent0731Comparator()({"capture": {"case_dir": str(SUPPLIER_CASE)}, "replay": bb_trace})
def _request_limit_case(tmp_path: Path) -> tuple[Path, dict]:
    trace, requests = _packet_trace_and_requests()
    trace = deepcopy(trace)
    trace["case_id"] = "request_cap_eight"
    trace["request_count"] = len(requests)
    trace["stream_fn_issued"] = len(requests) + 1
    last_assistant = next(message for message in reversed(trace["messages"]) if message.get("role") == "assistant")
    last_assistant["stopReason"] = "error"
    last_assistant["errorMessage"] = "PI_CAPTURE_REQUEST_LIMIT: eight model requests issued"
    last_assistant["content"] = [{"type": "text", "text": ""}]
    case = tmp_path / "request-limit-case"
    (case / "receiver").mkdir(parents=True, exist_ok=True)
    (case / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (case / "scenario.json").write_text(json.dumps({"steps": [{} for _ in requests]}), encoding="utf-8")
    (case / "receiver" / "http-transcript.jsonl").write_text(
        "\n".join(json.dumps({"body": request}) for request in requests) + "\n",
        encoding="utf-8",
    )
    return case, trace


def _replay_request_limit_bb_trace(case: Path, supplier_trace: dict) -> dict:
    requests = [
        json.loads(line)["body"]
        for line in (case / "receiver" / "http-transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    bb_requests = _bb_requests(requests)
    work_dir = case / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    task = supplier_trace.get("task", "execute task")
    state = PiSemanticsState(
        task=task,
        request_cap=len(requests),
        model_id="gpt-4o-mini",
        provider="openai",
        api="openai-completions",
    )
    assistant_messages = [
        msg for msg in supplier_trace.get("messages", [])
        if msg.get("role") == "assistant"
    ]
    for i in range(len(requests)):
        msg = assistant_messages[i]
        tool_calls = []
        for b in msg.get("content", []):
            if b.get("type") == "toolCall":
                tool_calls.append(
                    NativeToolCall(b["id"], b["name"], json.dumps(b.get("arguments", {})))
                )
        finish = "tool_calls" if tool_calls else msg.get("stopReason", "stop")
        resp = NativeProviderResponse(
            "binding", "request", "response", "gpt-4o-mini", None, finish, tuple(tool_calls)
        )
        assert state.begin_query() is None
        prep = state.prepare_response(resp)
        if prep.calls:
            raw = dispatch_native_tools(
                [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in prep.calls],
                cwd=work_dir,
            )
            state.commit_tool_results(prep.calls, raw)

    terminal = state.begin_query()
    assert terminal is not None
    assert state.exit_status == "RequestLimitExceeded"
    assert state.stream_fn_issued == len(requests) + 1

    expected = project_supplier_case(case)
    return state.to_trace(
        requests=bb_requests,
        runtime_inputs=BB_RUNTIME_INPUTS,
        effects=expected["effects"],
    )


def test_request_limit_cause_matches_real_shaped_pair(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": bb_trace}
    )
    assert report["passed"] is True
    assert [item for item in report["normalizations"] if item["rule"] == "request_limit_cause"] == [
        {"side": "supplier", "rule": "request_limit_cause", "count": 1},
        {"side": "bb", "rule": "request_limit_cause", "count": 1},
    ]


def test_request_limit_cause_requires_declared_count(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    bb_trace["request_count"] -= 1
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": bb_trace}
    )
    assert report["passed"] is False


def test_request_limit_cause_requires_supplier_literal(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    supplier_trace["messages"][-1]["errorMessage"] = "different failure"
    case.joinpath("trace.json").write_text(json.dumps(supplier_trace), encoding="utf-8")
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": bb_trace}
    )
    assert report["passed"] is False


def test_request_limit_counterfeit_missing_refused_attempt_fails(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    counterfeit = deepcopy(bb_trace)
    counterfeit["messages"] = [
        msg for msg in counterfeit["messages"]
        if not (isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("stopReason") == "error")
    ]
    counterfeit["stream_fn_issued"] = len(case.joinpath("receiver", "http-transcript.jsonl").read_text(encoding="utf-8").splitlines())
    counterfeit["request_count"] = counterfeit["stream_fn_issued"]
    counterfeit["termination"] = {"kind": "RequestLimitExceeded", "native_stop_reason": "error"}
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": counterfeit}
    )
    assert report["passed"] is False


def test_request_limit_counterfeit_unrelated_error_kind_fails(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    counterfeit = deepcopy(bb_trace)
    counterfeit["termination"] = {"kind": "error", "native_stop_reason": "error"}
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": counterfeit}
    )
    assert report["passed"] is False


def test_request_limit_counterfeit_unrelated_terminal_error_message_fails(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    counterfeit = deepcopy(bb_trace)
    counterfeit["messages"][-1]["errorMessage"] = "Unrelated model/transport error"
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": counterfeit}
    )
    assert report["passed"] is False


def test_request_limit_counterfeit_candidate_claims_supplier_role_fails(tmp_path: Path) -> None:
    case, supplier_trace = _request_limit_case(tmp_path)
    bb_trace = _replay_request_limit_bb_trace(case, supplier_trace)
    counterfeit = deepcopy(bb_trace)
    counterfeit["messages"][-1]["errorMessage"] = "PI_CAPTURE_REQUEST_LIMIT: fake"
    counterfeit["termination"] = {"kind": "error", "native_stop_reason": "error"}
    counterfeit["role"] = "supplier"
    report = PiCodingAgent0731Comparator()(
        {"capture": {"case_dir": str(case)}, "replay": counterfeit}
    )
    assert report["passed"] is False

def test_real_packet_inverse_runtime_and_advertisement_rules_match() -> None:
    """A BB-shaped trace made from a real packet is a comparator unit fixture."""
    expected = project_supplier_case(SUPPLIER_CASE)
    observed = project_bb_trace(_bb_trace_from_packet())
    assert expected == observed

    report = _report(_bb_trace_from_packet())
    assert report["passed"] is True
    assert all(item["count"] > 0 for item in report["normalizations"] if item["side"] == "supplier" and item["rule"] in {
        "workspace_root",
        "current_date_system_line",
        "package_dir_documentation_path",
        "advertisement_read_description",
        "advertisement_prompt_removal",
    })
    assert all(item["count"] == 0 for item in report["normalizations"] if item["side"] == "bb" and item["rule"] in {
        "advertisement_read_description",
        "advertisement_prompt_removal",
    })


def test_path_outside_declared_root_stays_raw_and_fails() -> None:
    bb_trace = _bb_trace_from_packet()
    system = bb_trace["requests"][0]["messages"][0]["content"]
    bb_trace["requests"][0]["messages"][0]["content"] = system.replace(
        BB_RUNTIME_INPUTS["cwd"] + "/AGENTS.md", "/outside/AGENTS.md", 1
    )
    report = _report(bb_trace)
    assert report["passed"] is False
    assert "/outside/AGENTS.md" in report["assertions"][0]["observed"]["requests"][0]["messages"][0]["content"]


def test_changed_relative_path_stays_raw_and_fails() -> None:
    bb_trace = _bb_trace_from_packet()
    bb_trace["messages"][1]["content"][1]["arguments"]["path"] = "different.txt"
    report = _report(bb_trace)
    assert report["passed"] is False


def test_missing_runtime_inputs_fails_closed() -> None:
    bb_trace = _bb_trace_from_packet()
    del bb_trace["runtime_inputs"]
    with pytest.raises(ValueError, match="runtime_inputs"):
        project_bb_trace(bb_trace)


def test_supplier_read_description_with_wrong_sha_fails_closed(tmp_path: Path) -> None:
    trace, requests = _packet_trace_and_requests()
    requests[0]["tools"][0]["function"]["description"] = "tampered native description"
    case = tmp_path / "supplier-case"
    (case / "receiver").mkdir(parents=True)
    (case / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (case / "receiver" / "http-transcript.jsonl").write_text(json.dumps({"body": requests[0]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="read description sha256 mismatch"):
        project_supplier_case(case)


def test_supplier_request_tools_must_contain_exactly_one_read(tmp_path: Path) -> None:
    trace, requests = _packet_trace_and_requests()
    requests[0]["tools"] = []
    case = tmp_path / "supplier-case"
    (case / "receiver").mkdir(parents=True)
    (case / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (case / "receiver" / "http-transcript.jsonl").write_text(json.dumps({"body": requests[0]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one read"):
        project_supplier_case(case)


@pytest.mark.parametrize("declaration", ["top_level", "runtime_inputs"])
def test_supplier_declared_date_must_match_capture_constant(tmp_path: Path, declaration: str) -> None:
    trace, requests = _packet_trace_and_requests()
    if declaration == "top_level":
        trace["current_date"] = "2099-01-01"
    else:
        trace["runtime_inputs"] = {"current_date": "2099-01-01"}
    case = tmp_path / "supplier-case"
    (case / "receiver").mkdir(parents=True)
    (case / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (case / "receiver" / "http-transcript.jsonl").write_text(json.dumps({"body": requests[0]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="capture constant 2026-09-23"):
        project_supplier_case(case)


def test_unexpected_date_elsewhere_stays_raw_and_fails() -> None:
    bb_trace = _bb_trace_from_packet()
    bb_trace["requests"][0]["messages"][0]["content"] += "\nUnrelated date: 2027-04-05"
    report = _report(bb_trace)
    assert report["passed"] is False
    assert "Unrelated date: 2027-04-05" in report["assertions"][0]["observed"]["requests"][0]["messages"][0]["content"]


def test_supplier_advertisement_prompt_must_occur_exactly_once(tmp_path: Path) -> None:
    trace, requests = _packet_trace_and_requests()
    requests[0]["messages"][0]["content"] = requests[0]["messages"][0]["content"].replace(
        "\n\nIn addition to the tools above, you may have access to other custom tools depending on the project.", "", 1
    )
    case = tmp_path / "supplier-case"
    (case / "receiver").mkdir(parents=True)
    (case / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (case / "receiver" / "http-transcript.jsonl").write_text(json.dumps({"body": requests[0]}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="occur exactly once"):
        project_supplier_case(case)


def test_comparator_rejects_non_supplier_case(tmp_path: Path) -> None:
    trace, _ = _packet_trace_and_requests()
    trace["role"] = "replay"
    (tmp_path / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    with pytest.raises(ValueError, match="role=supplier"):
        project_supplier_case(tmp_path)


def test_oversize_effect_content_is_ignored_but_digest_is_compared() -> None:
    supplier, requests = _packet_trace_and_requests()
    supplier = deepcopy(supplier)
    supplier["role"] = "supplier"
    oversized = {
        "exists": True,
        "bytes": EFFECT_CONTENT_UTF8_MAX_BYTES + 1,
        "sha256": "sha256:" + ("a" * 64),
        "content_utf8": "supplier content",
    }
    supplier.setdefault("effects", {})["oversize.txt"] = oversized
    replay = _bb_trace_from_packet()
    replay["effects"]["oversize.txt"] = {**oversized, "content_utf8": "different content"}
    report = PiCodingAgent0731Comparator()(
        {
            "capture": {**supplier, "requests": requests},
            "replay": replay,
        }
    )
    assert report["passed"] is True
    replay["effects"]["oversize.txt"]["sha256"] = "sha256:" + ("b" * 64)
    assert PiCodingAgent0731Comparator()(
        {
            "capture": {**supplier, "requests": requests},
            "replay": replay,
        }
    )["passed"] is False
