#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]

TOOL_BOUNDS = {
    "claude": {
        "Task": (2, 2),
        "TaskOutput": (2, 2),
        "run_shell": (1, 1),
    },
    "opencode": {
        "task": (0, 3),
        "list_dir": (0, 4),
        "read_file": (0, 4),
        "run_shell": (0, 2),
        "todowrite": (0, 6),
    },
    "omo": {
        "background_task": (0, 2),
        "background_output": (0, 2),
        "list_dir": (0, 8),
        "read_file": (0, 3),
        "run_shell": (0, 8),
        "call_omo_agent": (0, 2),
    },
}

STEP_BOUNDS = {
    "claude": (3, 4),
    "opencode": (1, 20),
    "omo": (1, 20),
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _detect_harness(scenario_name: str) -> str | None:
    if scenario_name.startswith("claude_code"):
        return "claude"
    if scenario_name.startswith("opencode_"):
        return "opencode"
    if scenario_name.startswith("oh_my_opencode"):
        return "omo"
    return None


def _tool_counts(summary: dict) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for turn in (summary.get("turn_tool_usage") or {}).values():
        for tool in (turn.get("tools") or []):
            name = tool.get("name")
            if name:
                counts[name] += 1
    return counts


def _steps_taken(summary: dict) -> int | None:
    steps = summary.get("steps_taken")
    if isinstance(steps, int):
        return steps
    steps = summary.get("completion_summary", {}).get("steps_taken")
    return steps if isinstance(steps, int) else None


def validate(root: Path) -> Tuple[List[dict], List[dict]]:
    failures: List[dict] = []
    warnings: List[dict] = []
    for scenario_dir in sorted(root.iterdir()):
        if not scenario_dir.is_dir():
            continue
        harness = _detect_harness(scenario_dir.name)
        if not harness:
            continue
        for run_dir in sorted([p for p in scenario_dir.iterdir() if p.is_dir()]):
            result_path = run_dir / "result.json"
            if not result_path.exists():
                continue
            try:
                result = _load_json(result_path)
            except Exception:
                continue
            run_dir_value = result.get("result", {}).get("run_dir")
            if not isinstance(run_dir_value, str) or not run_dir_value:
                continue
            summary_path = Path(run_dir_value) / "meta" / "run_summary.json"
            if not summary_path.exists():
                continue
            summary = _load_json(summary_path)
            tool_counts = _tool_counts(summary)
            steps = _steps_taken(summary)

            tool_bounds = TOOL_BOUNDS.get(harness, {})
            for tool, (min_v, max_v) in tool_bounds.items():
                count = tool_counts.get(tool, 0)
                if count < min_v or count > max_v:
                    failures.append(
                        {
                            "scenario": scenario_dir.name,
                            "run_id": run_dir.name,
                            "harness": harness,
                            "kind": "tool_count",
                            "tool": tool,
                            "count": count,
                            "min": min_v,
                            "max": max_v,
                        }
                    )
            step_bounds = STEP_BOUNDS.get(harness)
            if step_bounds and isinstance(steps, int):
                min_s, max_s = step_bounds
                if steps < min_s or steps > max_s:
                    failures.append(
                        {
                            "scenario": scenario_dir.name,
                            "run_id": run_dir.name,
                            "harness": harness,
                            "kind": "steps",
                            "steps": steps,
                            "min": min_s,
                            "max": max_s,
                        }
                    )
    return failures, warnings


def render_markdown(failures: List[dict]) -> str:
    lines = [
        "# Phase 8 — Live Bounds Validation",
        "",
        "Generated from phase_8_live_checks + logging run summaries.",
        "",
        "## Failures",
        "",
        "| scenario | run_id | harness | kind | detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not failures:
        lines.append("| (none) | (none) | (none) | (none) | all bounds satisfied |")
    else:
        for item in failures:
            if item["kind"] == "tool_count":
                detail = f"{item['tool']} count {item['count']} outside [{item['min']},{item['max']}]"
            else:
                detail = f"steps {item['steps']} outside [{item['min']},{item['max']}]"
            lines.append(
                f"| {item['scenario']} | {item['run_id']} | {item['harness']} | {item['kind']} | {detail} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 8 live run bounds.")
    parser.add_argument("--root", default=str(ROOT / "misc" / "phase_8_live_checks" / "breadboard"))
    parser.add_argument("--out", default=str(ROOT / "docs_tmp" / "phase_8" / "PHASE_8_LIVE_BOUNDS_VALIDATION.md"))
    parser.add_argument(
        "--mode",
        choices={"warn", "fail"},
        default="warn",
        help="Exit with non-zero status on violations when set to fail.",
    )
    args = parser.parse_args()

    failures, _ = validate(Path(args.root))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(failures), encoding="utf-8")
    print(f"[phase8-bounds] wrote {out_path} ({len(failures)} failures)")
    if failures and args.mode == "fail":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
