from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from breadboard.product.evidence.e4.run_lane import _comparator_entry
from breadboard_engine.conformance.c4_chain import _load_comparator_callable
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

def test_capture_unavailable_stop_reason_is_explicit_and_tamper_gated(tmp_path: Path) -> None:
    supplier_dir = tmp_path / "supplier"
    (supplier_dir / "receiver").mkdir(parents=True)
    supplier_trace = _trace()
    supplier_trace["exit"] = {"kind": "Submitted", "native_stop_reason": None}
    (supplier_dir / "trace.json").write_text(json.dumps(supplier_trace), encoding="utf-8")
    replay = _trace()
    replay["native_responses"] = [{"choices": [{"finish_reason": "stop"}]}]
    replay["exit"] = {"kind": "Submitted", "native_stop_reason": "stop"}
    report = compare({"capture": supplier_dir, "replay": replay})
    assert report["ok"] is True
    replay["exit"]["native_stop_reason"] = "length"
    report = compare({"capture": supplier_dir, "replay": replay})
    assert report["ok"] is False
    assert "recorded response bytes" in report["errors"][0]


def test_bb_cannot_self_declare_stop_reason_provenance() -> None:
    supplier = _trace()
    replay = deepcopy(supplier)
    replay["native_stop_reason_source"] = "capture_unavailable"
    report = compare({"capture": supplier, "replay": replay})
    assert report["ok"] is False
    assert "cannot declare" in report["errors"][0]


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
