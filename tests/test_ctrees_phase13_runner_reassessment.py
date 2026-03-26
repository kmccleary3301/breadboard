import json
from pathlib import Path

from agentic_coder_prototype.ctrees.phase13_runner_reassessment import (
    build_phase13_runner_reassessment,
    summarize_phase13_runtime_execution,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_summarize_phase13_runtime_execution_flags_unknown_tools_and_turn_drift(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    run_dir = repo_root / "logging" / "run1"
    _write_json(
        run_dir / "provider_native" / "tool_results" / "turn_25.json",
        [{"fn": "shell_command", "out": {"error": "unknown tool shell_command"}}],
    )
    _write_json(
        run_dir / "provider_native" / "tool_calls" / "turn_21.json",
        [{"function": {"name": "shell_command"}}],
    )
    _write_json(
        run_dir / "provider_native" / "tools_provided" / "turn_1.json",
        [{"name": "shell_command"}, {"name": "apply_patch"}],
    )

    payload_path = tmp_path / "payload.json"
    _write_json(
        payload_path,
        {
            "results": [
                {
                    "id": "task_1",
                    "run_dir": "logging/run1",
                    "completion_summary": {"completed": False, "reason": "max_steps_exhausted", "steps_taken": 8, "max_steps": 8},
                }
            ]
        },
    )

    summary = summarize_phase13_runtime_execution(payload_path, repo_root=repo_root)
    assert summary["row_count"] == 1
    assert summary["rows_with_unknown_tool_errors"] == 1
    assert summary["rows_with_all_tool_results_unknown"] == 1
    assert summary["rows_with_turn_index_drift"] == 1
    assert summary["unknown_tool_name_counts"]["shell_command"] == 1


def test_build_phase13_runner_reassessment_marks_ranking_invalid_when_all_tools_fail(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    system_paths = {}
    for system_key in ("candidate_a_runtime_v1", "deterministic_runtime_v1"):
        run_dir = repo_root / "logging" / system_key
        _write_json(
            run_dir / "provider_native" / "tool_results" / "turn_13.json",
            [{"fn": "update_plan", "out": {"error": "unknown tool update_plan"}}],
        )
        payload_path = tmp_path / f"{system_key}.json"
        _write_json(
            payload_path,
            {
                "results": [
                    {
                        "id": system_key,
                        "run_dir": f"logging/{system_key}",
                        "completion_summary": {
                            "completed": False,
                            "reason": "max_steps_exhausted",
                            "steps_taken": 8,
                            "max_steps": 8,
                        },
                    }
                ]
            },
        )
        system_paths[system_key] = payload_path

    payload = build_phase13_runner_reassessment(system_paths=system_paths, repo_root=repo_root)
    assert payload["global_summary"]["all_rows_have_unknown_tool_errors"] is True
    assert payload["global_summary"]["ranking_invalidated_by_runner_compatibility"] is True
