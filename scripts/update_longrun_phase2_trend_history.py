#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping


def _load_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _load_history(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("history file must be a JSON array")
    out: List[Dict[str, Any]] = []
    for row in payload:
        if isinstance(row, Mapping):
            out.append(dict(row))
    return out


def _extract_check(report: Mapping[str, Any], name: str) -> bool:
    checks = report.get("checks")
    if not isinstance(checks, list):
        return False
    for check in checks:
        if isinstance(check, Mapping) and str(check.get("name")) == name:
            return bool(check.get("passed"))
    return False


def update_history(
    report: Mapping[str, Any],
    parity: Mapping[str, Any] | None,
    history: List[Dict[str, Any]],
    *,
    max_entries: int = 200,
) -> List[Dict[str, Any]]:
    entry = {
        "ts_unix": int(time.time()),
        "report_ok": bool(report.get("ok", False)),
        "schema_version": str(report.get("schema_version") or ""),
        "scenario_matrix_ok": _extract_check(report, "scenario_count>=6"),
        "runs_per_scenario_ok": _extract_check(report, "runs_per_scenario>=5"),
        "no_success_regression_ok": _extract_check(report, "no_success_regression"),
        "boundedness_required_ok": _extract_check(report, "boundedness_improvement_required_set"),
        "stable_success_path_ok": _extract_check(report, "stable_success_path"),
        "parity_audit_ok": bool((parity or {}).get("ok", False)),
        "parity_checked": int((parity or {}).get("checked") or 0),
        "parity_failures": len((parity or {}).get("failures") or []),
        "github_ref": os.environ.get("GITHUB_REF_NAME") or "",
        "github_sha": os.environ.get("GITHUB_SHA") or "",
    }
    history.append(entry)
    if max_entries > 0 and len(history) > max_entries:
        history = history[-max_entries:]
    return history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append a longrun phase2 strict-gate report to trend history.")
    parser.add_argument("--report-json", required=True, help="Path to go/no-go report JSON.")
    parser.add_argument("--parity-json", required=True, help="Path to parity audit JSON.")
    parser.add_argument("--history-json", required=True, help="Path to history JSON array.")
    parser.add_argument("--max-entries", type=int, default=200, help="Max history entries to retain.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = _load_json(Path(args.report_json))
    parity = _load_json(Path(args.parity_json))
    history_path = Path(args.history_json)
    history = _load_history(history_path)
    updated = update_history(report, parity, history, max_entries=max(0, int(args.max_entries)))
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "history_entries": len(updated), "history_path": str(history_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
