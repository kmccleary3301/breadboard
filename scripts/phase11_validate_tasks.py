#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


ALLOWED_TASK_TYPES = {"general", "function", "bugfix", "refactor"}


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError("tasks file must be a JSON array")
    return [dict(item) for item in payload if isinstance(item, dict)]


def validate_task(task: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append("prompt_missing_or_empty")
    task_type = task.get("task_type")
    if task_type is not None and task_type not in ALLOWED_TASK_TYPES:
        issues.append(f"task_type_unknown:{task_type}")
    max_steps = task.get("max_steps")
    if max_steps is not None:
        if not isinstance(max_steps, int) or max_steps <= 0:
            issues.append("max_steps_invalid")
    latency = task.get("latency_budget_ms")
    if latency is not None:
        try:
            latency_val = float(latency)
            if latency_val <= 0:
                issues.append("latency_budget_ms_invalid")
        except (TypeError, ValueError):
            issues.append("latency_budget_ms_invalid")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 11 benchmark task files")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    tasks = _load_tasks(Path(args.tasks))
    issues: List[Dict[str, Any]] = []
    error_count = 0
    warning_count = 0

    for idx, task in enumerate(tasks):
        task_id = task.get("id") or f"task_{idx+1}"
        task_issues = validate_task(task)
        if task_issues:
            for issue in task_issues:
                is_warning = issue.startswith("task_type_unknown")
                if is_warning:
                    warning_count += 1
                else:
                    error_count += 1
                issues.append({"id": task_id, "issue": issue, "warning": is_warning})

    summary = {
        "total": len(tasks),
        "errors": error_count,
        "warnings": warning_count,
        "ok": error_count == 0,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "issues": issues}, indent=2), encoding="utf-8")
    if args.strict and error_count:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
