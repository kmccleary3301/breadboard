from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Callable

import pytest
from breadboard.product.evidence.e4.run_lane import _comparator_entry

from conformance.comparators.oh_my_pi_18_1_17 import (
    OhMyPi18Comparator,
    compare,
    project_bb_trace,
    project_supplier_case,
)
from breadboard_engine.conformance.c4_chain import _load_comparator_callable


def _trace() -> dict:
    return {
        "runtime_inputs": {
            "cwd": "/workspace",
            "home": "/home/capture",
            "current_date": "2026-09-23",
            "package_dir": "/packages",
        },
        "requests": [{"body": {"messages": [{"role": "user", "content": "do"}], "tools": [{"function": {"name": "bash"}}]}}],
        "tool_calls": [{"id": "c1", "name": "bash", "arguments": {"command": "printf hi"}}],
        "results": [{"tool_call_id": "c1", "name": "bash", "output": "hi", "error": None}],
        "effects": {"marker.txt": "sha256:" + "a" * 64},
        "termination": "submitted",
        "stop_reason": "stop",
        "native_responses": [{"choices": [{"finish_reason": "stop"}]}],
        "request_count": 1,
    }


def test_projection_is_one_canonical_episode() -> None:
    episode = project_bb_trace(_trace())
    assert list(episode) == ["schema_version", "requests", "tool_calls", "results", "file_effects", "termination", "request_count"]
    assert episode["tool_calls"][0]["arguments"] == {"command": "printf hi"}
    assert episode["termination"] == {"kind": "submitted", "native_stop_reason": "stop"}


def test_equal_supplier_and_bb_episodes_pass() -> None:
    trace = _trace()
    report = compare({"capture": trace, "replay": deepcopy(trace)})
    assert report["ok"] is True
    assert report["failed"] == 0


def test_negative_gate_rejects_completion_order_mutation_and_effect_mutation() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["results"][0]["output"] = "different"
    replay["effects"]["marker.txt"] = None
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert report["failed"] >= 2

def test_capture_unavailable_stop_reason_is_not_self_exempt(tmp_path: Path) -> None:
    supplier_dir = tmp_path / "supplier"
    (supplier_dir / "receiver").mkdir(parents=True)
    supplier_trace = _trace()
    supplier_trace.pop("native_responses")
    supplier_trace["exit"] = {"kind": "Submitted", "native_stop_reason": None}
    (supplier_dir / "trace.json").write_text(json.dumps(supplier_trace), encoding="utf-8")
    replay = _trace()
    replay["exit"] = {"kind": "Submitted", "native_stop_reason": "stop"}
    report = compare({"capture": supplier_dir, "replay": replay})
    assert report["ok"] is False
    assert report["failed"] >= 1


def test_bb_trace_requires_native_responses_for_each_request() -> None:
    replay = _trace()
    replay.pop("native_responses")
    with pytest.raises(ValueError, match="native_responses"):
        project_bb_trace(replay)


def test_bb_trace_requires_declared_runtime_inputs() -> None:
    replay = _trace()
    replay.pop("runtime_inputs")
    with pytest.raises(ValueError, match="runtime_inputs"):
        project_bb_trace(replay)


@pytest.mark.parametrize("native_response", [{}, {"choices": [{}]}])
def test_bb_native_response_requires_finish_reason(native_response: dict) -> None:
    replay = _trace()
    replay["native_responses"] = [native_response]
    with pytest.raises(ValueError, match="finish_reason|malformed"):
        project_bb_trace(replay)


def test_comparator_rejects_empty_native_response_record() -> None:
    supplier = _trace()
    supplier.pop("native_responses")
    replay = _trace()
    replay["native_responses"] = [{}]
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "malformed" in report["errors"][0]


def test_comparator_rejects_extra_native_response_record() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["native_responses"] = [{"choices": [{"finish_reason": "stop"}]}] * 2
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "exactly one native_responses" in report["errors"][0]


def test_comparator_rejects_missing_native_response_record() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["native_responses"] = []
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "exactly one native_responses" in report["errors"][0]


def test_comparator_rejects_reordered_native_response_records() -> None:
    supplier = _trace()
    supplier["requests"] = [supplier["requests"][0], deepcopy(supplier["requests"][0])]
    supplier["native_responses"] = [
        {"choices": [{"finish_reason": "length"}]},
        {"choices": [{"finish_reason": "stop"}]},
    ]
    replay = deepcopy(supplier)
    replay["native_responses"] = [
        {"choices": [{"finish_reason": "stop"}]},
        {"choices": [{"finish_reason": "length"}]},
    ]
    replay["exit"] = {"kind": "Submitted", "native_stop_reason": "length"}
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert report["failed"] >= 1


def test_comparator_accepts_positional_native_response_pair() -> None:
    supplier = _trace()
    supplier["requests"] = [supplier["requests"][0], deepcopy(supplier["requests"][0])]
    supplier["native_responses"] = [
        {"choices": [{"finish_reason": "length"}]},
        {"choices": [{"finish_reason": "stop"}]},
    ]
    replay = deepcopy(supplier)
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is True


def test_bb_runtime_inputs_match_supplier_declaration() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["runtime_inputs"]["cwd"] = "/different"
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "runtime_inputs" in report["errors"][0]


def test_bb_cannot_self_declare_stop_reason_provenance() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["native_stop_reason_source"] = "capture_unavailable"
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "cannot declare" in report["errors"][0]


def _request(messages: list[dict[str, object]]) -> dict[str, object]:
    return {
        "model": "capture",
        "messages": messages,
        "tools": [],
        "max_completion_tokens": 2048,
        "stream": True,
    }


def test_supplier_and_bb_dedupe_cumulative_snapshots(tmp_path: Path) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "bash", "arguments": "{\"command\":\"printf x\"}"},
    }
    result = {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "bash",
        "content": "x",
    }
    messages = [
        {"role": "system", "content": "<workstation>\n- OS: linux 6.8.0-90-generic\n- Kernel: #91-Ubuntu SMP\n- Model: capture/capture\n</workstation>"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
    ]
    requests = [
        {"index": 0, "body": _request(messages)},
        {"index": 1, "body": _request([*messages, result])},
        {"index": 2, "body": _request([*messages, result])},
    ]
    supplier = tmp_path / "supplier"
    receiver = supplier / "receiver"
    receiver.mkdir(parents=True)
    (supplier / "trace.json").write_text(
        json.dumps({
            "case_id": "case",
            "effects": {"marker.txt": "sha256:" + "a" * 64},
            "native_responses": [{"choices": [{"finish_reason": "stop"}]}],
            "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
        }),
        encoding="utf-8",
    )
    (receiver / "http-transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in requests),
        encoding="utf-8",
    )
    bb_trace = {
        "case_id": "case",
        "requests": requests,
        "runtime_inputs": {
            "cwd": "/workspace",
            "home": "/home/capture",
            "current_date": "2026-09-23",
            "package_dir": "/packages",
        },
        "native_responses": [{"choices": [{"finish_reason": "stop"}]}] * 3,
        "effects": {"marker.txt": "sha256:" + "a" * 64},
        "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
    }

    supplier_episode = project_supplier_case(supplier)
    bb_episode = project_bb_trace(bb_trace)
    assert supplier_episode == bb_episode
    assert len(bb_episode["tool_calls"]) == 1
    assert len(bb_episode["results"]) == 1
    assert OhMyPi18Comparator()({"capture": supplier, "replay": bb_trace})["ok"] is True


def test_comparator_rejects_nonidentical_cumulative_tool_call() -> None:
    call = {"id": "call-1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    changed = {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    trace = {
        "runtime_inputs": {
            "cwd": "/workspace",
            "home": "/home/capture",
            "current_date": "2026-09-23",
            "package_dir": "/packages",
        },
        "requests": [
            {"body": _request([{"role": "assistant", "tool_calls": [call]}])},
            {"body": _request([{"role": "assistant", "tool_calls": [changed]}])},
        ],
        "native_responses": [{"choices": [{"finish_reason": "stop"}]}] * 2,
        "effects": {},
        "exit": {"kind": "Submitted", "native_stop_reason": "stop"},
    }
    with pytest.raises(ValueError, match="non-identical wire payload"):
        project_bb_trace(trace)


def test_omp_comparator_loads_through_lane_registry() -> None:
    root = Path(__file__).resolve().parents[2]
    entry = _comparator_entry(
        {
            "lane_id": "oh_my_pi_18_1_17_replay",
            "compare": {"comparator": "oh_my_pi_18_1_17_trace_v1"},
        },
        root / "conformance/comparators/registry.json",
    )
    loaded = _load_comparator_callable(entry)
    assert loaded is compare
    assert entry["comparator_id"] == "oh_my_pi_18_1_17_trace_v1"


WORKSTATION_FIXTURES = Path(__file__).parent / "fixtures" / "omp_18_1_17_workstation"


def _first_request_pair() -> tuple[dict, dict]:
    """Job-1203 normal first requests; the BB one has R2 and R3 applied by hand."""
    supplier = json.loads((WORKSTATION_FIXTURES / "supplier-normal-req0.json").read_text(encoding="utf-8"))
    bb = json.loads((WORKSTATION_FIXTURES / "bb-normal-req0-r2-r3-hand-applied.json").read_text(encoding="utf-8"))
    assert bb["fixture"].startswith("HAND-EDITED")
    return supplier["body"], bb["body"]


def _first_request_traces() -> dict[str, dict]:
    traces: dict[str, dict] = {}
    for side, body in zip(("capture", "replay"), _first_request_pair()):
        traces[side] = _trace()
        traces[side]["requests"] = [{"body": body}]
    return traces


def test_workstation_grammar_equates_job_1203_first_requests() -> None:
    traces = _first_request_traces()
    assert traces["capture"]["requests"] != traces["replay"]["requests"]
    report = compare(traces)
    assert report["ok"] is True, report


def _swap(old: str, new: str) -> Callable[[str], str]:
    def mutate(content: str) -> str:
        assert content.count(old) == 1
        return content.replace(old, new)

    return mutate


def _drop_model_line(content: str) -> str:
    stripped, count = re.subn(r"(?m)^- Model: [^\n]*\n", "", content)
    assert count == 1
    return stripped


@pytest.mark.parametrize("side", ["capture", "replay"])
@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        pytest.param(_swap("\n- Distro: Linux\n", "\n- Distro: Linux\n- OS: linux 6.8.0-90-generic\n"), "exactly one '- OS:'", id="duplicate-os"),
        pytest.param(_drop_model_line, "exactly one '- Model:'", id="missing-model"),
        pytest.param(_swap("/capture\n</workstation>", "/other\n</workstation>"), "Model id does not equal", id="model-id-not-body-model"),
        pytest.param(_swap("-generic\n- Distro:", "-generic extra\n- Distro:"), "'- OS:' value does not match", id="text-after-release"),
        pytest.param(_swap("- Arch: x64\n", "- Arch: arm64\n"), None, id="non-value-byte"),
        pytest.param(_swap("- OS: linux ", "- OS: darwin "), None, id="platform"),
    ],
)
def test_workstation_grammar_mutations_fail(side: str, mutate: Callable[[str], str], error: str | None) -> None:
    traces = _first_request_traces()
    system = traces[side]["requests"][0]["body"]["messages"][0]
    assert system["role"] == "system"
    system["content"] = mutate(system["content"])
    report = compare(traces)
    assert report["ok"] is False
    if error is None:
        assert [item["assertion_id"] for item in report["assertions"] if item["status"] == "failed"] == ["episode.requests_equal"]
    else:
        assert error in report["errors"][0]


# Job-1203 malformed_tool_call effects, each in its side's recorded shape. The
# supplier probes every declared path (null: no regular file); BB records the
# paths its measured workspace changed.
_RECOVERED = "sha256:7072f186429aabd403ce0eac668a22372b34c499b9d65ea9a835bb87402a8caf"
_EMPTY = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_SUPPLIER_1203_EFFECTS = {"malformed_recovered.txt": {"bytes": 14, "sha256": _RECOVERED}, "must_not_exist.txt": None}
_BB_1203_EFFECTS = {"malformed_recovered.txt": {"bytes": 14, "exists": True, "sha256": _RECOVERED}}


def _effect_traces(capture_effects: dict, replay_effects: dict) -> dict[str, dict]:
    traces = {"capture": _trace(), "replay": _trace()}
    traces["capture"]["effects"] = deepcopy(capture_effects)
    traces["replay"]["effects"] = deepcopy(replay_effects)
    return traces


@pytest.mark.parametrize(
    ("supplier_effects", "bb_effects"),
    [
        pytest.param(_SUPPLIER_1203_EFFECTS, _BB_1203_EFFECTS, id="malformed-1203"),
        pytest.param({"cutoff_marker.txt": None}, {}, id="length-1203"),
        pytest.param({"must_not_exist.txt": None}, {"must_not_exist.txt": {"exists": False}}, id="bb-removed"),
    ],
)
def test_absent_effect_shapes_compare_equal(supplier_effects: dict, bb_effects: dict) -> None:
    report = compare(_effect_traces(supplier_effects, bb_effects))
    assert report["ok"] is True, report


@pytest.mark.parametrize("swap", [False, True], ids=["supplier-capture", "supplier-replay"])
@pytest.mark.parametrize(
    "bb_effects",
    [
        pytest.param({**_BB_1203_EFFECTS, "must_not_exist.txt": {"bytes": 0, "exists": True, "sha256": _EMPTY}}, id="null-vs-content"),
        pytest.param({}, id="changed-vs-absent"),
        pytest.param({"malformed_recovered.txt": {"exists": False}}, id="changed-vs-removed"),
    ],
)
def test_real_effect_differences_still_fail(bb_effects: dict, swap: bool) -> None:
    sides = (bb_effects, _SUPPLIER_1203_EFFECTS) if swap else (_SUPPLIER_1203_EFFECTS, bb_effects)
    report = compare(_effect_traces(*sides))
    assert report["ok"] is False
    assert [item["assertion_id"] for item in report["assertions"] if item["status"] == "failed"] == ["episode.file_effects_equal"]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        pytest.param({"exists": True, "bytes": 0}, "has no sha256 digest", id="present-without-digest"),
        pytest.param({"exists": False, "sha256": _EMPTY}, "has extra fields", id="removed-with-extra-fields"),
        pytest.param(0, "neither a digest nor absent", id="scalar"),
    ],
)
def test_malformed_effect_is_not_read_as_absent(value: object, error: str) -> None:
    traces = _effect_traces(_SUPPLIER_1203_EFFECTS, {**_BB_1203_EFFECTS, "must_not_exist.txt": value})
    report = compare(traces)
    assert report["ok"] is False
    assert error in report["errors"][0]


# Job-1203 stream_fragments_broken. The supplier side is the sealed capture,
# copied verbatim: trace.json (sha256 0c62b9bf…2b086) and
# receiver/http-transcript.jsonl (sha256 e9990125…0db5b), one broken:true row
# with 4 events and no finish chunk. BB produced no trace for this case, so its
# side is HAND-BUILT per the item-6 product design (see the fixture hand_edits).
BROKEN_FIXTURES = Path(__file__).parent / "fixtures" / "omp_18_1_17_broken_stream"


def _broken_bb_trace() -> dict:
    fixture = json.loads((BROKEN_FIXTURES / "bb-trace-hand-built.json").read_text(encoding="utf-8"))
    assert fixture["fixture"].startswith("HAND-BUILT")
    return fixture["trace"]


def _broken_supplier_case(tmp_path: Path, mutate: Callable[[dict], None]) -> Path:
    """Copy the sealed supplier case with its one transcript row changed by ``mutate``."""
    source = BROKEN_FIXTURES / "supplier"
    case = tmp_path / "supplier"
    (case / "receiver").mkdir(parents=True)
    (case / "trace.json").write_bytes((source / "trace.json").read_bytes())
    row = json.loads((source / "receiver" / "http-transcript.jsonl").read_text(encoding="utf-8"))
    mutate(row)
    (case / "receiver" / "http-transcript.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    return case


def _failed(report: dict) -> list[str]:
    return [item["assertion_id"] for item in report["assertions"] if item["status"] == "failed"]


def test_bb_truncated_stream_equals_supplier_broken_case() -> None:
    supplier = BROKEN_FIXTURES / "supplier"
    bb = _broken_bb_trace()
    # Supplier exit_code 0 maps to submitted; BB records Submitted and, having
    # received no finish chunk, no native stop reason.
    termination = {"kind": "submitted", "native_stop_reason": None}
    assert project_supplier_case(supplier)["termination"] == termination
    assert project_bb_trace(bb)["termination"] == termination
    report = compare({"capture": supplier, "replay": bb})
    assert report["ok"] is True, report
    assert report["passed"] == len(report["assertions"])
    tokens = next(item for item in report["assertions"] if item["assertion_id"] == "native_response_terminations_equal")
    assert tokens["expected"] == [{"stream_termination": "stream_truncated"}]


def _with_finish_chunk(row: dict) -> None:
    """The row as the kit sends a stream that is not cut: finish chunk, then [DONE]."""
    last = row["events"][-1]
    row["events"].append({**last, "choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}]})
    row["broken"] = False


def test_bb_truncation_does_not_equal_supplier_finish_reason(tmp_path: Path) -> None:
    report = compare({"capture": _broken_supplier_case(tmp_path, _with_finish_chunk), "replay": _broken_bb_trace()})
    assert report["ok"] is False
    assert _failed(report) == ["episode.termination_equal", "native_response_terminations_equal"]


def test_transport_error_does_not_equal_stream_truncated() -> None:
    bb = _broken_bb_trace()
    bb["native_responses"][0]["stream_termination"]["reason"] = "transport_error"
    report = compare({"capture": BROKEN_FIXTURES / "supplier", "replay": bb})
    assert report["ok"] is False
    assert _failed(report) == ["native_response_terminations_equal"]


@pytest.mark.parametrize(
    "extra",
    [{"finish_reason": "tool_calls"}, {"choices": [{"index": 0, "finish_reason": "tool_calls"}]}],
    ids=["finish_reason", "choices"],
)
def test_record_with_stream_termination_and_finish_reason_fails_closed(extra: dict) -> None:
    bb = _broken_bb_trace()
    bb["native_responses"][0].update(extra)
    report = compare({"capture": BROKEN_FIXTURES / "supplier", "replay": bb})
    assert report["ok"] is False
    assert "beside stream_termination" in report["errors"][0]


def test_supplier_row_not_broken_without_finish_chunk_fails_closed(tmp_path: Path) -> None:
    supplier = _broken_supplier_case(tmp_path, lambda row: row.update(broken=False))
    report = compare({"capture": supplier, "replay": _broken_bb_trace()})
    assert report["ok"] is False
    assert "omp-done-without-finish-reason" in report["errors"][0]


def test_reminder_normalizes_with_declared_date_on_both_sides() -> None:
    capture = _trace()
    capture["requests"][0]["body"]["messages"] = [
        {
            "role": "user",
            "content": (
                "<system-reminder>\n"
                "Today: 2026-09-23; current working directory: '/workspace'. "
                "Do not repeat this information in your reply.\n"
                "</system-reminder>\n"
                "Hello"
            ),
        }
    ]
    replay = deepcopy(capture)
    report = compare({"capture": capture, "replay": replay})
    assert report["ok"] is True
    assert report["failed"] == 0
    assert report["normalizations"] == [
        {"side": "supplier", "rule": "current_date_reminder", "count": 1},
        {"side": "supplier", "rule": "workspace_root", "count": 1},
        {"side": "supplier", "rule": "bash_wall_time", "count": 0},
        {"side": "bb", "rule": "current_date_reminder", "count": 1},
        {"side": "bb", "rule": "workspace_root", "count": 1},
        {"side": "bb", "rule": "bash_wall_time", "count": 0},
    ]


def test_reminder_with_different_date_than_runtime_input_does_not_normalize_and_diverges() -> None:
    capture = _trace()
    capture["requests"][0]["body"]["messages"] = [
        {
            "role": "user",
            "content": (
                "<system-reminder>\n"
                "Today: 2026-09-23; current working directory: '/workspace'. "
                "Do not repeat this information in your reply.\n"
                "</system-reminder>\n"
                "Hello"
            ),
        }
    ]
    replay = deepcopy(capture)
    # Replay has a different date in the reminder text than declared runtime_inputs (2026-09-23)
    replay["requests"][0]["body"]["messages"] = [
        {
            "role": "user",
            "content": (
                "<system-reminder>\n"
                "Today: 2026-09-24; current working directory: '/workspace'. "
                "Do not repeat this information in your reply.\n"
                "</system-reminder>\n"
                "Hello"
            ),
        }
    ]
    report = compare({"capture": capture, "replay": replay})
    assert report["ok"] is False
    assert report["failed"] >= 1
    # Supplier normalized because date matched declared runtime_inputs, but BB replay did NOT normalize
    assert report["normalizations"] == [
        {"side": "supplier", "rule": "current_date_reminder", "count": 1},
        {"side": "supplier", "rule": "workspace_root", "count": 1},
        {"side": "supplier", "rule": "bash_wall_time", "count": 0},
        {"side": "bb", "rule": "current_date_reminder", "count": 0},
        {"side": "bb", "rule": "workspace_root", "count": 0},
        {"side": "bb", "rule": "bash_wall_time", "count": 0},
    ]


def _bash_result(wall_time: str) -> str:
    return f"(no output)\n\nWall time: {wall_time} seconds\nTimeout clamped to 30s (requested 300s; global tools.maxTimeout ceiling 30s)."


def _wall_time_trace(wall_time: str) -> dict:
    trace = _trace()
    trace["requests"].append({"body": {"messages": [
        {"role": "user", "content": "do"},
        {"role": "tool", "tool_call_id": "c1", "content": _bash_result(wall_time)},
    ], "tools": [{"function": {"name": "bash"}}]}})
    trace["native_responses"].insert(0, {"choices": [{"finish_reason": "tool_calls"}]})
    trace["results"][0]["output"] = _bash_result(wall_time)
    trace["request_count"] = 2
    return trace


def test_bash_wall_time_line_is_counted_host_timing() -> None:
    report = compare({"capture": _wall_time_trace("0.09"), "replay": _wall_time_trace("0.02")})
    assert report["ok"] is True, [item for item in report["assertions"] if item["status"] == "failed"]
    counts = {(item["side"], item["rule"]): item["count"] for item in report["normalizations"]}
    assert counts[("supplier", "bash_wall_time")] == counts[("bb", "bash_wall_time")] == 2


@pytest.mark.parametrize("mutate", [
    lambda text: text.replace("Wall time: 0.02 seconds", "Wall time: 0.020 seconds"),
    lambda text: text.replace("Wall time: 0.02 seconds", "Wall time: 0.02 seconds (slow)"),
    lambda text: text.replace("(no output)", "(no outputs)"),
])
def test_bash_wall_time_does_not_hide_other_result_bytes(mutate: Callable[[str], str]) -> None:
    replay = _wall_time_trace("0.02")
    replay["results"][0]["output"] = mutate(replay["results"][0]["output"])
    message = replay["requests"][1]["body"]["messages"][1]
    message["content"] = mutate(message["content"])
    report = compare({"capture": _wall_time_trace("0.09"), "replay": replay})
    assert report["ok"] is False


def _guard_supplier(tmp_path: Path, guard: object) -> Path:
    supplier_dir = tmp_path / "supplier"
    supplier_dir.mkdir()
    trace = _trace()
    for name in ("termination", "stop_reason", "runtime_inputs"):
        trace.pop(name)
    trace.update({"exit_code": 0, "timed_out": False, "request_guard": guard})
    trace["native_responses"] = [{"choices": [{"finish_reason": "tool_calls"}]}]
    (supplier_dir / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    return supplier_dir


def _limit_replay() -> dict:
    replay = _trace()
    for name in ("termination", "stop_reason"):
        replay.pop(name)
    replay["exit"] = {"kind": "RequestLimitExceeded", "native_stop_reason": "tool_calls"}
    replay["native_responses"] = [{"choices": [{"finish_reason": "tool_calls"}]}]
    return replay


def test_stopped_supplier_request_guard_is_the_request_limit(tmp_path: Path) -> None:
    guard = {"limit": 1, "issued": 1, "stopped": True, "reason": "capture request cap"}
    report = compare({"capture": _guard_supplier(tmp_path, guard), "replay": _limit_replay()})
    assert report["ok"] is True, [item for item in report["assertions"] if item["status"] == "failed"]


def test_unstopped_supplier_request_guard_is_not_a_request_limit(tmp_path: Path) -> None:
    guard = {"limit": 8, "issued": 1, "stopped": False, "reason": None}
    report = compare({"capture": _guard_supplier(tmp_path, guard), "replay": _limit_replay()})
    assert report["ok"] is False
    assert any(
        item["assertion_id"] == "episode.termination_equal" and item["status"] == "failed"
        for item in report["assertions"]
    )


@pytest.mark.parametrize("guard", [
    {"limit": 8, "issued": 7, "stopped": True, "reason": "capture request cap"},
    {"limit": 8, "issued": 8, "stopped": "yes", "reason": "capture request cap"},
    ["stopped"],
])
def test_malformed_supplier_request_guard_fails_closed(tmp_path: Path, guard: object) -> None:
    report = compare({"capture": _guard_supplier(tmp_path, guard), "replay": _limit_replay()})
    assert report["ok"] is False
    assert any("request_guard" in error for error in report["errors"])
