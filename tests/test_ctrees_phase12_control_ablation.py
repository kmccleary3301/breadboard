from __future__ import annotations

from agentic_coder_prototype.ctrees.phase12_control_ablation import (
    build_phase12_anchor_control_ablation,
    build_phase12_calibration_control_ablation,
    build_phase12_iteration2_anchor_ablation,
    build_phase12_iteration2_calibration_ablation,
)


def test_build_phase12_calibration_control_ablation_uses_grounded_comparison(tmp_path) -> None:
    practical = {
        "summary": {"total": 1, "grounded_completed": 0},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/task-1",
                "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 12},
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "progress_watchdog": {"triggered": True},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": False, "no_progress_streak_max": 12},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                },
            }
        ],
    }
    deterministic = practical
    candidate = {
        "summary": {"total": 1, "grounded_completed": 1},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/task-1",
                "completion_summary": {"completed": True, "reason": "finish_reason:stop", "steps_taken": 6},
                "completion_gate": {"outcome": "grounded_complete", "satisfied": True},
                "progress_watchdog": {"triggered": False},
                "grounded_summary": {
                    "grounded": {"grounded_completion": True, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": True, "no_progress_streak_max": 0},
                    "classification": {"failure_family": ""},
                },
            }
        ],
    }
    practical_path = tmp_path / "practical.json"
    deterministic_path = tmp_path / "deterministic.json"
    candidate_path = tmp_path / "candidate.json"
    practical_path.write_text(__import__("json").dumps(practical), encoding="utf-8")
    deterministic_path.write_text(__import__("json").dumps(deterministic), encoding="utf-8")
    candidate_path.write_text(__import__("json").dumps(candidate), encoding="utf-8")

    payload = build_phase12_calibration_control_ablation(
        practical_old_controller_path=practical_path,
        deterministic_control_path=deterministic_path,
        candidate_a_control_path=candidate_path,
    )

    assert payload["counts"]["candidate_a_control_vs_deterministic_control"] == {"wins": 1, "losses": 0, "ties": 0}
    assert payload["rows"][0]["candidate_a_control_vs_practical_old_controller"]["basis"] == "grounded_completion"


def test_build_phase12_anchor_control_ablation_reuses_old_controller_grounded_rows(tmp_path) -> None:
    old_matrix = {
        "system_summaries": {"practical_flagship": {"grounded_completed": 0}},
        "rows": [
            {
                "task_id": "task-1",
                "practical_flagship": {
                    "run_dir": "logging/practical",
                    "grounded": {"grounded_completion": False, "completed": False, "ungrounded_stop": False},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                    "source": {"reason": "max_steps_exhausted", "steps": 10, "episode_return": 10.0},
                },
                "candidate_a_flagship": {
                    "run_dir": "logging/candidate-old",
                    "grounded": {"grounded_completion": False, "completed": False, "ungrounded_stop": False},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                    "source": {"reason": "max_steps_exhausted", "steps": 10, "episode_return": 11.0},
                },
            }
        ],
    }
    control_payload = {
        "summary": {"total": 1, "grounded_completed": 0},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/control",
                "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 12},
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "progress_watchdog": {"triggered": True},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": False, "no_progress_streak_max": 12},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                },
            }
        ],
    }
    old_path = tmp_path / "old.json"
    deterministic_path = tmp_path / "deterministic.json"
    candidate_path = tmp_path / "candidate.json"
    old_path.write_text(__import__("json").dumps(old_matrix), encoding="utf-8")
    deterministic_path.write_text(__import__("json").dumps(control_payload), encoding="utf-8")
    candidate_path.write_text(__import__("json").dumps(control_payload), encoding="utf-8")

    payload = build_phase12_anchor_control_ablation(
        grounded_old_controller_matrix_path=old_path,
        deterministic_control_path=deterministic_path,
        candidate_a_control_path=candidate_path,
    )

    assert payload["rows"][0]["practical_flagship_old_controller"]["failure_family"] == "read_heavy_no_closure_loop"
    assert payload["counts"]["candidate_a_control_vs_candidate_a_old_controller"] == {"wins": 0, "losses": 0, "ties": 1}


def test_build_phase12_iteration2_calibration_ablation_compares_v2_against_v1(tmp_path) -> None:
    base = {
        "summary": {"total": 1, "grounded_completed": 0},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/task-1",
                "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 10},
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "progress_watchdog": {"triggered": True},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": False, "no_progress_streak_max": 10},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                },
            }
        ],
    }
    better = {
        "summary": {"total": 1, "grounded_completed": 1},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/task-1",
                "completion_summary": {"completed": True, "reason": "finish_reason:stop", "steps_taken": 5},
                "completion_gate": {"outcome": "verified_completion", "satisfied": True},
                "progress_watchdog": {"triggered": False},
                "grounded_summary": {
                    "grounded": {"grounded_completion": True, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": True, "no_progress_streak_max": 0},
                    "classification": {"failure_family": "grounded_completion"},
                },
            }
        ],
    }
    paths = {}
    for name, payload in {
        "practical": base,
        "det_v1": base,
        "cand_v1": base,
        "det_v2": base,
        "cand_v2": better,
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_text(__import__("json").dumps(payload), encoding="utf-8")
        paths[name] = path

    payload = build_phase12_iteration2_calibration_ablation(
        practical_old_controller_path=paths["practical"],
        deterministic_control_v1_path=paths["det_v1"],
        candidate_a_control_v1_path=paths["cand_v1"],
        deterministic_control_v2_path=paths["det_v2"],
        candidate_a_control_v2_path=paths["cand_v2"],
    )

    assert payload["counts"]["candidate_a_control_v2_vs_deterministic_control_v2"] == {"wins": 1, "losses": 0, "ties": 0}
    assert payload["counts"]["candidate_a_control_v2_vs_candidate_a_control_v1"] == {"wins": 1, "losses": 0, "ties": 0}


def test_build_phase12_iteration2_anchor_ablation_aligns_old_and_v2_rows(tmp_path) -> None:
    old_matrix = {
        "system_summaries": {},
        "rows": [
            {
                "task_id": "task-1",
                "practical_flagship": {
                    "run_dir": "logging/practical",
                    "grounded": {"grounded_completion": False, "completed": False, "ungrounded_stop": False},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                    "source": {"reason": "max_steps_exhausted", "steps": 10, "episode_return": 10.0},
                },
                "candidate_a_flagship": {
                    "run_dir": "logging/candidate-old",
                    "grounded": {"grounded_completion": False, "completed": False, "ungrounded_stop": False},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                    "source": {"reason": "max_steps_exhausted", "steps": 10, "episode_return": 10.0},
                },
            }
        ],
    }
    base = {
        "summary": {"total": 1, "grounded_completed": 0},
        "results": [
            {
                "id": "task-1",
                "run_dir": "logging/task-1",
                "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 10},
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "progress_watchdog": {"triggered": True},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {"verified_completion": False, "no_progress_streak_max": 10},
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                },
            }
        ],
    }
    paths = {}
    for name, payload in {
        "old": old_matrix,
        "det_v1": base,
        "cand_v1": base,
        "det_v2": base,
        "cand_v2": base,
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_text(__import__("json").dumps(payload), encoding="utf-8")
        paths[name] = path

    payload = build_phase12_iteration2_anchor_ablation(
        grounded_old_controller_matrix_path=paths["old"],
        deterministic_control_v1_path=paths["det_v1"],
        candidate_a_control_v1_path=paths["cand_v1"],
        deterministic_control_v2_path=paths["det_v2"],
        candidate_a_control_v2_path=paths["cand_v2"],
    )

    assert payload["rows"][0]["candidate_a_control_v2_vs_deterministic_control_v2"] == {"comparison": "tie", "basis": "grounded_parity"}
