from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PACK = ROOT / "docs" / "contracts" / "darwin" / "fixtures" / "scheduling_scenario_pack_v0.json"
DEFAULT_OUT = ROOT / "artifacts" / "darwin" / "live_baselines" / "lane.scheduling" / "scheduling_baseline.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_sort_key(task: dict, strategy: str) -> tuple:
    if strategy == "deadline_first":
        return (task["deadline"], -task["value"], task["task_id"])
    if strategy == "value_density":
        return (-(task["value"] / max(task["duration"], 1)), task["deadline"], task["task_id"])
    if strategy == "slack_then_value":
        return (task["deadline"] - task["duration"], -task["value"], task["task_id"])
    raise ValueError(f"unknown strategy: {strategy}")


def _schedule_tasks(tasks: list[dict], strategy: str) -> dict:
    ordered = sorted(tasks, key=lambda task: _task_sort_key(task, strategy))
    time_cursor = 0
    chosen = []
    total_value = 0
    for task in ordered:
        finish = time_cursor + int(task["duration"])
        if finish <= int(task["deadline"]):
            chosen.append(task["task_id"])
            total_value += int(task["value"])
            time_cursor = finish
    return {"selected_tasks": chosen, "objective": total_value, "final_time": time_cursor}


def _optimal_objective(tasks: list[dict]) -> int:
    best = 0
    for size in range(len(tasks) + 1):
        for subset in itertools.permutations(tasks, size):
            time_cursor = 0
            total_value = 0
            feasible = True
            for task in subset:
                time_cursor += int(task["duration"])
                if time_cursor > int(task["deadline"]):
                    feasible = False
                    break
                total_value += int(task["value"])
            if feasible:
                best = max(best, total_value)
    return best


def run_scheduling_baseline(*, strategy: str, scenario_pack_path: Path = SCENARIO_PACK) -> dict:
    payload = _load_json(scenario_pack_path)
    rows = []
    for scenario in payload.get("scenarios") or []:
        tasks = list(scenario["tasks"])
        scheduled = _schedule_tasks(tasks, strategy)
        optimum = _optimal_objective(tasks)
        normalized = float(scheduled["objective"] / optimum) if optimum else 1.0
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "objective": scheduled["objective"],
                "optimal_objective": optimum,
                "normalized_score": round(normalized, 6),
                "selected_tasks": scheduled["selected_tasks"],
            }
        )
    mean_score = sum(row["normalized_score"] for row in rows) / len(rows) if rows else 0.0
    return {
        "schema": "breadboard.darwin.scheduling_baseline_summary.v0",
        "strategy": strategy,
        "scenario_count": len(rows),
        "overall_ok": True,
        "primary_score": round(mean_score, 6),
        "decision_state": "ready" if rows else "empty",
        "rows": rows,
    }


def write_summary(*, strategy: str, out_path: Path = DEFAULT_OUT, scenario_pack_path: Path = SCENARIO_PACK) -> dict:
    summary = run_scheduling_baseline(strategy=strategy, scenario_pack_path=scenario_pack_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "primary_score": summary["primary_score"], "scenario_count": summary["scenario_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the DARWIN scheduling lane baseline or heuristic variant.")
    parser.add_argument("--strategy", default="deadline_first", choices=["deadline_first", "value_density", "slack_then_value"])
    parser.add_argument("--scenario-pack", default=str(SCENARIO_PACK))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_summary(strategy=args.strategy, out_path=Path(args.out), scenario_pack_path=Path(args.scenario_pack))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"scheduling_summary={summary['out_path']}")
        print(f"primary_score={summary['primary_score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
