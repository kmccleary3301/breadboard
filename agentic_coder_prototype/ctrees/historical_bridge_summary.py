from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .historical_runner_bridge import load_historical_runner_result


_DEFAULT_OUTPUT_ROOT = Path(
    "/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_ctrees_restore_20260310/logs/phase10_prompt_centric_historical_bridge_v1"
)


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_prompt_centric_bridge_summary(output_root: str | Path = _DEFAULT_OUTPUT_ROOT) -> Dict[str, Any]:
    output_path = Path(output_root)
    scenario_summaries: List[Dict[str, Any]] = []
    total_repeats = 0
    pass_count = 0
    task_failure_count = 0
    tool_policy_failure_count = 0
    bundle_present_count = 0
    request_counts: List[int] = []
    input_tokens: List[int] = []
    output_tokens: List[int] = []

    for results_path in sorted(output_path.glob("*_results.json")):
        payload = _safe_json_load(results_path)
        rows = list(payload.get("results") or [])
        repeat_summaries: List[Dict[str, Any]] = []
        for row in rows:
            total_repeats += 1
            score = dict(row.get("score") or {})
            tool_requirements = dict(row.get("tool_requirements") or {})
            usage_summary = dict(row.get("usage_summary") or {})
            bridge_payload = load_historical_runner_result(str(row.get("run_dir") or ""))
            score_ok = bool(score.get("ok"))
            tool_ok = bool(tool_requirements.get("ok"))
            if score_ok and tool_ok:
                pass_count += 1
            else:
                task_failure_count += 1 if not score_ok else 0
                tool_policy_failure_count += 1 if not tool_ok else 0
            if any(bool(value) for value in (bridge_payload.get("exists") or {}).values()):
                bundle_present_count += 1
            totals = dict(usage_summary.get("totals") or {})
            request_count = int(usage_summary.get("request_count") or 0)
            in_tokens = int(totals.get("input_tokens") or 0)
            out_tokens = int(totals.get("output_tokens") or 0)
            if request_count:
                request_counts.append(request_count)
            if in_tokens:
                input_tokens.append(in_tokens)
            if out_tokens:
                output_tokens.append(out_tokens)
            repeat_summaries.append(
                {
                    "repeat_index": int(row.get("repeat_index") or 0),
                    "score_ok": score_ok,
                    "score_reason": str(score.get("reason") or ""),
                    "tool_policy_ok": tool_ok,
                    "tool_policy_reason": str(tool_requirements.get("reason") or ""),
                    "request_count": request_count,
                    "input_tokens": in_tokens,
                    "output_tokens": out_tokens,
                    "observed_semantics": dict(bridge_payload.get("observed_semantics") or {}),
                    "selection_delta_stage": bridge_payload.get("selection_delta_stage"),
                }
            )
        scenario_id = str(rows[0].get("scenario_id") or results_path.name) if rows else results_path.name
        scenario_summaries.append(
            {
                "scenario_id": scenario_id,
                "results_path": str(results_path),
                "repeat_count": len(rows),
                "repeat_summaries": repeat_summaries,
                "policy_failure_count": sum(1 for item in repeat_summaries if not bool(item.get("tool_policy_ok"))),
                "task_failure_count": sum(1 for item in repeat_summaries if not bool(item.get("score_ok"))),
            }
        )

    def _avg(values: List[int]) -> float:
        return (sum(values) / len(values)) if values else 0.0

    return {
        "schema_version": "ctree_historical_bridge_summary_v1",
        "output_root": str(output_path),
        "scenario_count": len(scenario_summaries),
        "total_repeat_count": total_repeats,
        "pass_count": pass_count,
        "task_failure_count": task_failure_count,
        "tool_policy_failure_count": tool_policy_failure_count,
        "bundle_present_count": bundle_present_count,
        "avg_request_count": _avg(request_counts),
        "avg_input_tokens": _avg(input_tokens),
        "avg_output_tokens": _avg(output_tokens),
        "scenario_summaries": scenario_summaries,
    }

