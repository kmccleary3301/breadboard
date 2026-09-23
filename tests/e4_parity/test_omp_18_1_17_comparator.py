from __future__ import annotations

from copy import deepcopy

from conformance.comparators.oh_my_pi_18_1_17 import compare, project_bb_trace, project_supplier_case


def _trace() -> dict:
    return {
        "requests": [{"body": {"messages": [{"role": "user", "content": "do"}], "tools": [{"function": {"name": "bash"}}]}}],
        "tool_calls": [{"id": "c1", "name": "bash", "arguments": {"command": "printf hi"}}],
        "results": [{"tool_call_id": "c1", "name": "bash", "output": "hi", "error": None}],
        "effects": {"marker.txt": "sha256:" + "a" * 64},
        "termination": "submitted",
        "stop_reason": "stop",
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
