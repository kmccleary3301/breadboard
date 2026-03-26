from agentic_coder_prototype.ctrees.phase14_executor_probe import build_phase14_executor_probe_summary


def test_phase14_executor_probe_summary_counts_grounded_wins() -> None:
    candidate = {
        "summary": {"total": 1},
        "results": [
            {
                "id": "task-1",
                "completion_summary": {"completed": True, "steps_taken": 3},
                "grounded_summary": {
                    "grounded": {"grounded_completion": True, "ungrounded_stop": False},
                    "controller_metrics": {
                        "verified_completion": True,
                        "first_write_step": 1,
                        "first_verify_step": 2,
                        "no_progress_streak_max": 0,
                    },
                    "classification": {"failure_family": ""},
                },
                "completion_gate": {"outcome": "verified_completion", "satisfied": True},
                "phase11_live_protocol": {
                    "family": "candidate_a_executor_v1",
                    "support_strategy": "candidate_a",
                    "executor_contract": {"phase_order": ["localize", "commit_edit", "verify", "close"]},
                },
            }
        ],
    }
    deterministic = {
        "summary": {"total": 1},
        "results": [
            {
                "id": "task-1",
                "completion_summary": {"completed": False, "steps_taken": 8},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": False},
                    "controller_metrics": {
                        "verified_completion": False,
                        "first_write_step": None,
                        "first_verify_step": None,
                        "no_progress_streak_max": 8,
                    },
                    "classification": {"failure_family": "read_heavy_no_closure_loop"},
                },
                "completion_gate": {"outcome": "incomplete", "satisfied": False},
                "phase11_live_protocol": {
                    "family": "deterministic_executor_v1",
                    "support_strategy": "deterministic_reranker",
                    "executor_contract": {"phase_order": ["localize", "commit_edit", "verify", "close"]},
                },
            }
        ],
    }
    execution_first = {
        "summary": {"total": 1},
        "results": [
            {
                "id": "task-1",
                "completion_summary": {"completed": True, "steps_taken": 2},
                "grounded_summary": {
                    "grounded": {"grounded_completion": False, "ungrounded_stop": True},
                    "controller_metrics": {
                        "verified_completion": False,
                        "first_write_step": None,
                        "first_verify_step": None,
                        "no_progress_streak_max": 2,
                    },
                    "classification": {"failure_family": "premature_natural_language_stop"},
                },
                "completion_gate": {"outcome": "ungrounded_stop", "satisfied": False},
                "phase11_live_protocol": {
                    "family": "execution_first_executor_v1_mini",
                    "support_strategy": "minimal_support",
                    "executor_contract": {"phase_order": ["localize", "commit_edit", "verify", "close"]},
                },
            }
        ],
    }

    import json
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        candidate_path = root / "candidate.json"
        deterministic_path = root / "deterministic.json"
        execution_first_path = root / "execution_first.json"
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        deterministic_path.write_text(json.dumps(deterministic), encoding="utf-8")
        execution_first_path.write_text(json.dumps(execution_first), encoding="utf-8")

        payload = build_phase14_executor_probe_summary(
            candidate_a_path=candidate_path,
            deterministic_path=deterministic_path,
            execution_first_path=execution_first_path,
        )

    counts = payload["counts"]
    assert counts["candidate_a_executor_v1_vs_deterministic_executor_v1"] == {"wins": 1, "losses": 0, "ties": 0}
    assert counts["execution_first_executor_v1_mini_vs_deterministic_executor_v1"] == {"wins": 0, "losses": 0, "ties": 1}
