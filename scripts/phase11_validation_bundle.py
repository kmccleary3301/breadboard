#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.optimize.optuna_config import load_optuna_config


def _run_task_validator(tasks_path: Path) -> Dict[str, Any]:
    from scripts.phase11_validate_tasks import _load_tasks, validate_task  # local import

    tasks = _load_tasks(tasks_path)
    issues = []
    error_count = 0
    warning_count = 0
    for idx, task in enumerate(tasks):
        task_id = task.get("id") or f"task_{idx+1}"
        task_issues = validate_task(task)
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
