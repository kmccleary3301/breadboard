#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT_DIR / "artifacts" / "maintenance" / "devx_full_pass_latest.json"


def _ms_to_s(value: int) -> float:
    return round(max(0, value) / 1000.0, 3)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("report payload must be an object")
    return payload


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    timing = payload.get("timing")
    if not isinstance(timing, dict):
        raise ValueError("missing timing block in full-pass report")
    bootstrap_ms = int(timing.get("bootstrap_duration_ms", 0))
    smoke_ms = int(timing.get("devx_smoke_duration_ms", 0))
    total_ms = max(0, bootstrap_ms + smoke_ms)
    return {
        "profile": payload.get("profile", "unknown"),
        "started_at_utc": payload.get("started_at_utc"),
        "ended_at_utc": payload.get("ended_at_utc"),
        "bootstrap_duration_ms": bootstrap_ms,
        "devx_smoke_duration_ms": smoke_ms,
        "total_duration_ms": total_ms,
        "bootstrap_duration_s": _ms_to_s(bootstrap_ms),
        "devx_smoke_duration_s": _ms_to_s(smoke_ms),
        "total_duration_s": _ms_to_s(total_ms),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize devx full-pass timing report.")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Path to devx_full_pass_latest.json report.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()

    report_path = Path(args.input).expanduser().resolve()
    if not report_path.exists():
        raise SystemExit(f"missing report: {report_path}")

    payload = _load(report_path)
    summary = _summary(payload)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"[devx-timing] report: {report_path}")
    print(f"- profile: {summary['profile']}")
    print(f"- started_at_utc: {summary['started_at_utc']}")
    print(f"- ended_at_utc: {summary['ended_at_utc']}")
    print(f"- bootstrap: {summary['bootstrap_duration_ms']} ms ({summary['bootstrap_duration_s']} s)")
    print(f"- devx_smoke: {summary['devx_smoke_duration_ms']} ms ({summary['devx_smoke_duration_s']} s)")
    print(f"- total: {summary['total_duration_ms']} ms ({summary['total_duration_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
