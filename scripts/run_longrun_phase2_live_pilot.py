#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if TYPE_CHECKING:
    from agentic_coder_prototype.agent import AgenticCoder


SCHEMA_VERSION = "longrun_phase2_live_pilot_v1"
DEFAULT_REQUIRE_KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY")
DEFAULT_ARM_TIMEOUT_SECONDS = 180.0
DEFAULT_LONGRUN_OVERRIDES: Dict[str, Any] = {
    # Keep live pilot lean and avoid expensive verification commands in tiny runs.
    "long_running.verification.tiers": [],
    # Disable queue coupling for standalone live pilot prompts.
    "long_running.queue.backend": "none",
    "long_running.budgets.max_episodes": 2,
    "long_running.budgets.max_retries_per_item": 1,
    "long_running.budgets.max_wall_clock_seconds": 600,
    "long_running.budgets.total_tokens": 30000,
    "long_running.budgets.total_cost_usd": 3.0,
}


@dataclass(frozen=True)
class LiveTask:
    task_id: str
    prompt: str
    max_steps: int
    context: Dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_tasks_payload(path: Path) -> List[LiveTask]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and isinstance(payload.get("tasks"), list):
        payload = payload.get("tasks")
    if not isinstance(payload, list):
        raise ValueError("tasks payload must be a JSON list or an object with 'tasks'")
    out: List[LiveTask] = []
    for idx, item in enumerate(payload):
        if not isinstance(item, Mapping):
            continue
        task_id = str(item.get("id") or f"task_{idx+1}").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not task_id or not prompt:
            continue
        max_steps = max(1, _safe_int(item.get("max_steps"), 4))
        context = item.get("context")
        out.append(
            LiveTask(
                task_id=task_id,
                prompt=prompt,
                max_steps=max_steps,
                context=dict(context) if isinstance(context, Mapping) else {},
            )
        )
    return out


def _default_tasks() -> List[LiveTask]:
    return [
        LiveTask(
            task_id="hello_py_function",
            prompt=(
                "Create hello.py with a function greet(name: str) -> str that returns "
                "\"Hello, <name>!\" and include a short docstring."
            ),
            max_steps=4,
            context={"task_type": "function"},
        ),
        LiveTask(
            task_id="json_helper",
            prompt=(
                "Create json_utils.py with a function normalize_payload(payload: dict) that "
                "returns a new dict with string keys sorted alphabetically."
            ),
            max_steps=4,
            context={"task_type": "function"},
        ),
        LiveTask(
            task_id="small_refactor",
            prompt=(
                "Create math_tools.py with add(a,b) and sub(a,b), then refactor to include "
                "type hints and concise docstrings."
            ),
            max_steps=5,
            context={"task_type": "refactor"},
        ),
    ]


def _sum_usage_from_turn_diagnostics(run_summary: Mapping[str, Any]) -> Dict[str, int]:
    prompt_total = 0
    completion_total = 0
    total_total = 0
    diagnostics = run_summary.get("turn_diagnostics")
    if not isinstance(diagnostics, list):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in diagnostics:
        if not isinstance(row, Mapping):
            continue
        usage = row.get("usage")
        if not isinstance(usage, Mapping):
            continue
        prompt = _safe_int(usage.get("prompt_tokens"), 0)
        completion = _safe_int(usage.get("completion_tokens"), 0)
        total = _safe_int(usage.get("total_tokens"), prompt + completion)
        prompt_total += max(0, prompt)
        completion_total += max(0, completion)
        total_total += max(0, total)
    if total_total <= 0:
        total_total = prompt_total + completion_total
    return {
        "prompt_tokens": max(0, prompt_total),
        "completion_tokens": max(0, completion_total),
        "total_tokens": max(0, total_total),
    }


def _sum_usage_from_reward_metrics(reward_metrics: Mapping[str, Any]) -> Dict[str, int]:
    turns = reward_metrics.get("turns")
    if not isinstance(turns, list):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # Reward metrics only stores TE (total tokens), so keep prompt/completion unknown.
    te_total = 0.0
    for row in turns:
        if not isinstance(row, Mapping):
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, Mapping):
            continue
        te_total += _safe_float(metrics.get("TE"), 0.0)
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": max(0, int(round(te_total)))}


def _extract_usage(
    run_summary: Optional[Mapping[str, Any]],
    reward_metrics: Optional[Mapping[str, Any]],
) -> Dict[str, int]:
    if isinstance(run_summary, Mapping):
        usage = _sum_usage_from_turn_diagnostics(run_summary)
        if usage.get("total_tokens", 0) > 0:
            return usage
    if isinstance(reward_metrics, Mapping):
        usage = _sum_usage_from_reward_metrics(reward_metrics)
        if usage.get("total_tokens", 0) > 0:
            return usage
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _estimate_cost_usd(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    input_cost_per_million: float,
    output_cost_per_million: float,
) -> float:
    prompt = max(0, int(prompt_tokens))
    completion = max(0, int(completion_tokens))
    total = max(0, int(total_tokens))
    # If split usage is missing, conservatively bill all tokens at output rate.
    if prompt <= 0 and completion <= 0:
        return (total / 1_000_000.0) * max(0.0, float(output_cost_per_million))
    return (
        (prompt / 1_000_000.0) * max(0.0, float(input_cost_per_million))
        + (completion / 1_000_000.0) * max(0.0, float(output_cost_per_million))
    )


def _load_run_sidecars(run_dir: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not run_dir:
        return None, None, None
    root = Path(run_dir)
    run_summary = _load_json(root / "meta" / "run_summary.json")
    reward_metrics = _load_json(root / "meta" / "reward_metrics.json")
    reward_v1 = _load_json(root / "meta" / "reward_v1.json")
    return run_summary, reward_metrics, reward_v1


def _extract_guardrail_telemetry(run_summary: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(run_summary, Mapping):
        return {"guard_trigger_count": 0, "guard_trigger_by_name": {}}
    events = run_summary.get("guardrail_events")
    counters: Dict[str, int] = {}
    total = 0
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            name = str(event.get("guardrail") or event.get("name") or "unknown")
            counters[name] = int(counters.get(name) or 0) + 1
            total += 1
    fallback = run_summary.get("guardrails")
    if not counters and isinstance(fallback, Mapping):
        for key, value in fallback.items():
            if key in {"events", "warnings", "aborts"}:
                continue
            try:
                parsed = int(value)
            except Exception:
                continue
            if parsed > 0:
                counters[str(key)] = parsed
                total += parsed
    return {
        "guard_trigger_count": int(total),
        "guard_trigger_by_name": counters,
    }


def _build_runtime_overrides(
    base_overrides: Mapping[str, Any],
    *,
    model_override: Optional[str],
) -> Dict[str, Any]:
    out = dict(base_overrides)
    if isinstance(model_override, str) and model_override.strip():
        out["providers.default_model"] = model_override.strip()
    return out


def _run_one(
    *,
    label: str,
    config_path: Path,
    workspace_root: Path,
    task: LiveTask,
    model_override: Optional[str],
    dry_run: bool,
    dry_run_seed: int,
    overrides: Optional[Mapping[str, Any]] = None,
    pricing_input_per_million: float,
    pricing_output_per_million: float,
) -> Dict[str, Any]:
    if dry_run:
        rng = random.Random(f"{dry_run_seed}:{label}:{task.task_id}")
        prompt_tokens = rng.randint(600, 1400)
        completion_tokens = rng.randint(200, 900)
        total_tokens = prompt_tokens + completion_tokens
        completed = rng.random() > 0.15
        completion_reason = "task_completed" if completed else "max_steps_reached"
        episodes = 1 if label == "baseline" else rng.randint(1, 2)
        estimated_cost = _estimate_cost_usd(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            input_cost_per_million=pricing_input_per_million,
            output_cost_per_million=pricing_output_per_million,
        )
        return {
            "label": label,
            "config_path": str(config_path),
            "task_id": task.task_id,
            "dry_run": True,
            "completed": completed,
            "completion_reason": completion_reason,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "estimated_cost_usd": estimated_cost,
            "longrun_summary": {
                "episodes_run": episodes,
                "stop_reason": "episode_completed" if completed else "max_episodes_reached",
                "completed": completed,
            }
            if label == "longrun"
            else None,
            "wall_clock_seconds": round(rng.uniform(3.0, 15.0), 3),
            "run_dir": None,
            "errors": [],
            "telemetry": {
                "arm_status": "completed" if completed else "incomplete",
                "failure_class": "none" if completed else completion_reason,
                "guard_trigger_count": 0,
                "guard_trigger_by_name": {},
                "timeout_hit": False,
                "process_error": None,
            },
        }

    from agentic_coder_prototype.agent import AgenticCoder

    run_started = time.perf_counter()
    runtime_overrides = _build_runtime_overrides(overrides or {}, model_override=model_override)
    workspace_dir = workspace_root / label / task.task_id
    coder = AgenticCoder(str(config_path), workspace_dir=str(workspace_dir), overrides=runtime_overrides or None)
    result = coder.run_task(task.prompt, max_iterations=task.max_steps, context=task.context)
    run_elapsed = max(0.0, time.perf_counter() - run_started)

    run_dir = result.get("run_dir") or result.get("logging_dir")
    run_summary, reward_metrics, reward_v1 = _load_run_sidecars(str(run_dir) if run_dir else None)
    completion_summary = result.get("completion_summary")
    if not isinstance(completion_summary, Mapping):
        completion_summary = (run_summary or {}).get("completion_summary") if isinstance(run_summary, Mapping) else {}
    if not isinstance(completion_summary, Mapping):
        completion_summary = {}

    usage = _extract_usage(run_summary, reward_metrics)
    estimated_cost = _estimate_cost_usd(
        prompt_tokens=_safe_int(usage.get("prompt_tokens"), 0),
        completion_tokens=_safe_int(usage.get("completion_tokens"), 0),
        total_tokens=_safe_int(usage.get("total_tokens"), 0),
        input_cost_per_million=pricing_input_per_million,
        output_cost_per_million=pricing_output_per_million,
    )

    longrun_summary = None
    if isinstance(run_summary, Mapping):
        longrun_block = run_summary.get("longrun")
        if isinstance(longrun_block, Mapping) and isinstance(longrun_block.get("summary"), Mapping):
            longrun_summary = dict(longrun_block.get("summary") or {})
    guard_telemetry = _extract_guardrail_telemetry(run_summary)

    completed = bool(result.get("completed", completion_summary.get("completed", False)))
    completion_reason = str(result.get("completion_reason") or completion_summary.get("reason") or "unknown")
    out: Dict[str, Any] = {
        "label": label,
        "config_path": str(config_path),
        "task_id": task.task_id,
        "dry_run": False,
        "completed": completed,
        "completion_reason": completion_reason,
        "completion_summary": dict(completion_summary),
        "usage": usage,
        "estimated_cost_usd": estimated_cost,
        "wall_clock_seconds": round(run_elapsed, 3),
        "run_dir": str(run_dir) if run_dir else None,
        "errors": [],
        "telemetry": {
            "arm_status": "completed" if completed else "incomplete",
            "failure_class": "none" if completed else completion_reason,
            "guard_trigger_count": int(guard_telemetry.get("guard_trigger_count") or 0),
            "guard_trigger_by_name": dict(guard_telemetry.get("guard_trigger_by_name") or {}),
            "timeout_hit": False,
            "process_error": None,
        },
    }
    if isinstance(longrun_summary, Mapping):
        out["longrun_summary"] = dict(longrun_summary)
    if isinstance(reward_v1, Mapping):
        out["episode_return"] = reward_v1.get("episode_return")
    return out


def _failed_arm_row(
    *,
    label: str,
    config_path: Path,
    task: LiveTask,
    reason: str,
    error_message: str,
    wall_clock_seconds: float,
) -> Dict[str, Any]:
    return {
        "label": label,
        "config_path": str(config_path),
        "task_id": task.task_id,
        "dry_run": False,
        "completed": False,
        "completion_reason": reason,
        "completion_summary": {
            "completed": False,
            "reason": reason,
            "method": "guardrail",
            "exit_kind": "guardrail",
            "steps_taken": 0,
            "max_steps": int(task.max_steps),
        },
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "estimated_cost_usd": 0.0,
        "wall_clock_seconds": round(max(0.0, float(wall_clock_seconds)), 3),
        "run_dir": None,
        "errors": [f"{reason}: {error_message}"],
        "telemetry": {
            "arm_status": "failed",
            "failure_class": str(reason),
            "guard_trigger_count": 0,
            "guard_trigger_by_name": {},
            "timeout_hit": bool(reason == "arm_timeout"),
            "process_error": str(error_message),
        },
    }


def _run_one_process_entry(queue: Any, kwargs: Mapping[str, Any]) -> None:
    try:
        row = _run_one(**kwargs)
        queue.put({"ok": True, "row": row})
    except Exception as exc:  # pragma: no cover - exercised via timeout/guard behavior
        queue.put(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )


def _run_one_with_guard(
    *,
    label: str,
    config_path: Path,
    workspace_root: Path,
    task: LiveTask,
    model_override: Optional[str],
    dry_run: bool,
    dry_run_seed: int,
    overrides: Optional[Mapping[str, Any]],
    pricing_input_per_million: float,
    pricing_output_per_million: float,
    arm_timeout_seconds: float,
) -> Dict[str, Any]:
    run_kwargs = {
        "label": label,
        "config_path": config_path,
        "workspace_root": workspace_root,
        "task": task,
        "model_override": model_override,
        "dry_run": bool(dry_run),
        "dry_run_seed": int(dry_run_seed),
        "overrides": overrides,
        "pricing_input_per_million": float(pricing_input_per_million),
        "pricing_output_per_million": float(pricing_output_per_million),
    }

    # Dry-run mode is deterministic and fast; don't pay process-spawn overhead.
    if bool(dry_run) or float(arm_timeout_seconds) <= 0.0:
        started = time.perf_counter()
        try:
            return _run_one(**run_kwargs)
        except Exception as exc:
            elapsed = max(0.0, time.perf_counter() - started)
            return _failed_arm_row(
                label=label,
                config_path=config_path,
                task=task,
                reason="arm_exception",
                error_message=f"{type(exc).__name__}: {exc}",
                wall_clock_seconds=elapsed,
            )

    ctx = mp.get_context("spawn")
    queue: Any = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_run_one_process_entry, args=(queue, run_kwargs), daemon=True)
    started = time.perf_counter()
    proc.start()
    proc.join(timeout=max(0.0, float(arm_timeout_seconds)))
    elapsed = max(0.0, time.perf_counter() - started)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5.0)
        if proc.is_alive():  # pragma: no cover - extreme fallback
            proc.kill()
            proc.join(timeout=2.0)
        return _failed_arm_row(
            label=label,
            config_path=config_path,
            task=task,
            reason="arm_timeout",
            error_message=f"exceeded arm timeout of {float(arm_timeout_seconds):.1f}s",
            wall_clock_seconds=elapsed,
        )

    payload: Optional[Mapping[str, Any]] = None
    try:
        if not queue.empty():
            raw = queue.get_nowait()
            if isinstance(raw, Mapping):
                payload = raw
    except Exception:
        payload = None
    finally:
        queue.close()
        queue.join_thread()

    if not payload:
        return _failed_arm_row(
            label=label,
            config_path=config_path,
            task=task,
            reason="arm_process_no_result",
            error_message=f"process exited with code {proc.exitcode}",
            wall_clock_seconds=elapsed,
        )

    if not bool(payload.get("ok")):
        error_text = str(payload.get("error") or "unknown process error")
        return _failed_arm_row(
            label=label,
            config_path=config_path,
            task=task,
            reason="arm_exception",
            error_message=error_text,
            wall_clock_seconds=elapsed,
        )

    row = payload.get("row")
    if not isinstance(row, Mapping):
        return _failed_arm_row(
            label=label,
            config_path=config_path,
            task=task,
            reason="arm_invalid_result",
            error_message="runner returned non-object result",
            wall_clock_seconds=elapsed,
        )
    return dict(row)


def _compute_arm_summary(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows_list = list(rows)
    run_count = len(rows_list)
    completed = sum(1 for row in rows_list if bool(row.get("completed")))
    tokens = [int((row.get("usage") or {}).get("total_tokens") or 0) for row in rows_list]
    costs = [float(row.get("estimated_cost_usd") or 0.0) for row in rows_list]
    wall_clock = [float(row.get("wall_clock_seconds") or 0.0) for row in rows_list]
    guard_trigger_total = 0
    timeout_count = 0
    failure_classes: Dict[str, int] = {}
    for row in rows_list:
        telemetry = row.get("telemetry")
        if isinstance(telemetry, Mapping):
            guard_trigger_total += max(0, _safe_int(telemetry.get("guard_trigger_count"), 0))
            if bool(telemetry.get("timeout_hit")):
                timeout_count += 1
            failure_class = str(telemetry.get("failure_class") or "")
            if failure_class and failure_class != "none":
                failure_classes[failure_class] = int(failure_classes.get(failure_class) or 0) + 1
    return {
        "run_count": run_count,
        "success_rate": (completed / run_count) if run_count else 0.0,
        "median_tokens": statistics.median(tokens) if tokens else 0.0,
        "median_estimated_cost_usd": statistics.median(costs) if costs else 0.0,
        "median_wall_clock_seconds": statistics.median(wall_clock) if wall_clock else 0.0,
        "guard_trigger_total": int(guard_trigger_total),
        "timeout_count": int(timeout_count),
        "failure_classes": failure_classes,
    }


def _render_markdown(payload: Mapping[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# LongRun Phase-2 Live Pilot Summary")
    lines.append("")
    lines.append(f"- Generated: `{payload.get('generated_at')}`")
    lines.append(f"- Dry-run: `{bool(payload.get('dry_run'))}`")
    lines.append(f"- Schema: `{payload.get('schema_version')}`")
    lines.append("")
    caps = payload.get("caps") if isinstance(payload.get("caps"), Mapping) else {}
    totals = payload.get("totals") if isinstance(payload.get("totals"), Mapping) else {}
    lines.append("## Caps / Totals")
    lines.append("")
    lines.append("| Metric | Cap | Observed |")
    lines.append("|---|---:|---:|")
    lines.append(
        f"| Total tokens | `{caps.get('max_total_tokens')}` | `{totals.get('total_tokens')}` |"
    )
    lines.append(
        f"| Estimated cost (USD) | `{caps.get('max_total_estimated_cost_usd')}` | `{totals.get('total_estimated_cost_usd')}` |"
    )
    lines.append("")
    lines.append("## Arm Summary")
    lines.append("")
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    baseline = summary.get("baseline") if isinstance(summary.get("baseline"), Mapping) else {}
    longrun = summary.get("longrun") if isinstance(summary.get("longrun"), Mapping) else {}
    lines.append("| Arm | Runs | Success Rate | Median Tokens | Median Est. Cost | Median Wall Clock (s) | Guard Triggers | Timeouts |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| baseline | `{baseline.get('run_count', 0)}` | `{baseline.get('success_rate', 0.0):.3f}` | "
        f"`{baseline.get('median_tokens', 0)}` | `{baseline.get('median_estimated_cost_usd', 0.0):.6f}` | `{baseline.get('median_wall_clock_seconds', 0.0):.3f}` | "
        f"`{baseline.get('guard_trigger_total', 0)}` | `{baseline.get('timeout_count', 0)}` |"
    )
    lines.append(
        f"| longrun | `{longrun.get('run_count', 0)}` | `{longrun.get('success_rate', 0.0):.3f}` | "
        f"`{longrun.get('median_tokens', 0)}` | `{longrun.get('median_estimated_cost_usd', 0.0):.6f}` | `{longrun.get('median_wall_clock_seconds', 0.0):.3f}` | "
        f"`{longrun.get('guard_trigger_total', 0)}` | `{longrun.get('timeout_count', 0)}` |"
    )
    lines.append("")
    lines.append("## Pair Results")
    lines.append("")
    lines.append("| Task | Baseline | LongRun | Token Delta | Cost Delta (USD) |")
    lines.append("|---|---|---|---:|---:|")
    for pair in payload.get("pairs", []) if isinstance(payload.get("pairs"), list) else []:
        if not isinstance(pair, Mapping):
            continue
        task_id = str(pair.get("task_id") or "")
        baseline_row = pair.get("baseline") if isinstance(pair.get("baseline"), Mapping) else {}
        longrun_row = pair.get("longrun") if isinstance(pair.get("longrun"), Mapping) else {}
        b_ok = "ok" if baseline_row.get("completed") else "fail"
        l_ok = "ok" if longrun_row.get("completed") else "fail"
        b_tokens = int((baseline_row.get("usage") or {}).get("total_tokens") or 0)
        l_tokens = int((longrun_row.get("usage") or {}).get("total_tokens") or 0)
        b_cost = float(baseline_row.get("estimated_cost_usd") or 0.0)
        l_cost = float(longrun_row.get("estimated_cost_usd") or 0.0)
        lines.append(
            f"| `{task_id}` | `{b_ok}` | `{l_ok}` | `{l_tokens - b_tokens}` | `{l_cost - b_cost:.6f}` |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a spend-capped live Phase-2 longrun pilot.")
    parser.add_argument("--baseline-config", default="agent_configs/base_v2.yaml")
    parser.add_argument("--longrun-config", default="agent_configs/longrun_conservative_v1.yaml")
    parser.add_argument("--tasks-json", help="Optional task list JSON path. Defaults to built-in lightweight tasks.")
    parser.add_argument("--out-json", required=True, help="Output artifact JSON path.")
    parser.add_argument("--out-markdown", help="Optional markdown summary path.")
    parser.add_argument("--workspace-root", default="tmp/longrun_phase2_live_runs")
    parser.add_argument("--max-pairs", type=int, default=3)
    parser.add_argument("--max-total-tokens", type=int, default=40000)
    parser.add_argument("--max-total-estimated-cost-usd", type=float, default=2.0)
    parser.add_argument(
        "--arm-timeout-seconds",
        type=float,
        default=DEFAULT_ARM_TIMEOUT_SECONDS,
        help="Per-arm timeout in seconds. On timeout, arm is marked failed and pilot continues.",
    )
    parser.add_argument("--input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--output-cost-per-million", type=float, default=0.0)
    parser.add_argument("--model-override", help="Optional providers.default_model override for both arms.")
    parser.add_argument("--baseline-overrides-json", help="Optional JSON object of dotted-path overrides.")
    parser.add_argument("--longrun-overrides-json", help="Optional JSON object of dotted-path overrides.")
    parser.add_argument("--dry-run", action="store_true", help="No provider calls; synthetic data only.")
    parser.add_argument("--dry-run-seed", type=int, default=7)
    parser.add_argument(
        "--allow-missing-keys",
        action="store_true",
        help="Bypass provider key checks (intended for dry-run/development only).",
    )
    return parser.parse_args()


def _parse_overrides(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("override payload must be a JSON object")
    return dict(payload)


def _ensure_provider_keys(*, dry_run: bool, allow_missing_keys: bool) -> None:
    if dry_run or allow_missing_keys:
        return
    if any(os.environ.get(key) for key in DEFAULT_REQUIRE_KEYS):
        return
    keys = ", ".join(DEFAULT_REQUIRE_KEYS)
    raise RuntimeError(f"missing provider credentials; set one of: {keys}")


def _cap_reached(*, totals_tokens: int, totals_cost: float, max_tokens: int, max_cost: float) -> Optional[str]:
    if max_tokens > 0 and totals_tokens >= max_tokens:
        return "max_total_tokens_reached"
    if max_cost > 0 and totals_cost >= max_cost:
        return "max_total_estimated_cost_usd_reached"
    return None


def main() -> int:
    args = parse_args()
    _ensure_provider_keys(dry_run=bool(args.dry_run), allow_missing_keys=bool(args.allow_missing_keys))

    baseline_config = Path(args.baseline_config)
    longrun_config = Path(args.longrun_config)
    out_json = Path(args.out_json)
    out_markdown = Path(args.out_markdown) if args.out_markdown else None
    workspace_root = Path(args.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)

    if args.tasks_json:
        tasks = _parse_tasks_payload(Path(args.tasks_json))
    else:
        tasks = _default_tasks()
    if not tasks:
        raise RuntimeError("no tasks available to run")
    max_pairs = max(1, int(args.max_pairs))
    tasks = tasks[:max_pairs]

    baseline_overrides = _parse_overrides(args.baseline_overrides_json)
    longrun_overrides = dict(DEFAULT_LONGRUN_OVERRIDES)
    longrun_overrides.update(_parse_overrides(args.longrun_overrides_json))

    totals_tokens = 0
    totals_prompt_tokens = 0
    totals_completion_tokens = 0
    totals_estimated_cost = 0.0
    pairs: List[Dict[str, Any]] = []
    stop_reason: Optional[str] = None
    budget_exceeded = False

    for task in tasks:
        pre_cap_reason = _cap_reached(
            totals_tokens=totals_tokens,
            totals_cost=totals_estimated_cost,
            max_tokens=max(0, int(args.max_total_tokens)),
            max_cost=max(0.0, float(args.max_total_estimated_cost_usd)),
        )
        if pre_cap_reason:
            stop_reason = pre_cap_reason
            budget_exceeded = True
            break

        pair_row: Dict[str, Any] = {"task_id": task.task_id, "prompt": task.prompt}
        for label, cfg_path, overrides in (
            ("baseline", baseline_config, baseline_overrides),
            ("longrun", longrun_config, longrun_overrides),
        ):
            pre_cap_reason = _cap_reached(
                totals_tokens=totals_tokens,
                totals_cost=totals_estimated_cost,
                max_tokens=max(0, int(args.max_total_tokens)),
                max_cost=max(0.0, float(args.max_total_estimated_cost_usd)),
            )
            if pre_cap_reason:
                stop_reason = pre_cap_reason
                budget_exceeded = True
                break
            run_row = _run_one_with_guard(
                label=label,
                config_path=cfg_path,
                workspace_root=workspace_root,
                task=task,
                model_override=args.model_override,
                dry_run=bool(args.dry_run),
                dry_run_seed=int(args.dry_run_seed),
                overrides=overrides,
                pricing_input_per_million=float(args.input_cost_per_million),
                pricing_output_per_million=float(args.output_cost_per_million),
                arm_timeout_seconds=float(args.arm_timeout_seconds),
            )
            usage = run_row.get("usage") if isinstance(run_row.get("usage"), Mapping) else {}
            totals_prompt_tokens += max(0, _safe_int(usage.get("prompt_tokens"), 0))
            totals_completion_tokens += max(0, _safe_int(usage.get("completion_tokens"), 0))
            totals_tokens += max(0, _safe_int(usage.get("total_tokens"), 0))
            totals_estimated_cost += max(0.0, _safe_float(run_row.get("estimated_cost_usd"), 0.0))
            pair_row[label] = run_row
        pairs.append(pair_row)
        if budget_exceeded:
            break

    post_cap_reason = _cap_reached(
        totals_tokens=totals_tokens,
        totals_cost=totals_estimated_cost,
        max_tokens=max(0, int(args.max_total_tokens)),
        max_cost=max(0.0, float(args.max_total_estimated_cost_usd)),
    )
    if post_cap_reason and not stop_reason:
        stop_reason = post_cap_reason
        budget_exceeded = True

    baseline_rows = [pair.get("baseline", {}) for pair in pairs if isinstance(pair, Mapping) and isinstance(pair.get("baseline"), Mapping)]
    longrun_rows = [pair.get("longrun", {}) for pair in pairs if isinstance(pair, Mapping) and isinstance(pair.get("longrun"), Mapping)]
    baseline_summary = _compute_arm_summary(baseline_rows)
    longrun_summary = _compute_arm_summary(longrun_rows)

    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "dry_run": bool(args.dry_run),
        "configs": {
            "baseline": str(baseline_config),
            "longrun": str(longrun_config),
            "model_override": args.model_override,
        },
        "pricing": {
            "input_cost_per_million": float(args.input_cost_per_million),
            "output_cost_per_million": float(args.output_cost_per_million),
        },
        "caps": {
            "max_pairs": max_pairs,
            "max_total_tokens": max(0, int(args.max_total_tokens)),
            "max_total_estimated_cost_usd": max(0.0, float(args.max_total_estimated_cost_usd)),
        },
        "totals": {
            "runs_executed": len(baseline_rows) + len(longrun_rows),
            "pair_rows": len(pairs),
            "prompt_tokens": totals_prompt_tokens,
            "completion_tokens": totals_completion_tokens,
            "total_tokens": totals_tokens,
            "total_estimated_cost_usd": round(totals_estimated_cost, 8),
            "budget_exceeded": bool(budget_exceeded),
            "stop_reason": stop_reason,
        },
        "summary": {
            "baseline": baseline_summary,
            "longrun": longrun_summary,
            "delta_success_rate": float(longrun_summary.get("success_rate", 0.0)) - float(baseline_summary.get("success_rate", 0.0)),
            "delta_median_tokens": float(longrun_summary.get("median_tokens", 0.0)) - float(baseline_summary.get("median_tokens", 0.0)),
            "delta_median_estimated_cost_usd": float(longrun_summary.get("median_estimated_cost_usd", 0.0))
            - float(baseline_summary.get("median_estimated_cost_usd", 0.0)),
        },
        "pairs": pairs,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if out_markdown:
        out_markdown.parent.mkdir(parents=True, exist_ok=True)
        out_markdown.write_text(_render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
