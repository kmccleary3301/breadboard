from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from conformance.comparators.pi_coding_agent_0_73_1 import (
    PiCodingAgent0731Comparator,
    project_bb_trace,
    project_supplier_case,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPLIER_CASE = Path("/tmp/pipkt/packet/cases/normal_workspace_episode")
NATIVE_CONFIG = REPO_ROOT / "config" / "e4_targets" / "pi" / "0.73.1" / "native-config.json"
BB_RUNTIME_INPUTS = {
    "cwd": "/leases/pi-0731/workspace-abc",
    "home": "/leases/pi-0731/home-abc",
    "current_date": "2027-04-05",
    "package_dir": "/srv/pi-0731",
}


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


def _bb_trace_from_packet() -> dict:
    trace, requests = _packet_trace_and_requests()
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
    return {
        **trace,
        "role": "replay",
        "requests": bb_requests,
        "runtime_inputs": dict(BB_RUNTIME_INPUTS),
    }


def _report(bb_trace: dict) -> dict:
    return PiCodingAgent0731Comparator()({"capture": {"case_dir": str(SUPPLIER_CASE)}, "replay": bb_trace})


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
