#!/usr/bin/env python3
"""
Emit a stable JSON health summary for phase4 tier3 nightly runs.

Tier3 lanes:
- provider_codex (optional)
- provider_claude (optional)
- resize_storm (required)
- concurrency_20 (required)
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

SCHEMA_VERSION = "phase4_tier3_health_v2"


def _coerce_int(value: str) -> int | None:
    raw = str(value or "").strip()
    if raw == "":
        return None
    return int(raw)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _frame_count(run_dir: Path) -> int | None:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists() or not index_path.is_file():
        return None
    count = 0
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def _load_index_rows(run_dir: Path) -> list[dict[str, Any]]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists() or not index_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with index_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def _text_line_churn(run_dir: Path, rows: list[dict[str, Any]]) -> dict[str, float | None]:
    if len(rows) < 2:
        return {"line_churn_mean": None, "line_churn_p95": None}

    def load_lines(rel_path: str) -> list[str]:
        path = run_dir / rel_path
        if not path.exists() or not path.is_file():
            return []
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()

    churn_values: list[float] = []
    prev_lines = load_lines(str(rows[0].get("text", "")))
    for row in rows[1:]:
        cur_lines = load_lines(str(row.get("text", "")))
        max_len = max(len(prev_lines), len(cur_lines), 1)
        changed = 0
        for i in range(max_len):
            left = prev_lines[i] if i < len(prev_lines) else ""
            right = cur_lines[i] if i < len(cur_lines) else ""
            if left != right:
                changed += 1
        churn_values.append(changed / max_len)
        prev_lines = cur_lines

    return {
        "line_churn_mean": (None if not churn_values else float(mean(churn_values))),
        "line_churn_p95": _percentile(churn_values, 0.95),
    }


def _extract_status_line(lines: list[str]) -> str | None:
    if not lines:
        return None
    prompt_idx: int | None = None
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx].strip()
        if not line:
            continue
        if "for shortcuts" in line or line.startswith("❯"):
            prompt_idx = idx
            break
    if prompt_idx is None:
        return None
    for idx in range(prompt_idx - 1, -1, -1):
        line = lines[idx].strip()
        if not line:
            continue
        if "for shortcuts" in line:
            continue
        return line
    return None


def _status_surface_metrics(run_dir: Path, rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    if not rows:
        return {
            "status_line_samples": None,
            "status_line_changes": None,
            "status_line_unique": None,
            "status_line_churn_rate": None,
        }

    statuses: list[str] = []
    for row in rows:
        rel_path = str(row.get("text", ""))
        path = run_dir / rel_path
        if not path.exists() or not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        status_line = _extract_status_line(lines)
        if status_line:
            statuses.append(status_line)

    if not statuses:
        return {
            "status_line_samples": 0,
            "status_line_changes": 0,
            "status_line_unique": 0,
            "status_line_churn_rate": None,
        }

    changes = 0
    prev = statuses[0]
    for status in statuses[1:]:
        if status != prev:
            changes += 1
        prev = status
    samples = len(statuses)
    return {
        "status_line_samples": samples,
        "status_line_changes": changes,
        "status_line_unique": len(set(statuses)),
        "status_line_churn_rate": (None if samples < 2 else float(changes / (samples - 1))),
    }


def _telemetry_maxima(run_dir: Path, rows: list[dict[str, Any]]) -> dict[str, int | None]:
    telemetry_keys = (
        "statusTransitions",
        "statusCommits",
        "statusCoalesced",
        "eventCoalesced",
        "eventMaxQueueDepth",
        "workgraphMaxQueueDepth",
        "workgraphLaneTransitions",
        "workgraphDroppedTransitions",
        "workgraphLaneChurn",
        "workgraphDroppedEvents",
        "adaptiveCadenceAdjustments",
    )
    patterns = {key: re.compile(rf"{re.escape(key)}=(\d+)") for key in telemetry_keys}
    maxima: dict[str, int | None] = {f"telemetry_{key}_max": None for key in telemetry_keys}
    sample_count = 0

    for row in rows:
        rel_path = str(row.get("text", ""))
        path = run_dir / rel_path
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        row_hit = False
        for key, pattern in patterns.items():
            matches = [int(match.group(1)) for match in pattern.finditer(text)]
            if not matches:
                continue
            row_hit = True
            field = f"telemetry_{key}_max"
            current = maxima[field]
            candidate = max(matches)
            maxima[field] = candidate if current is None else max(current, candidate)
        if row_hit:
            sample_count += 1

    maxima["telemetry_samples"] = sample_count
    return maxima


def _perf_metrics(run_dir: Path) -> dict[str, float | int | None]:
    rows = _load_index_rows(run_dir)
    if not rows:
        return {
            "frame_count": None,
            "interval_p50_ms": None,
            "interval_p95_ms": None,
            "interval_max_ms": None,
            "line_churn_mean": None,
            "line_churn_p95": None,
            "status_line_samples": None,
            "status_line_changes": None,
            "status_line_unique": None,
            "status_line_churn_rate": None,
            "telemetry_statusTransitions_max": None,
            "telemetry_statusCommits_max": None,
            "telemetry_statusCoalesced_max": None,
            "telemetry_eventCoalesced_max": None,
            "telemetry_eventMaxQueueDepth_max": None,
            "telemetry_workgraphMaxQueueDepth_max": None,
            "telemetry_workgraphLaneTransitions_max": None,
            "telemetry_workgraphDroppedTransitions_max": None,
            "telemetry_workgraphLaneChurn_max": None,
            "telemetry_workgraphDroppedEvents_max": None,
            "telemetry_adaptiveCadenceAdjustments_max": None,
            "telemetry_samples": None,
        }

    timestamps = [float(row.get("timestamp", 0.0)) for row in rows if isinstance(row.get("timestamp"), (int, float))]
    intervals_ms: list[float] = []
    for i in range(1, len(timestamps)):
        intervals_ms.append(max(0.0, (timestamps[i] - timestamps[i - 1]) * 1000.0))
    churn = _text_line_churn(run_dir, rows)
    status_surface = _status_surface_metrics(run_dir, rows)
    telemetry_max = _telemetry_maxima(run_dir, rows)

    return {
        "frame_count": len(rows),
        "interval_p50_ms": _percentile(intervals_ms, 0.50),
        "interval_p95_ms": _percentile(intervals_ms, 0.95),
        "interval_max_ms": (None if not intervals_ms else float(max(intervals_ms))),
        "line_churn_mean": churn["line_churn_mean"],
        "line_churn_p95": churn["line_churn_p95"],
        "status_line_samples": status_surface["status_line_samples"],
        "status_line_changes": status_surface["status_line_changes"],
        "status_line_unique": status_surface["status_line_unique"],
        "status_line_churn_rate": status_surface["status_line_churn_rate"],
        "telemetry_statusTransitions_max": telemetry_max["telemetry_statusTransitions_max"],
        "telemetry_statusCommits_max": telemetry_max["telemetry_statusCommits_max"],
        "telemetry_statusCoalesced_max": telemetry_max["telemetry_statusCoalesced_max"],
        "telemetry_eventCoalesced_max": telemetry_max["telemetry_eventCoalesced_max"],
        "telemetry_eventMaxQueueDepth_max": telemetry_max["telemetry_eventMaxQueueDepth_max"],
        "telemetry_workgraphMaxQueueDepth_max": telemetry_max["telemetry_workgraphMaxQueueDepth_max"],
        "telemetry_workgraphLaneTransitions_max": telemetry_max["telemetry_workgraphLaneTransitions_max"],
        "telemetry_workgraphDroppedTransitions_max": telemetry_max["telemetry_workgraphDroppedTransitions_max"],
        "telemetry_workgraphLaneChurn_max": telemetry_max["telemetry_workgraphLaneChurn_max"],
        "telemetry_workgraphDroppedEvents_max": telemetry_max["telemetry_workgraphDroppedEvents_max"],
        "telemetry_adaptiveCadenceAdjustments_max": telemetry_max["telemetry_adaptiveCadenceAdjustments_max"],
        "telemetry_samples": telemetry_max["telemetry_samples"],
    }


def _lane(
    run_dir_raw: str,
    *,
    required: bool,
    optional_note: str | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    run_dir = str(run_dir_raw or "").strip()
    payload: dict[str, Any] = {
        "required": required,
        "present": False,
        "run_dir": "",
        "scenario": None,
        "scenario_result": None,
        "capture_mode": None,
        "render_profile": None,
        "frame_count": None,
        "strict_validation_ok": None,
        "skip_reason": skip_reason,
        "perf": {
            "frame_count": None,
            "interval_p50_ms": None,
            "interval_p95_ms": None,
            "interval_max_ms": None,
            "line_churn_mean": None,
            "line_churn_p95": None,
            "status_line_samples": None,
            "status_line_changes": None,
            "status_line_unique": None,
            "status_line_churn_rate": None,
            "telemetry_statusTransitions_max": None,
            "telemetry_statusCommits_max": None,
            "telemetry_statusCoalesced_max": None,
            "telemetry_eventCoalesced_max": None,
            "telemetry_eventMaxQueueDepth_max": None,
            "telemetry_workgraphMaxQueueDepth_max": None,
            "telemetry_workgraphLaneTransitions_max": None,
            "telemetry_workgraphDroppedTransitions_max": None,
            "telemetry_workgraphLaneChurn_max": None,
            "telemetry_workgraphDroppedEvents_max": None,
            "telemetry_adaptiveCadenceAdjustments_max": None,
            "telemetry_samples": None,
        },
        "optional_note": optional_note,
    }
    if not run_dir:
        return payload

    root = Path(run_dir).expanduser().resolve()
    payload["run_dir"] = str(root)
    payload["present"] = root.exists() and root.is_dir()
    if not payload["present"]:
        return payload

    manifest = _read_json(root / "scenario_manifest.json")
    if manifest:
        payload["scenario"] = manifest.get("scenario")
        payload["scenario_result"] = manifest.get("scenario_result")
        payload["capture_mode"] = manifest.get("capture_mode")
        payload["render_profile"] = manifest.get("render_profile")

    validation = _read_json(root / "validation_report.json")
    if validation is not None:
        payload["strict_validation_ok"] = bool(validation.get("ok"))

    payload["frame_count"] = _frame_count(root)
    payload["perf"] = _perf_metrics(root)
    return payload


def _lane_ok(lane: dict[str, Any]) -> bool:
    if not lane["present"]:
        return not lane["required"]
    if lane["scenario_result"] not in (None, "pass"):
        return False
    if lane["strict_validation_ok"] is False:
        return False
    return True


def build_health(args: argparse.Namespace) -> dict[str, Any]:
    rc = {
        "provider_rc": _coerce_int(args.provider_rc),
        "stress_rc": _coerce_int(args.stress_rc),
        "validation_rc": _coerce_int(args.validation_rc),
    }
    lanes = {
        "provider_codex": _lane(
            args.provider_codex_run_dir,
            required=False,
            optional_note="optional lane; skipped when no provider CLI/auth is available",
            skip_reason=(str(args.provider_codex_skip_reason or "").strip() or None),
        ),
        "provider_claude": _lane(
            args.provider_claude_run_dir,
            required=False,
            optional_note="optional lane; skipped when no provider CLI/auth is available",
            skip_reason=(str(args.provider_claude_skip_reason or "").strip() or None),
        ),
        "resize_storm": _lane(args.resize_storm_run_dir, required=True),
        "concurrency_20": _lane(args.concurrency_run_dir, required=True),
    }

    lanes_ok = all(_lane_ok(lane) for lane in lanes.values())
    rc_ok = all(v in (None, 0) for v in rc.values())
    overall_ok = bool(lanes_ok and rc_ok)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "overall_ok": overall_ok,
        "rc": rc,
        "lanes": lanes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit stable phase4 tier3 nightly health JSON")
    parser.add_argument("--mode", choices=("nightly",), default="nightly")
    parser.add_argument("--provider-codex-run-dir", default="")
    parser.add_argument("--provider-claude-run-dir", default="")
    parser.add_argument("--provider-codex-skip-reason", default="")
    parser.add_argument("--provider-claude-skip-reason", default="")
    parser.add_argument("--resize-storm-run-dir", default="")
    parser.add_argument("--concurrency-run-dir", default="")
    parser.add_argument("--provider-rc", default="")
    parser.add_argument("--stress-rc", default="")
    parser.add_argument("--validation-rc", default="")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--strict", action="store_true", help="exit non-zero when overall_ok is false")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        health = build_health(args)
        output_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")
        print(f"[phase4-tier3-health] wrote: {output_path}")
        print(f"[phase4-tier3-health] overall_ok={health['overall_ok']}")
        if args.strict and not health["overall_ok"]:
            return 2
        return 0
    except Exception as exc:
        print(f"[phase4-tier3-health] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
