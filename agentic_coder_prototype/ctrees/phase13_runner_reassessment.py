from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


_CONTROL_ROOT = Path("/shared_folders/querylake_server/ray_testing/ray_SCE")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PHASE13_ROOT = _CONTROL_ROOT / "docs_tmp" / "c_trees" / "phase_13"

_SYSTEM_PATHS = {
    "candidate_a_runtime_v1": _PHASE13_ROOT / "artifacts" / "runtime_floor_gate_v1" / "phase13_candidate_a_runtime_v1_live.json",
    "deterministic_runtime_v1": _PHASE13_ROOT / "artifacts" / "runtime_floor_gate_v1" / "phase13_deterministic_runtime_v1_live.json",
    "candidate_a_runtime_v2": _PHASE13_ROOT / "artifacts" / "runtime_floor_gate_v2" / "phase13_candidate_a_runtime_v2_live.json",
    "deterministic_runtime_v2": _PHASE13_ROOT / "artifacts" / "runtime_floor_gate_v2" / "phase13_deterministic_runtime_v2_live.json",
}


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_run_dir(run_dir: str | Path, *, repo_root: Path) -> Path:
    path = Path(run_dir)
    if path.is_absolute():
        return path
    return repo_root / path


def _iter_turn_payloads(path: Path) -> Iterable[tuple[int, List[Dict[str, Any]]]]:
    if not path.exists():
        return []
    payloads: List[tuple[int, List[Dict[str, Any]]]] = []
    for item in sorted(path.glob("turn_*.json")):
        try:
            turn_idx = int(item.stem.split("_", 1)[1])
        except Exception:
            continue
        payload = _load_json(item)
        if not isinstance(payload, list):
            continue
        payloads.append((turn_idx, [dict(entry) for entry in payload if isinstance(entry, dict)]))
    return payloads


def _count_strings(items: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        counts[item] = int(counts.get(item) or 0) + 1
    return counts


def _summarize_run_row(row: Dict[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    task_id = str(row.get("id") or "")
    run_dir_raw = str(row.get("run_dir") or "")
    run_dir = _resolve_run_dir(run_dir_raw, repo_root=repo_root)
    completion_summary = dict(row.get("completion_summary") or {})
    max_steps = int(completion_summary.get("max_steps") or 0)

    tool_results_dir = run_dir / "provider_native" / "tool_results"
    tools_provided_dir = run_dir / "provider_native" / "tools_provided"
    tool_calls_dir = run_dir / "provider_native" / "tool_calls"

    total_tool_results = 0
    unknown_tool_results = 0
    successful_tool_results = 0
    unknown_tool_names: List[str] = []
    attempted_tool_names: List[str] = []
    provided_tool_names: List[str] = []
    max_tool_result_turn = 0
    max_tool_call_turn = 0
    max_tools_provided_turn = 0

    for turn_idx, payload in _iter_turn_payloads(tool_results_dir):
        max_tool_result_turn = max(max_tool_result_turn, turn_idx)
        for entry in payload:
            total_tool_results += 1
            fn = str(entry.get("fn") or "")
            if fn:
                attempted_tool_names.append(fn)
            out = entry.get("out")
            if isinstance(out, dict):
                error = str(out.get("error") or "")
                if "unknown tool" in error:
                    unknown_tool_results += 1
                    unknown_tool_names.append(fn or error)
                elif error == "":
                    successful_tool_results += 1

    for turn_idx, payload in _iter_turn_payloads(tool_calls_dir):
        max_tool_call_turn = max(max_tool_call_turn, turn_idx)
        for entry in payload:
            fn = str(dict(entry.get("function") or {}).get("name") or "")
            if fn:
                attempted_tool_names.append(fn)

    for turn_idx, payload in _iter_turn_payloads(tools_provided_dir):
        max_tools_provided_turn = max(max_tools_provided_turn, turn_idx)
        for entry in payload:
            name = str(dict(entry).get("name") or "")
            if name:
                provided_tool_names.append(name)

    max_observed_turn = max(max_tool_result_turn, max_tool_call_turn, max_tools_provided_turn)
    turn_index_exceeds_step_limit = bool(max_steps > 0 and max_observed_turn > max_steps)

    return {
        "task_id": task_id,
        "run_dir": str(run_dir),
        "completed": bool(completion_summary.get("completed")),
        "reason": str(completion_summary.get("reason") or ""),
        "steps_taken": int(completion_summary.get("steps_taken") or 0),
        "max_steps": max_steps,
        "total_tool_results": total_tool_results,
        "unknown_tool_results": unknown_tool_results,
        "successful_tool_results": successful_tool_results,
        "all_tool_results_unknown": bool(total_tool_results > 0 and unknown_tool_results == total_tool_results),
        "unknown_tool_names": sorted(set(name for name in unknown_tool_names if name)),
        "attempted_tool_name_counts": _count_strings(name for name in attempted_tool_names if name),
        "provided_tool_name_counts": _count_strings(name for name in provided_tool_names if name),
        "max_tool_result_turn": max_tool_result_turn,
        "max_tool_call_turn": max_tool_call_turn,
        "max_tools_provided_turn": max_tools_provided_turn,
        "max_observed_turn": max_observed_turn,
        "turn_index_exceeds_step_limit": turn_index_exceeds_step_limit,
    }


def summarize_phase13_runtime_execution(
    payload_path: str | Path,
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    path = Path(payload_path)
    payload = _load_json(path)
    resolved_repo_root = repo_root or _REPO_ROOT
    rows = [
        _summarize_run_row(dict(row), repo_root=resolved_repo_root)
        for row in list(payload.get("results") or [])
        if isinstance(row, dict)
    ]

    return {
        "payload_path": str(path),
        "row_count": len(rows),
        "rows_with_unknown_tool_errors": sum(1 for row in rows if int(row.get("unknown_tool_results") or 0) > 0),
        "rows_with_all_tool_results_unknown": sum(1 for row in rows if bool(row.get("all_tool_results_unknown"))),
        "rows_with_successful_tool_results": sum(1 for row in rows if int(row.get("successful_tool_results") or 0) > 0),
        "rows_with_turn_index_drift": sum(1 for row in rows if bool(row.get("turn_index_exceeds_step_limit"))),
        "total_tool_results": sum(int(row.get("total_tool_results") or 0) for row in rows),
        "unknown_tool_results": sum(int(row.get("unknown_tool_results") or 0) for row in rows),
        "successful_tool_results": sum(int(row.get("successful_tool_results") or 0) for row in rows),
        "unknown_tool_name_counts": _count_strings(
            name
            for row in rows
            for name in list(row.get("unknown_tool_names") or [])
        ),
        "rows": rows,
    }


def build_phase13_runner_reassessment(
    *,
    system_paths: Dict[str, Path] | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    resolved_system_paths = dict(system_paths or _SYSTEM_PATHS)
    resolved_repo_root = repo_root or _REPO_ROOT
    system_summaries = {
        system_key: summarize_phase13_runtime_execution(path, repo_root=resolved_repo_root)
        for system_key, path in resolved_system_paths.items()
    }
    return {
        "schema_version": "phase13_runner_reassessment_v1",
        "repo_root": str(resolved_repo_root),
        "system_paths": {key: str(path) for key, path in resolved_system_paths.items()},
        "global_summary": {
            "all_rows_have_unknown_tool_errors": all(
                int(summary.get("rows_with_unknown_tool_errors") or 0) == int(summary.get("row_count") or 0)
                for summary in system_summaries.values()
            ),
            "all_rows_have_turn_index_drift": all(
                int(summary.get("rows_with_turn_index_drift") or 0) == int(summary.get("row_count") or 0)
                for summary in system_summaries.values()
            ),
            "any_successful_tool_results": any(
                int(summary.get("successful_tool_results") or 0) > 0
                for summary in system_summaries.values()
            ),
            "ranking_invalidated_by_runner_compatibility": all(
                int(summary.get("rows_with_all_tool_results_unknown") or 0) == int(summary.get("row_count") or 0)
                for summary in system_summaries.values()
            ),
        },
        "system_summaries": system_summaries,
    }
