#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from breadboard_engine.optimize.optuna_config import load_optuna_config
ALLOWED_TASK_TYPES = {"general", "function", "bugfix", "refactor"}


def _load_tasks(path: Path) -> list[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError("tasks file must be a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def _validate_task(task: Dict[str, Any]) -> list[str]:
    issues: list[str] = []
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append("prompt_missing_or_empty")
    task_type = task.get("task_type")
    if task_type is not None and task_type not in ALLOWED_TASK_TYPES:
        issues.append(f"task_type_unknown:{task_type}")
    max_steps = task.get("max_steps")
    if max_steps is not None and (not isinstance(max_steps, int) or max_steps <= 0):
        issues.append("max_steps_invalid")
    latency = task.get("latency_budget_ms")
    if latency is not None:
        try:
            if float(latency) <= 0:
                issues.append("latency_budget_ms_invalid")
        except (TypeError, ValueError):
            issues.append("latency_budget_ms_invalid")
    return issues




def _run_task_validator(tasks_path: Path) -> Dict[str, Any]:
    tasks = _load_tasks(tasks_path)
    issues = []
    error_count = 0
    warning_count = 0
    for idx, task in enumerate(tasks):
        task_id = task.get("id") or f"task_{idx+1}"
        task_issues = _validate_task(task)
        for issue in task_issues:
            is_warning = issue.startswith("task_type_unknown")
            if is_warning:
                warning_count += 1
            else:
                error_count += 1
            issues.append({"id": task_id, "issue": issue, "warning": is_warning})
    return {
        "task_summary": {
            "total": len(tasks),
            "errors": error_count,
            "warnings": warning_count,
            "ok": error_count == 0,
        },
        "task_issues": issues,
    }


def _run_optuna_config_validator(space_path: Path) -> Dict[str, Any]:
    try:
        cfg = load_optuna_config(space_path)
    except Exception as exc:
        return {"optuna_ok": False, "error": str(exc)}
    return {"optuna_ok": True, "space_keys": sorted(cfg["space"].keys())}


def _run_surface_audit(summary_path: Path) -> Dict[str, Any]:
    from scripts.phase11_surface_coverage_audit import _load_summary  # local import

    summary = _load_summary(summary_path)
    manifest = summary.get("surface_manifest") or {}
    surface_names = {entry.get("name") for entry in manifest.get("surfaces", []) if isinstance(entry, dict)}
    return {
        "surface_ok": bool(surface_names),
        "surface_names": sorted(name for name in surface_names if name),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 11 validation bundle")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--run-summary", required=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report: Dict[str, Any] = {}
    report.update(_run_task_validator(Path(args.tasks)))
    report.update(_run_optuna_config_validator(Path(args.space)))
    if args.run_summary:
        report.update(_run_surface_audit(Path(args.run_summary)))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.strict:
        if not report.get("optuna_ok"):
            return 2
        if not report.get("task_summary", {}).get("ok"):
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
