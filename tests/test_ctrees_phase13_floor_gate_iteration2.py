from __future__ import annotations

import json

from agentic_coder_prototype.ctrees.phase13_floor_gate_iteration2 import (
    build_phase13_floor_gate_iteration2_summary,
)


def test_build_phase13_floor_gate_iteration2_summary_compares_v2_to_v1(tmp_path) -> None:
    base_row = {
        "task_id": "task-1",
        "grounded_completion": False,
        "verified_completion": False,
    }
    cand_v1 = {
        "candidate_a_runtime_v1_summary": {"grounded_completed": 0},
        "rows": [{"task_id": "task-1", "candidate_a_runtime_v1": dict(base_row)}],
    }
    det_v1 = {
        "deterministic_runtime_v1_summary": {"grounded_completed": 0},
        "rows": [{"task_id": "task-1", "deterministic_runtime_v1": dict(base_row)}],
    }
    cand_v2 = {
        "candidate_a_runtime_v2_summary": {"grounded_completed": 1},
        "rows": [{"task_id": "task-1", "candidate_a_runtime_v2": {**base_row, "grounded_completion": True}}],
    }
    det_v2 = {
        "deterministic_runtime_v2_summary": {"grounded_completed": 0},
        "rows": [{"task_id": "task-1", "deterministic_runtime_v2": dict(base_row)}],
    }
    paths = {}
    for name, payload in {"cand_v1": cand_v1, "det_v1": det_v1, "cand_v2": cand_v2, "det_v2": det_v2}.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path

    payload = build_phase13_floor_gate_iteration2_summary(
        candidate_a_v1_path=paths["cand_v1"],
        deterministic_v1_path=paths["det_v1"],
        candidate_a_v2_path=paths["cand_v2"],
        deterministic_v2_path=paths["det_v2"],
    )
    assert payload["counts"]["candidate_a_runtime_v2_vs_candidate_a_runtime_v1"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
    }
    assert payload["counts"]["candidate_a_runtime_v2_vs_deterministic_runtime_v2"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
    }
