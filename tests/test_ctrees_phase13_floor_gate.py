from __future__ import annotations

import json

from agentic_coder_prototype.ctrees.phase13_floor_gate import build_phase13_floor_gate_summary


def test_build_phase13_floor_gate_summary_uses_grounded_then_verified(tmp_path) -> None:
    candidate = {
        "summary": {"total": 1, "grounded_completed": 1},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/candidate",
                "completion_summary": {"completed": True, "reason": "finish_reason:stop", "steps_taken": 5},
                "completion_gate": {"outcome": "verified_completion", "satisfied": True},
                "progress_watchdog": {"triggered": False},
                "grounded_summary": {
                    "grounded": {"grounded_completion": True, "ungrounded_stop": False},
                    "controller_metrics": {
                        "verified_completion": True,
                        "first_write_step": 2,
                        "first_verify_step": 4,
                        "no_progress_streak_max": 1,
                    },
                    "classification": {"failure_family": "grounded_completion"},
                },
                "phase11_live_protocol": {
                    "family": "candidate_a_runtime_v1",
                    "support_strategy": "candidate_a",
                    "runtime_contract": {"phase_policies": [{}, {}, {}, {}, {}]},
                },
            }
        ],
    }
    deterministic = {
        "summary": {"total": 1, "grounded_completed": 0},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/deterministic",
                "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 8},
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "progress_watchdog": {"triggered": True},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {
                        "verified_completion": False,
                        "first_write_step": None,
                        "first_verify_step": 1,
                        "no_progress_streak_max": 8,
                    },
                    "classification": {"failure_family": "early_verify_no_edit"},
                },
                "phase11_live_protocol": {
                    "family": "deterministic_runtime_v1",
                    "support_strategy": "deterministic_reranker",
                    "runtime_contract": {"phase_policies": [{}, {}, {}, {}, {}]},
                },
            }
        ],
    }
    candidate_path = tmp_path / "candidate.json"
    deterministic_path = tmp_path / "deterministic.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    deterministic_path.write_text(json.dumps(deterministic), encoding="utf-8")

    payload = build_phase13_floor_gate_summary(
        candidate_a_runtime_path=candidate_path,
        deterministic_runtime_path=deterministic_path,
    )

    assert payload["counts"]["candidate_a_runtime_v1_vs_deterministic_runtime_v1"] == {
        "wins": 1,
        "losses": 0,
        "ties": 0,
    }
    row = payload["rows"][0]["candidate_a_runtime_v1"]
    assert row["protocol_family"] == "candidate_a_runtime_v1"
    assert row["runtime_phase_count"] == 5


def test_build_phase13_floor_gate_summary_supports_custom_labels(tmp_path) -> None:
    payload = {
        "summary": {"total": 1, "grounded_completed": 0},
        "results": [
            {
                "id": "task-1",
                "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 8},
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "progress_watchdog": {"triggered": True},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": False, "first_write_step": None, "first_verify_step": 1, "no_progress_streak_max": 8},
                    "classification": {"failure_family": "early_verify_no_edit"},
                },
                "phase11_live_protocol": {
                    "family": "candidate_a_runtime_v2",
                    "support_strategy": "candidate_a",
                    "runtime_contract": {"phase_policies": [{}, {}, {}, {}, {}]},
                },
            }
        ],
    }
    p1 = tmp_path / "a.json"
    p2 = tmp_path / "b.json"
    import json
    p1.write_text(json.dumps(payload), encoding="utf-8")
    p2.write_text(json.dumps(payload), encoding="utf-8")
    out = build_phase13_floor_gate_summary(
        candidate_a_runtime_path=p1,
        deterministic_runtime_path=p2,
        candidate_label="candidate_a_runtime_v2",
        deterministic_label="deterministic_runtime_v2",
    )
    assert "candidate_a_runtime_v2_summary" in out
    assert "deterministic_runtime_v2_summary" in out
    assert "candidate_a_runtime_v2_vs_deterministic_runtime_v2" in out["counts"]
