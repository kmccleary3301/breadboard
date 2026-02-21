#!/usr/bin/env python3
"""
Emit a stable JSON health summary for phase4 fullpane workflow runs.

This is intended for CI artifacts and machine-readable trend checks.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LANES: tuple[str, ...] = ("streaming", "todo", "subagents", "everything")
SCHEMA_VERSION = "phase4_run_health_v1"


def _coerce_int(value: str) -> int | None:
    raw = str(value or "").strip()
    if raw == "":
        return None
    return int(raw)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return None


def _frame_count(index_jsonl: Path) -> int | None:
    if not index_jsonl.exists() or not index_jsonl.is_file():
        return None
    count = 0
    with index_jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def _lane_health(run_dir_raw: str) -> dict[str, Any]:
    run_dir = str(run_dir_raw or "").strip()
    if not run_dir:
        return {
            "present": False,
            "run_dir": "",
            "scenario": None,
            "capture_mode": None,
            "render_profile": None,
            "frame_count": None,
            "showcase_regression_ok": None,
        }

    root = Path(run_dir).expanduser().resolve()
    payload: dict[str, Any] = {
        "present": root.exists() and root.is_dir(),
        "run_dir": str(root),
        "scenario": None,
        "capture_mode": None,
        "render_profile": None,
        "frame_count": None,
        "showcase_regression_ok": None,
    }
    if not payload["present"]:
        return payload

    manifest = _load_json(root / "scenario_manifest.json")
    if manifest:
        payload["scenario"] = manifest.get("scenario")
        payload["capture_mode"] = manifest.get("capture_mode")
        payload["render_profile"] = manifest.get("render_profile")

    payload["frame_count"] = _frame_count(root / "index.jsonl")

    showcase_report = _load_json(root / "showcase_regression_report.json")
    if showcase_report is not None:
        payload["showcase_regression_ok"] = bool(showcase_report.get("ok"))

    return payload


def _pack_health(pack_dir_raw: str) -> dict[str, Any]:
    pack_dir = str(pack_dir_raw or "").strip()
    if not pack_dir:
        return {
            "present": False,
            "pack_dir": "",
            "index_exists": False,
            "manifest_exists": False,
            "schema_report_exists": False,
            "schema_ok": None,
        }

    root = Path(pack_dir).expanduser().resolve()
    schema_report = _load_json(root / "visual_pack_schema_report.json")
    return {
        "present": root.exists() and root.is_dir(),
        "pack_dir": str(root),
        "index_exists": (root / "INDEX.md").exists(),
        "manifest_exists": (root / "manifest.json").exists(),
        "schema_report_exists": (root / "visual_pack_schema_report.json").exists(),
        "schema_ok": (None if schema_report is None else bool(schema_report.get("ok"))),
    }


def _is_zero_or_none(value: int | None) -> bool:
    return value is None or value == 0


def build_health(args: argparse.Namespace) -> dict[str, Any]:
    text_contract_rc_raw = getattr(args, "text_contract_rc", "")
    text_contract_report_raw = str(getattr(args, "text_contract_report", "") or "").strip()
    rc = {
        "scenario_rc": _coerce_int(args.scenario_rc),
        "validation_rc": _coerce_int(args.validation_rc),
        "text_contract_rc": _coerce_int(text_contract_rc_raw),
        "pack_rc": _coerce_int(args.pack_rc),
        "pack_schema_rc": _coerce_int(args.pack_schema_rc),
    }

    lanes = {
        "streaming": _lane_health(args.stream_run_dir),
        "todo": _lane_health(args.todo_run_dir),
        "subagents": _lane_health(args.subagents_run_dir),
        "everything": _lane_health(args.everything_run_dir),
    }

    pack = _pack_health(args.pack_dir)
    text_contract_report = _load_json(Path(text_contract_report_raw).expanduser().resolve()) if text_contract_report_raw else None

    lane_presence_ok = all(bool(lanes[name]["present"]) for name in LANES)
    rc_ok = all(_is_zero_or_none(v) for v in rc.values())
    pack_ok = (pack["schema_ok"] is not False) and (
        not pack["present"] or (pack["index_exists"] and pack["manifest_exists"])
    )
    text_contract_ok = (text_contract_report is None) or bool(text_contract_report.get("overall_ok"))

    overall_ok = bool(lane_presence_ok and rc_ok and pack_ok and text_contract_ok)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "overall_ok": overall_ok,
        "rc": rc,
        "lanes": lanes,
        "pack": pack,
        "text_contract": {
            "report_path": text_contract_report_raw,
            "present": text_contract_report is not None,
            "overall_ok": (None if text_contract_report is None else bool(text_contract_report.get("overall_ok"))),
            "total_runs": (None if text_contract_report is None else text_contract_report.get("total_runs")),
            "unmapped_count": (None if text_contract_report is None else text_contract_report.get("unmapped_count")),
        },
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Emit stable phase4 run health JSON")
    p.add_argument("--mode", choices=("nightly", "gate"), required=True)
    p.add_argument("--stream-run-dir", default="")
    p.add_argument("--todo-run-dir", default="")
    p.add_argument("--subagents-run-dir", default="")
    p.add_argument("--everything-run-dir", default="")
    p.add_argument("--pack-dir", default="")
    p.add_argument("--scenario-rc", default="")
    p.add_argument("--validation-rc", default="")
    p.add_argument("--text-contract-rc", default="")
    p.add_argument("--pack-rc", default="")
    p.add_argument("--pack-schema-rc", default="")
    p.add_argument("--text-contract-report", default="")
    p.add_argument("--output-json", required=True)
    p.add_argument("--strict", action="store_true", help="exit non-zero when overall_ok is false")
    return p.parse_args()


def main() -> int:
    try:
        args = parse_args()
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        health = build_health(args)
        output_path.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")
        print(f"[phase4-run-health] wrote: {output_path}")
        print(f"[phase4-run-health] overall_ok={health['overall_ok']}")
        if args.strict and not health["overall_ok"]:
            return 2
        return 0
    except Exception as exc:
        print(f"[phase4-run-health] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
