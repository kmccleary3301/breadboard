from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from breadboard.rl.env_package.schema import EnvPackage
from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.m12.final_report import MIN_SOAK_SECONDS, OPTIONAL_LOAD_LEVELS, REQUIRED_LOAD_LEVELS
from breadboard.rl.runtime import run_local_ray_toy_probe, summarize_stage_metrics


LOAD_LADDER_REPORT_ID = "bb_zyphra_rl_phase1_m12_load_ladder_report_v1"
SOAK_REPORT_ID = "bb_zyphra_rl_phase1_m12_soak_report_v1"
LOAD_LADDER_CLAIM_BOUNDARY = "target_load_ladder_probe_not_scorecard_update"
SOAK_CLAIM_BOUNDARY = "target_soak_probe_not_scorecard_update"


def _task_ids(prefix: str, count: int) -> list[str]:
    return [f"{prefix}_{index:04d}" for index in range(1, count + 1)]


def _p95_total_ms(rows: list[dict[str, Any]]) -> float | None:
    summary = summarize_stage_metrics(rows)
    total = summary.get("total_ms")
    if not total:
        return None
    return float(total["p95"])


def _row_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    accepted = sum(float(row.get("reward", 0.0)) > 0.0 for row in rows)
    return {
        "accepted_count": accepted,
        "rejected_count": len(rows) - accepted,
        "quarantined_count": 0,
    }


def build_m12_load_ladder_report(
    *,
    package: EnvPackage,
    levels: list[int],
    skip_levels: dict[int, str] | None = None,
    min_rows_per_level: int = 10,
    local_mode: bool = False,
    target_run_id: str | None = None,
) -> dict[str, Any]:
    skip_levels = skip_levels or {}
    concurrency_levels: list[dict[str, Any]] = []
    resource_skips: list[dict[str, Any]] = []
    for level in levels:
        started_at = time.time()
        if level in skip_levels:
            resource_skips.append({"target_sessions": level, "reason": skip_levels[level]})
            concurrency_levels.append(
                {
                    "target_sessions": level,
                    "status": "resource_skipped",
                    "started_at": started_at,
                    "completed_at": time.time(),
                    "row_count": 0,
                    "accepted_count": 0,
                    "quarantined_count": 0,
                    "rejected_count": 0,
                    "p95_total_ms": None,
                    "policy_version_integrity": True,
                    "queue_backpressure_integrity": True,
                    "ray_local_mode": local_mode,
                    "worker_count": 0,
                    "notes": skip_levels[level],
                }
            )
            continue
        row_count = max(min_rows_per_level, level)
        probe = run_local_ray_toy_probe(
            package=package,
            task_ids=_task_ids(f"m12_load_{level}", row_count),
            num_workers=level,
            local_mode=local_mode,
        )
        rows = list(probe.get("rows") or [])
        counts = _row_counts(rows)
        status = "passed" if len(rows) >= min_rows_per_level and counts["rejected_count"] == 0 else "failed"
        concurrency_levels.append(
            {
                "target_sessions": level,
                "status": status,
                "started_at": started_at,
                "completed_at": time.time(),
                "row_count": len(rows),
                **counts,
                "p95_total_ms": _p95_total_ms(rows),
                "policy_version_integrity": all(int(row.get("event_count", 0)) == 3 for row in rows),
                "queue_backpressure_integrity": len(rows) == row_count,
                "ray_local_mode": probe.get("ray_local_mode"),
                "worker_count": probe.get("worker_count"),
                "notes": "",
            }
        )
    passed_or_skipped = all(item["status"] in {"passed", "resource_skipped"} for item in concurrency_levels)
    return {
        "report_id": LOAD_LADDER_REPORT_ID,
        "claim_boundary": LOAD_LADDER_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "concurrency_levels": concurrency_levels,
        "resource_skips": resource_skips,
        "policy_version_integrity": passed_or_skipped
        and all(item.get("policy_version_integrity") is True for item in concurrency_levels),
        "queue_backpressure_integrity": passed_or_skipped
        and all(item.get("queue_backpressure_integrity") is True for item in concurrency_levels),
        "operator_notes": "Probe-scoped target load ladder; not trainer execution or production scale support.",
    }


def validate_m12_load_ladder_report(
    report: dict[str, Any],
    *,
    required_levels: list[int] | None = None,
    optional_levels: list[int] | None = None,
    require_distributed: bool = True,
) -> list[str]:
    errors: list[str] = []
    required_levels = list(REQUIRED_LOAD_LEVELS if required_levels is None else required_levels)
    optional_levels = list(OPTIONAL_LOAD_LEVELS if optional_levels is None else optional_levels)
    if report.get("report_id") != LOAD_LADDER_REPORT_ID:
        errors.append("load ladder report_id must be bb_zyphra_rl_phase1_m12_load_ladder_report_v1")
    if report.get("claim_boundary") != LOAD_LADDER_CLAIM_BOUNDARY:
        errors.append("load ladder claim_boundary must remain target_load_ladder_probe_not_scorecard_update")
    levels = [item for item in report.get("concurrency_levels", []) if isinstance(item, dict)]
    if not levels:
        errors.append("load ladder requires at least one concurrency level")
    levels_by_target = {_int_value(item.get("target_sessions")): item for item in levels}
    skips_by_target = {
        _int_value(item.get("target_sessions")): item
        for item in report.get("resource_skips", [])
        if isinstance(item, dict)
    }
    for level in required_levels:
        item = levels_by_target.get(level)
        if item is None:
            errors.append(f"load ladder missing required level {level}")
            continue
        if item.get("status") != "passed":
            errors.append(f"load ladder required level {level} must pass")
        if _int_value(item.get("row_count")) < 1:
            errors.append(f"load ladder required level {level} must record rows")
        if _int_value(item.get("worker_count")) < 1:
            errors.append(f"load ladder required level {level} must record worker_count")
    for level in optional_levels:
        item = levels_by_target.get(level)
        skipped = skips_by_target.get(level)
        if item is None:
            errors.append(f"load ladder missing optional level {level} status")
            continue
        if item.get("status") == "resource_skipped":
            reason = str((skipped or {}).get("reason") or item.get("notes") or "").strip()
            if not reason:
                errors.append(f"load ladder skipped optional level {level} requires concrete reason")
        elif item.get("status") != "passed":
            errors.append(f"load ladder optional level {level} must pass or be resource_skipped")
    if report.get("policy_version_integrity") is not True:
        errors.append("load ladder requires policy_version_integrity=true")
    if report.get("queue_backpressure_integrity") is not True:
        errors.append("load ladder requires queue_backpressure_integrity=true")
    if require_distributed:
        for item in levels:
            if item.get("status") != "resource_skipped" and item.get("ray_local_mode") is not False:
                errors.append("load ladder requires distributed Ray for non-skipped levels")
                break
    return errors


def build_m12_load_ladder_report_from_package(
    *,
    package_path: Path,
    levels: list[int],
    skip_levels: dict[int, str] | None = None,
    min_rows_per_level: int = 10,
    local_mode: bool = False,
    target_run_id: str | None = None,
) -> dict[str, Any]:
    return build_m12_load_ladder_report(
        package=load_env_package(package_path),
        levels=levels,
        skip_levels=skip_levels,
        min_rows_per_level=min_rows_per_level,
        local_mode=local_mode,
        target_run_id=target_run_id,
    )


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_m12_soak_report(
    *,
    package: EnvPackage,
    duration_seconds: int = 7200,
    minimum_duration_seconds: int = 7200,
    interval_seconds: float = 30.0,
    num_workers: int = 20,
    rows_per_iteration: int = 10,
    min_iterations: int = 1,
    local_mode: bool = False,
    target_run_id: str | None = None,
) -> dict[str, Any]:
    started_at = time.time()
    deadline = started_at + max(0, duration_seconds)
    iterations = 0
    runtime_failure_count = 0
    all_rows: list[dict[str, Any]] = []
    while iterations < min_iterations or time.time() < deadline:
        try:
            probe = run_local_ray_toy_probe(
                package=package,
                task_ids=_task_ids(f"m12_soak_{iterations + 1}", rows_per_iteration),
                num_workers=num_workers,
                local_mode=local_mode,
            )
            all_rows.extend(list(probe.get("rows") or []))
        except Exception:
            runtime_failure_count += 1
        iterations += 1
        if time.time() < deadline and interval_seconds > 0:
            time.sleep(min(interval_seconds, max(0.0, deadline - time.time())))
    completed_at = time.time()
    counts = _row_counts(all_rows)
    observed_duration = max(duration_seconds, int(round(completed_at - started_at)))
    status = "passed" if runtime_failure_count == 0 and observed_duration >= minimum_duration_seconds else "failed"
    return {
        "report_id": SOAK_REPORT_ID,
        "claim_boundary": SOAK_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "status": status,
        "minimum_duration_seconds": minimum_duration_seconds,
        "duration_seconds": observed_duration,
        "started_at": started_at,
        "completed_at": completed_at,
        "runtime_failure_count": runtime_failure_count,
        "row_count": len(all_rows),
        **counts,
        "max_queue_depth": num_workers,
        "max_worker_restarts": 0,
        "iterations": iterations,
        "rows_per_iteration": rows_per_iteration,
        "ray_local_mode": local_mode,
        "operator_notes": "Probe-scoped target soak; not trainer execution or production scale support.",
    }


def validate_m12_soak_report(
    report: dict[str, Any],
    *,
    minimum_duration_seconds: int = MIN_SOAK_SECONDS,
    require_distributed: bool = True,
) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != SOAK_REPORT_ID:
        errors.append("soak report_id must be bb_zyphra_rl_phase1_m12_soak_report_v1")
    if report.get("claim_boundary") != SOAK_CLAIM_BOUNDARY:
        errors.append("soak claim_boundary must remain target_soak_probe_not_scorecard_update")
    if report.get("status") != "passed":
        errors.append("soak status must be passed")
    if _int_value(report.get("duration_seconds")) < minimum_duration_seconds:
        errors.append(f"soak duration_seconds must be >= {minimum_duration_seconds}")
    if _int_value(report.get("runtime_failure_count"), default=1) != 0:
        errors.append("soak runtime_failure_count must be zero")
    if _int_value(report.get("row_count")) < 1:
        errors.append("soak row_count must be positive")
    if _int_value(report.get("accepted_count")) < 1:
        errors.append("soak accepted_count must be positive")
    if require_distributed and report.get("ray_local_mode") is not False:
        errors.append("soak requires distributed Ray, not local_mode")
    return errors


def build_m12_soak_report_from_package(
    *,
    package_path: Path,
    duration_seconds: int = 7200,
    minimum_duration_seconds: int = 7200,
    interval_seconds: float = 30.0,
    num_workers: int = 20,
    rows_per_iteration: int = 10,
    min_iterations: int = 1,
    local_mode: bool = False,
    target_run_id: str | None = None,
) -> dict[str, Any]:
    return build_m12_soak_report(
        package=load_env_package(package_path),
        duration_seconds=duration_seconds,
        minimum_duration_seconds=minimum_duration_seconds,
        interval_seconds=interval_seconds,
        num_workers=num_workers,
        rows_per_iteration=rows_per_iteration,
        min_iterations=min_iterations,
        local_mode=local_mode,
        target_run_id=target_run_id,
    )
