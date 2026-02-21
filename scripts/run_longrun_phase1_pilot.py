#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentic_coder_prototype.longrun.controller import LongRunController


EpisodeResult = Dict[str, Any]
ScenarioFn = Callable[[int], EpisodeResult]


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    description: str
    fn: ScenarioFn
    longrun_config: Dict[str, Any]
    runs: int = 5


class _MemoryLogger:
    def __init__(self) -> None:
        self._writes: Dict[str, Dict[str, Any]] = {}

    def write_json(self, rel_path: str, data: Mapping[str, Any]) -> str:
        self._writes[rel_path] = dict(data)
        return rel_path

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._writes)


def _single_success(episode_index: int) -> EpisodeResult:
    return {
        "completed": True,
        "completion_reason": "task_completed",
        "completion_summary": {"completed": True, "reason": "task_completed"},
        "verification_summary": {"overall_status": "pass"},
    }


def _flaky_then_success(episode_index: int) -> EpisodeResult:
    if episode_index == 0:
        return {
            "completed": False,
            "completion_reason": "verification_failed",
            "verification_summary": {"overall_status": "fail"},
            "progress_metrics": {"made_progress": False, "delta": 0},
        }
    return {
        "completed": True,
        "completion_reason": "task_completed_after_retry",
        "completion_summary": {"completed": True, "reason": "task_completed_after_retry"},
        "verification_summary": {"overall_status": "pass"},
        "progress_metrics": {"made_progress": True, "delta": 1},
    }


def _persistent_no_progress(episode_index: int) -> EpisodeResult:
    return {
        "completed": False,
        "completion_reason": "not_done",
        "verification_summary": {"overall_status": "fail"},
        "progress_metrics": {"made_progress": False, "delta": 0},
    }


def _regression_with_recovery(episode_index: int) -> EpisodeResult:
    # Never completes; intended to trigger rollback then bounded stop.
    return {
        "completed": False,
        "completion_reason": "not_done",
        "verification_summary": {"overall_status": "fail"},
        "progress_metrics": {"made_progress": False, "delta": 0},
    }


def _signature_repeat_failure(episode_index: int) -> EpisodeResult:
    return {
        "completed": False,
        "completion_reason": "stuck_signature",
        "verification_summary": {"overall_status": "fail"},
        "progress_metrics": {"made_progress": False, "delta": 0},
    }


def _alternating_failures(episode_index: int) -> EpisodeResult:
    return {
        "completed": False,
        "completion_reason": f"alternating_{episode_index % 2}",
        "verification_summary": {"overall_status": "fail"},
        "progress_metrics": {"made_progress": False, "delta": 0},
    }


def build_scenarios() -> List[ScenarioSpec]:
    return [
        ScenarioSpec(
            scenario_id="single_success",
            description="Single deterministic episode completion.",
            fn=_single_success,
            longrun_config={
                "long_running": {
                    "enabled": True,
                    "budgets": {"max_episodes": 3, "max_retries_per_item": 1},
                    "recovery": {"no_progress_max_episodes": 2},
                }
            },
        ),
        ScenarioSpec(
            scenario_id="flaky_then_success",
            description="First episode fails verification, second succeeds.",
            fn=_flaky_then_success,
            longrun_config={
                "long_running": {
                    "enabled": True,
                    "budgets": {"max_episodes": 4, "max_retries_per_item": 3},
                    "recovery": {"no_progress_max_episodes": 0},
                }
            },
        ),
        ScenarioSpec(
            scenario_id="persistent_no_progress",
            description="No progress episodes should stop by no-progress threshold.",
            fn=_persistent_no_progress,
            longrun_config={
                "long_running": {
                    "enabled": True,
                    "budgets": {"max_episodes": 8, "max_retries_per_item": 0},
                    "recovery": {"no_progress_max_episodes": 2},
                }
            },
        ),
        ScenarioSpec(
            scenario_id="retry_then_rollback",
            description="Retry budget triggers one rollback path then bounded stop.",
            fn=_regression_with_recovery,
            longrun_config={
                "long_running": {
                    "enabled": True,
                    "budgets": {"max_episodes": 5, "max_retries_per_item": 1},
                    "recovery": {
                        "no_progress_max_episodes": 0,
                        "rollback_enabled": True,
                        "checkpoint_every_episodes": 1,
                    },
                }
            },
        ),
        ScenarioSpec(
            scenario_id="signature_repeat_stop",
            description="Repeated failure signature triggers bounded no-progress stop.",
            fn=_signature_repeat_failure,
            longrun_config={
                "long_running": {
                    "enabled": True,
                    "budgets": {"max_episodes": 6, "max_retries_per_item": 0},
                    "recovery": {
                        "no_progress_max_episodes": 0,
                        "no_progress_signature_repeats": 2,
                    },
                }
            },
        ),
        ScenarioSpec(
            scenario_id="alternating_failure_signatures",
            description="Alternating failure signatures avoids signature stop and hits cap.",
            fn=_alternating_failures,
            longrun_config={
                "long_running": {
                    "enabled": True,
                    "budgets": {"max_episodes": 4, "max_retries_per_item": 0},
                    "recovery": {
                        "no_progress_max_episodes": 0,
                        "no_progress_signature_repeats": 3,
                    },
                }
            },
        ),
    ]


def run_baseline_once(spec: ScenarioSpec) -> Dict[str, Any]:
    started = time.perf_counter()
    max_episodes = int(
        ((((spec.longrun_config or {}).get("long_running") or {}).get("budgets") or {}).get("max_episodes")
        or 1
    )
    )
    result: EpisodeResult = {"completed": False, "completion_reason": "single_episode_exit"}
    episodes_run = 0
    for episode_index in range(max(1, max_episodes)):
        episodes_run += 1
        result = spec.fn(episode_index)
        if bool(result.get("completed", False)):
            break
    ended = time.perf_counter()
    completed = bool(result.get("completed", False))
    return {
        "completed": completed,
        "completion_reason": str(result.get("completion_reason", "unknown")),
        "episodes_run": episodes_run,
        "stop_reason": "episode_completed" if completed else "max_episodes_reached",
        "duration_ms": int((ended - started) * 1000),
    }


def run_longrun_once(spec: ScenarioSpec) -> Dict[str, Any]:
    logger = _MemoryLogger()
    controller = LongRunController(spec.longrun_config, logger_v2=logger)
    started = time.perf_counter()
    out = controller.run(spec.fn)
    ended = time.perf_counter()
    summary = dict(out.get("macro_summary") or {})
    state = dict(out.get("macro_state") or {})
    writes = logger.snapshot()
    return {
        "completed": bool(summary.get("completed", False)),
        "completion_reason": str((out.get("result") or {}).get("completion_reason", "unknown")),
        "episodes_run": int(summary.get("episodes_run") or 0),
        "stop_reason": str(summary.get("stop_reason") or "unknown"),
        "duration_ms": int((ended - started) * 1000),
        "rollback_used": bool(state.get("rollback_used", False)),
        "checkpoint_artifacts": sorted([k for k in writes.keys() if "checkpoints/" in k]),
    }


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    success_rate = sum(1 for row in records if row["completed"]) / max(1, len(records))
    return {
        "run_count": len(records),
        "success_rate": success_rate,
        "median_duration_ms": int(statistics.median([row["duration_ms"] for row in records])) if records else 0,
        "median_episodes": statistics.median([row["episodes_run"] for row in records]) if records else 0,
        "stop_reasons": _histogram([row["stop_reason"] for row in records]),
    }


def _histogram(values: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def run_pilot() -> Dict[str, Any]:
    scenarios = build_scenarios()
    scenario_rows: List[Dict[str, Any]] = []
    for spec in scenarios:
        baseline_runs = [run_baseline_once(spec) for _ in range(spec.runs)]
        longrun_runs = [run_longrun_once(spec) for _ in range(spec.runs)]
        scenario_rows.append(
            {
                "scenario_id": spec.scenario_id,
                "description": spec.description,
                "baseline": {
                    "runs": baseline_runs,
                    "summary": aggregate(baseline_runs),
                },
                "longrun": {
                    "runs": longrun_runs,
                    "summary": aggregate(longrun_runs),
                },
            }
        )

    return {
        "schema_version": "longrun_phase1_pilot_v1",
        "generated_at_unix": int(time.time()),
        "baseline_arm": "more_steps_only",
        "runs_per_scenario": scenarios[0].runs if scenarios else 0,
        "scenarios": scenario_rows,
    }


def write_markdown(payload: Mapping[str, Any], out_path: Path) -> None:
    lines: List[str] = []
    lines.append("# LongRun Phase 1 Pilot Summary")
    lines.append("")
    lines.append(f"- Baseline control arm: `{payload.get('baseline_arm', 'unknown')}`")
    lines.append(f"- Runs per scenario: `{payload.get('runs_per_scenario', 0)}`")
    lines.append("")
    lines.append("| Scenario | Baseline Success | LongRun Success | Baseline Episodes (median) | LongRun Episodes (median) | Baseline Top Stop | LongRun Top Stop |")
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for row in payload.get("scenarios", []):
        baseline_summary = row["baseline"]["summary"]
        longrun_summary = row["longrun"]["summary"]
        b_stop = _top_reason(baseline_summary.get("stop_reasons", {}))
        l_stop = _top_reason(longrun_summary.get("stop_reasons", {}))
        lines.append(
            "| {sid} | {bs:.2f} | {ls:.2f} | {be} | {le} | {btop} | {ltop} |".format(
                sid=row["scenario_id"],
                bs=baseline_summary.get("success_rate", 0.0),
                ls=longrun_summary.get("success_rate", 0.0),
                be=baseline_summary.get("median_episodes", 0),
                le=longrun_summary.get("median_episodes", 0),
                btop=b_stop,
                ltop=l_stop,
            )
        )
    lines.append("")
    lines.append("## Delta Highlights")
    lines.append("")
    for row in payload.get("scenarios", []):
        baseline_summary = row["baseline"]["summary"]
        longrun_summary = row["longrun"]["summary"]
        delta_success = float(longrun_summary.get("success_rate", 0.0)) - float(baseline_summary.get("success_rate", 0.0))
        delta_episodes = float(longrun_summary.get("median_episodes", 0.0)) - float(baseline_summary.get("median_episodes", 0.0))
        lines.append(
            f"- `{row['scenario_id']}`: Δsuccess={delta_success:+.2f}, Δmedian_episodes={delta_episodes:+.2f}"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("- This pilot is deterministic and policy-focused (controller behavior), not provider-quality benchmarking.")
    lines.append("- Use this as a boundedness/reliability sanity check before higher-cost live model evaluations.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _top_reason(hist: Mapping[str, Any]) -> str:
    if not hist:
        return "n/a"
    items = sorted(hist.items(), key=lambda item: (-int(item[1]), str(item[0])))
    key, value = items[0]
    return f"{key} ({value})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic baseline-vs-longrun pilot matrix.")
    parser.add_argument(
        "--json-out",
        required=True,
        help="Path to output JSON report.",
    )
    parser.add_argument(
        "--markdown-out",
        required=True,
        help="Path to output markdown summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_pilot()
    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(payload, Path(args.markdown_out))
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote Markdown: {args.markdown_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
