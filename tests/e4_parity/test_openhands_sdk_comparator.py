from __future__ import annotations

from copy import deepcopy
import json
import hashlib
from pathlib import Path
import shutil

import pytest

from conformance.comparators.openhands_sdk import (
    _Normalizer,
    _response_projection,
    compare_cases,
    project_bb_trace,
    project_supplier_case,
)

FIXTURES = Path(__file__).parent / "fixtures" / "openhands_sdk"
CAPTURED_CASE = Path(__file__).parents[1] / "fixtures" / "openhands_rerun2" / "captures" / "OH-01-normal-file-effect"
CANDIDATE_ID = "56351706-00f7-47c5-98d0-7145da8af641"
CASES = tuple(sorted(path for path in FIXTURES.iterdir() if path.is_dir()))


@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_supplier_projection_self_replays(case_dir: Path) -> None:
    supplier = project_supplier_case(case_dir)
    replay = _as_bb_trace(supplier)
    projected = project_bb_trace(deepcopy(replay))
    assert projected["file_effects"] == supplier["file_effects"]


def _as_bb_trace(supplier: dict) -> dict:
    replay = deepcopy(supplier)
    replay["file_effects"] = {
        path: (
            {"exists": False}
            if digest is None
            else {"exists": True, "bytes": 0, "sha256": digest}
        )
        for path, digest in supplier["file_effects"].items()
    }
    return replay

def _captured_replay() -> dict:
    replay = _as_bb_trace(project_supplier_case(CAPTURED_CASE))
    replay["conversation_id"] = CANDIDATE_ID
    for request in replay["requests"]:
        request["body"]["prompt_cache_key"] = CANDIDATE_ID
    return replay


def test_captured_oh01_bound_conversation_passes() -> None:
    report = compare_cases(CAPTURED_CASE, _captured_replay())
    assert report["ok"] is True, report
    assert next(item for item in report["assertions"] if item["assertion_id"].endswith(".requests.prompt_cache_key_bound"))["status"] == "passed"


def test_captured_supplier_and_candidate_with_distinct_declared_roots_compare() -> None:
    replay = _captured_replay()
    candidate_root = "/tmp/episode/workspace/workspace-abc/repository"
    for request in replay["requests"]:
        for tool in request["body"]["tools"]:
            function = tool["function"]
            if "<WORKSPACE>" in function.get("description", ""):
                function["description"] = function["description"].replace("<WORKSPACE>", candidate_root)
    report = compare_cases(CAPTURED_CASE, replay)
    assert report["ok"] is True, report


def test_registered_comparator_rejects_foreign_candidate_cache_key() -> None:
    replay = _captured_replay()
    replay["requests"][0]["body"]["prompt_cache_key"] = "123e4567-e89b-12d3-a456-426614174000"
    report = compare_cases(CAPTURED_CASE, replay)
    assert report["ok"] is False
    assert next(item for item in report["assertions"] if item["assertion_id"].endswith(".requests.prompt_cache_key_bound"))["status"] == "failed"


def test_registered_comparator_rejects_foreign_supplier_cache_key(tmp_path: Path) -> None:
    case = tmp_path / CAPTURED_CASE.name
    shutil.copytree(CAPTURED_CASE, case)
    stderr = case / "supplier.stderr"
    source_id = "f77abdcc-8ac9-460c-8d9b-e35e5b884c1a"
    stderr.write_text(stderr.read_text(encoding="utf-8").replace(source_id, "123e4567-e89b-12d3-a456-426614174000"), encoding="utf-8")
    report = compare_cases(case, _captured_replay())
    assert report["ok"] is False
    assert next(item for item in report["assertions"] if item["assertion_id"].endswith(".requests.prompt_cache_key_bound"))["status"] == "failed"


@pytest.mark.parametrize("side", ["candidate_missing", "supplier_missing", "supplier_ambiguous"])
def test_registered_comparator_requires_unambiguous_conversation_id(side: str, tmp_path: Path) -> None:
    replay = _captured_replay()
    case = tmp_path / CAPTURED_CASE.name
    shutil.copytree(CAPTURED_CASE, case)
    if side == "candidate_missing":
        del replay["conversation_id"]
    else:
        stderr = case / "supplier.stderr"
        record = stderr.read_text(encoding="utf-8")
        if side == "supplier_missing":
            stderr.unlink()
        else:
            stderr.write_text(record + "\n" + record, encoding="utf-8")
    report = compare_cases(case, replay)
    assert report["ok"] is False
    assert next(item for item in report["assertions"] if item["assertion_id"].endswith(".requests.prompt_cache_key_bound"))["status"] == "failed"


def _replay(case: str) -> tuple[Path, dict]:
    path = FIXTURES / case
    supplier = project_supplier_case(path)
    return path, _as_bb_trace(supplier)




def _drop_invalid_followup(trace: dict) -> None:
    trace["tool_calls"] = [call for call in trace["tool_calls"] if call["arguments"].get("command") != "create" or call["arguments"].get("path") != "after.txt"]


def _execute_cutoff_call(trace: dict) -> None:
    trace["file_effects"]["must-not-exist.txt"] = {
        "exists": True,
        "bytes": 0,
        "sha256": "sha256:" + "a" * 64,
    }


def _drop_corrective_nudge(trace: dict) -> None:
    trace["requests"] = trace["requests"][:1]
    trace["request_count"] = 1


def _add_ninth_request(trace: dict) -> None:
    extra = deepcopy(trace["requests"][-1])
    extra["index"] = 8
    trace["requests"].append(extra)
    trace["request_count"] += 1


def _change_tool_order(trace: dict) -> None:
    trace["tool_calls"][0], trace["tool_calls"][1] = trace["tool_calls"][1], trace["tool_calls"][0]


def _change_security_risk_placement(trace: dict) -> None:
    trace["tool_calls"][0]["security_risk"] = "LOW"


def _change_file_effect(trace: dict) -> None:
    path = next(iter(trace["file_effects"]))
    if trace["file_effects"][path]["exists"]:
        trace["file_effects"][path] = {
            "exists": True,
            "bytes": 0,
            "sha256": "sha256:" + "b" * 64,
        }




def _change_termination(trace: dict) -> None:
    trace["termination"]["kind"] = "finished"


@pytest.mark.parametrize(
    ("case", "mutation"),
    [
        ("OH-02-invalid-call-continues", _drop_invalid_followup),
        ("OH-03-finish-cutoff", _execute_cutoff_call),
        ("OH-06-response-classification", _drop_corrective_nudge),
        ("OH-01-normal-file-effect", _add_ninth_request),
        ("OH-04-persistent-pty-reset", _change_tool_order),
        ("OH-01-normal-file-effect", _change_security_risk_placement),
        ("OH-01-normal-file-effect", _change_file_effect),
        ("OH-05-iteration-budget", _change_termination),
    ],
    ids=["per_call_validation", "finish_cutoff", "corrective_nudge", "extra_request", "tool_order", "security_risk", "file_effect", "termination"],
)
def test_negative_mutations_fail(case: str, mutation) -> None:
    path, replay = _replay(case)
    mutation(replay)
    report = compare_cases(path, replay)
    assert report["ok"] is False
    assert report["failed"] >= 1
    assert any("first difference" in item["detail"] for item in report["assertions"] if item["status"] == "failed")


def test_undeclared_placeholder_is_rejected() -> None:
    path, replay = _replay("OH-01-normal-file-effect")
    replay["requests"][0]["body"]["messages"][0]["content"] = "<TIMESTAMP>"
    replay["normalizations"] = []
    report = compare_cases(path, replay)
    assert report["ok"] is False
    assert "normalization" in report["errors"][0]




def test_malformed_measured_effect_is_rejected() -> None:
    trace = _replay("OH-01-normal-file-effect")[1]
    trace["file_effects"]["malformed.txt"] = {"exists": True}
    with pytest.raises(ValueError, match="requires"):
        project_bb_trace(trace)


def test_bb_scalar_effect_is_rejected() -> None:
    trace = _replay("OH-01-normal-file-effect")[1]
    trace["file_effects"]["scalar.txt"] = "sha256:" + ("a" * 64)
    with pytest.raises(ValueError, match="must be an object"):
        project_bb_trace(trace)


def test_bb_supplier_wrapper_effect_shape_is_rejected() -> None:
    trace = _replay("OH-01-normal-file-effect")[1]
    trace["file_effects"] = {
        "files": {
            "wrapped.txt": {
                "exists": True,
                "bytes": 1,
                "sha256": "sha256:" + ("a" * 64),
            }
        }
    }
    with pytest.raises(ValueError, match="boolean exists"):
        project_bb_trace(trace)


def test_supplier_legacy_scalar_effect_remains_accepted(tmp_path: Path) -> None:
    source = FIXTURES / "OH-01-normal-file-effect"
    case = tmp_path / source.name
    shutil.copytree(source, case)
    trace_path = case / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["effects"]["files"]["legacy.txt"] = "sha256:" + ("a" * 64)
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    projected = project_supplier_case(case)
    assert projected["file_effects"]["legacy.txt"] == "sha256:" + ("a" * 64)



def _volatile_trace() -> dict:
    return {
        "schema_version": "bb.e4.openhands-sdk-trace.v1",
        "case_id": "volatile-case",
        "requests": [{
            "index": 0,
            "body": {
                "id": "oh-capture-response-abc",
                "call_id": "call-abc",
                "event_id": "123e4567-e89b-12d3-a456-426614174000",
                "timestamp": "2026-09-23T12:34:56Z",
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "file_editor",
                        "description": "Your current working directory is: /opt/openhands/workspace",
                    },
                }],
            },
        }],
        "tool_calls": [{
            "tool_name": "terminal",
            "arguments": {"id": "call-abc"},
            "security_risk": "LOW",
        }],
        "observations": [{
            "event_kind": "ObservationEvent",
            "tool_name": "terminal",
            "is_error": False,
            "result": {"timestamp": "2026-09-23T12:34:56Z"},
        }],
        "file_effects": {},
        "termination": {"kind": "finished", "native_stop_reason": "stop"},
        "request_count": 1,
    }


def test_raw_volatile_trace_derives_all_static_normalizations() -> None:
    projected = project_bb_trace(_volatile_trace())
    assert projected["normalizations"] == [
        "call_id:<CALL_ID>",
        "event_uuid:<EVENT_UUID>",
        "response_id:<RESPONSE_ID>",
        "timestamp:<TIMESTAMP>",
        "workspace:<WORKSPACE>",
    ]


def test_undeclared_literal_placeholder_remains_rejected() -> None:
    trace = _volatile_trace()
    trace["requests"][0]["body"]["id"] = "<RESPONSE_ID>"
    with pytest.raises(ValueError, match="normalization"):
        project_bb_trace(trace)


def test_declared_workspace_root_positive_exact_prefix_normalized() -> None:
    trace = _volatile_trace()
    trace["tool_calls"][0]["arguments"]["path"] = "/opt/openhands/workspace/sub/file.txt"
    trace["tool_calls"][0]["arguments"]["exact_dir"] = "/opt/openhands/workspace"
    trace["tool_calls"][0]["arguments"]["unrelated"] = "/other/directory/file.txt"
    trace["requests"][0]["body"]["tools"][0]["function"]["description"] = (
        "File editor.\nYour current working directory is: /opt/openhands/workspace\nUse absolute paths."
    )
    trace["tool_calls"][0]["arguments"]["sibling"] = "/opt/openhands/workspace-old/file.txt"
    projected = project_bb_trace(trace)
    assert projected["tool_calls"][0]["arguments"]["path"] == "<WORKSPACE>/sub/file.txt"
    assert projected["tool_calls"][0]["arguments"]["exact_dir"] == "<WORKSPACE>"
    assert projected["tool_calls"][0]["arguments"]["unrelated"] == "/other/directory/file.txt"
    assert projected["requests"][0]["body"]["tools"][0]["function"]["description"] == (
        "File editor.\nYour current working directory is: <WORKSPACE>\nUse absolute paths."
    )
    assert projected["tool_calls"][0]["arguments"]["sibling"] == "/opt/openhands/workspace-old/file.txt"
    assert "workspace:<WORKSPACE>" in projected["normalizations"]


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda t: t["requests"][0]["body"].pop("tools"), "declared working directory is absent"),
        (lambda t: t["requests"][0]["body"]["tools"][0]["function"].update({"description": "no cwd here"}), "declared working directory is absent"),
        (
            lambda t: t["requests"].append({
                "index": 1,
                "body": {
                    "tools": [{
                        "type": "function",
                        "function": {
                            "name": "file_editor",
                            "description": "Your current working directory is: /different/workspace",
                        },
                    }],
                },
            }),
            "declared working directory differs across requests",
        ),
        (
            lambda t: t["requests"][0]["body"]["tools"].append({
                "type": "function",
                "function": {
                    "name": "other_tool",
                    "description": "Your current working directory is: /conflicting/workspace",
                },
            }),
            "ambiguous declared working directory",
        ),
        (lambda t: t.update({"requests": []}), "declared working directory is absent"),
    ],
    ids=["tools_missing", "cwd_missing", "differs_across_requests", "ambiguous_in_request", "empty_requests"],
)
def test_declared_workspace_root_fails_closed(mutation, match: str) -> None:
    trace = _volatile_trace()
    mutation(trace)
    with pytest.raises(ValueError, match=match):
        project_bb_trace(trace)


def test_response_projection_applied_to_bb_requests() -> None:
    trace = _volatile_trace()
    trace["requests"][0]["response"] = {
        "id": "oh-capture-resp-1",
        "created": 1234567890,
        "model": "gpt-4o-mini",
        "object": "chat.completion",
        "usage": {"total_tokens": 10},
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None},
        }],
    }
    projected = project_bb_trace(trace)
    response = projected["requests"][0].get("response")
    assert response == {
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None},
        }]
    }


def test_declared_workspace_root_embedded_in_observation_text() -> None:
    trace = _volatile_trace()
    trace["observations"].append({
        "event_kind": "ObservationEvent",
        "tool_name": "file_editor",
        "is_error": False,
        "result": {
            "content": [{
                "type": "text",
                "text": "File created successfully at: /opt/openhands/workspace/marker.txt",
            }]
        },
    })
    projected = project_bb_trace(trace)
    obs_text = projected["observations"][1]["result"]["content"][0]["text"]
    assert obs_text == "File created successfully at: <WORKSPACE>/marker.txt"


def test_declared_workspace_root_boundary_workspace2() -> None:
    trace = _volatile_trace()
    trace["observations"].append({
        "event_kind": "ObservationEvent",
        "tool_name": "terminal",
        "is_error": False,
        "result": {
            "content": [{
                "type": "text",
                "text": "Workspace is /opt/openhands/workspace2/test.txt vs /opt/openhands/workspace/test.txt",
            }]
        },
    })
    projected = project_bb_trace(trace)
    obs_text = projected["observations"][1]["result"]["content"][0]["text"]
    assert obs_text == "Workspace is /opt/openhands/workspace2/test.txt vs <WORKSPACE>/test.txt"


def test_file_effects_content_normalized_before_digesting(tmp_path: Path) -> None:
    case = tmp_path / CAPTURED_CASE.name
    shutil.copytree(CAPTURED_CASE, case)
    supp_file = case / "workspace" / "pty-after-reset.txt"
    supp_file.parent.mkdir(parents=True, exist_ok=True)
    supp_file.write_text("unset|/opt/openhands/case/workspace\n", encoding="utf-8")
    supp_digest = "sha256:" + hashlib.sha256(b"unset|/opt/openhands/case/workspace\n").hexdigest()
    case_trace = json.loads((case / "trace.json").read_text(encoding="utf-8"))
    case_trace["effects"]["files"]["pty-after-reset.txt"] = {
        "bytes": len("unset|/opt/openhands/case/workspace\n"),
        "exists": True,
        "sha256": supp_digest,
    }
    (case / "trace.json").write_text(json.dumps(case_trace), encoding="utf-8")

    replay = _captured_replay()
    bb_root = "/tmp/episode/workspace/workspace-abc/repository"
    bb_content = f"unset|{bb_root}\n"
    bb_digest = "sha256:" + hashlib.sha256(bb_content.encode("utf-8")).hexdigest()
    for request in replay["requests"]:
        for tool in request["body"]["tools"]:
            desc = tool["function"].get("description", "")
            if "<WORKSPACE>" in desc:
                tool["function"]["description"] = desc.replace("<WORKSPACE>", bb_root)
    replay["file_effects"]["pty-after-reset.txt"] = {
        "bytes": len(bb_content),
        "content_utf8": bb_content,
        "exists": True,
        "sha256": bb_digest,
    }
    report = compare_cases(case, replay)
    fe_assertion = next(item for item in report["assertions"] if item["assertion_id"].endswith(".file_effects_equal"))
    assert fe_assertion["status"] == "passed", fe_assertion


def test_file_effects_genuine_non_root_difference_still_fails(tmp_path: Path) -> None:
    case = tmp_path / CAPTURED_CASE.name
    shutil.copytree(CAPTURED_CASE, case)
    supp_file = case / "workspace" / "pty-after-reset.txt"
    supp_file.parent.mkdir(parents=True, exist_ok=True)
    supp_file.write_text("unset|/opt/openhands/case/workspace\n", encoding="utf-8")
    supp_digest = "sha256:" + hashlib.sha256(b"unset|/opt/openhands/case/workspace\n").hexdigest()
    case_trace = json.loads((case / "trace.json").read_text(encoding="utf-8"))
    case_trace["effects"]["files"]["pty-after-reset.txt"] = {
        "bytes": len("unset|/opt/openhands/case/workspace\n"),
        "exists": True,
        "sha256": supp_digest,
    }
    (case / "trace.json").write_text(json.dumps(case_trace), encoding="utf-8")

    replay = _captured_replay()
    bb_root = "/tmp/episode/workspace/workspace-abc/repository"
    bb_content = f"genuine_diff|{bb_root}\n"
    bb_digest = "sha256:" + hashlib.sha256(bb_content.encode("utf-8")).hexdigest()
    for request in replay["requests"]:
        for tool in request["body"]["tools"]:
            desc = tool["function"].get("description", "")
            if "<WORKSPACE>" in desc:
                tool["function"]["description"] = desc.replace("<WORKSPACE>", bb_root)
    replay["file_effects"]["pty-after-reset.txt"] = {
        "bytes": len(bb_content),
        "content_utf8": bb_content,
        "exists": True,
        "sha256": bb_digest,
    }
    report = compare_cases(case, replay)
    fe_assertion = next(item for item in report["assertions"] if item["assertion_id"].endswith(".file_effects_equal"))
    assert fe_assertion["status"] == "failed"


def test_response_projection_symmetric() -> None:
    raw_response = {
        "id": "oh-capture-response-abc123",
        "created": 1727289600,
        "model": "gpt-4o-mini",
        "object": "chat.completion",
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_12345678",
                    "type": "function",
                    "function": {
                        "name": "file_editor",
                        "arguments": '{"command":"create","path":"/opt/openhands/workspace/test.txt"}',
                    },
                }],
            },
        }],
    }
    trace = _volatile_trace()
    trace["requests"][0]["response"] = deepcopy(raw_response)
    projected_bb = project_bb_trace(trace)
    bb_resp = projected_bb["requests"][0]["response"]

    normalizer = _Normalizer("/opt/openhands/workspace")
    supplier_resp = _response_projection(raw_response, normalizer)
    assert bb_resp == supplier_resp
    assert "usage" not in bb_resp
    assert "id" not in bb_resp
    assert bb_resp["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"command":"create","path":"<WORKSPACE>/test.txt"}'


def test_declared_workspace_root_embedded_in_observation_text_bracket_lookahead() -> None:
    trace = _volatile_trace()
    trace["observations"].append({
        "event_kind": "ObservationEvent",
        "tool_name": "terminal",
        "is_error": False,
        "result": {
            "content": [{
                "type": "text",
                "text": "[Current working directory: /opt/openhands/workspace]",
            }]
        },
    })
    projected = project_bb_trace(trace)
    obs_text = projected["observations"][1]["result"]["content"][0]["text"]
    assert obs_text == "[Current working directory: <WORKSPACE>]"


def test_file_effects_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    case = tmp_path / CAPTURED_CASE.name
    shutil.copytree(CAPTURED_CASE, case)
    supp_file = case / "workspace" / "pty-after-reset.txt"
    supp_file.parent.mkdir(parents=True, exist_ok=True)
    supp_file.write_text("unset|/opt/openhands/case/workspace\n", encoding="utf-8")
    supp_digest = "sha256:" + hashlib.sha256(b"unset|/opt/openhands/case/workspace\n").hexdigest()
    case_trace = json.loads((case / "trace.json").read_text(encoding="utf-8"))
    case_trace["effects"]["files"]["pty-after-reset.txt"] = {
        "bytes": len("unset|/opt/openhands/case/workspace\n"),
        "exists": True,
        "sha256": supp_digest,
    }
    (case / "trace.json").write_text(json.dumps(case_trace), encoding="utf-8")

    replay = _captured_replay()
    bb_root = "/tmp/episode/workspace/workspace-abc/repository"
    bb_content = f"unset|{bb_root}\n"
    for request in replay["requests"]:
        for tool in request["body"]["tools"]:
            desc = tool["function"].get("description", "")
            if "<WORKSPACE>" in desc:
                tool["function"]["description"] = desc.replace("<WORKSPACE>", bb_root)

    # 1. Supplier content mismatch: workspace file content does not match recorded digest
    wrong_supp_digest = "sha256:" + ("a" * 64)
    case_trace["effects"]["files"]["pty-after-reset.txt"]["sha256"] = wrong_supp_digest
    (case / "trace.json").write_text(json.dumps(case_trace), encoding="utf-8")
    replay["file_effects"]["pty-after-reset.txt"] = {
        "bytes": len(bb_content),
        "content_utf8": bb_content,
        "exists": True,
        "sha256": "sha256:" + hashlib.sha256(bb_content.encode("utf-8")).hexdigest(),
    }
    report_supp = compare_cases(case, replay)
    assert report_supp["ok"] is False
    assert any("does not match workspace bytes digest" in err for err in report_supp["errors"])

    # 2. BB content mismatch: content_utf8 does not match recorded sha256
    case_trace["effects"]["files"]["pty-after-reset.txt"]["sha256"] = supp_digest
    (case / "trace.json").write_text(json.dumps(case_trace), encoding="utf-8")
    replay["file_effects"]["pty-after-reset.txt"]["sha256"] = "sha256:" + ("b" * 64)
    report_bb = compare_cases(case, replay)
    assert report_bb["ok"] is False
    assert any("does not match content_utf8 digest" in err for err in report_bb["errors"])
