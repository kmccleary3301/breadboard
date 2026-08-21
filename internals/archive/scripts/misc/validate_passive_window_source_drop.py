#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


ATP_CANDIDATES = [
    "atp_ops_digest.ops_nightly.local.json",
    "atp_ops_digest.latest.json",
    "atp_ops_digest.local.json",
    "atp_ops_digest.json",
]

EVOLAKE_CANDIDATES = [
    "evolake_toy_campaign_nightly.json",
    "evolake_toy_campaign_nightly.latest.json",
    "evolake_toy_campaign_nightly.local.json",
    "evolake_toy_campaign_nightly.ops_nightly.local.json",
    "evolake_toy_campaign_local.latest.json",
]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_first_existing(source_dirs: list[Path], names: list[str]) -> Path | None:
    for source_dir in source_dirs:
        for name in names:
            candidate = source_dir / name
            if candidate.exists():
                return candidate
    return None


def _atp_ok(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    if "overall_ok" in payload:
        return bool(payload.get("overall_ok"))
    if "decision_state" in payload:
        return str(payload.get("decision_state")).lower() == "green"
    return False


def _evolake_ok(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    if "ok" in payload:
        return bool(payload.get("ok"))
    runs_failed = payload.get("runs_failed")
    runs_requested = payload.get("runs_requested")
    runs_passed = payload.get("runs_passed")
    if isinstance(runs_failed, int) and runs_failed == 0:
        if isinstance(runs_requested, int) and isinstance(runs_passed, int):
            return runs_passed == runs_requested
        return True
    return str(payload.get("classification", "")).lower() == "stable_pass"


def _latest_local_evolake_payload(source_dirs: list[Path]) -> tuple[Path | None, dict[str, Any] | None]:
    candidates: list[Path] = []
    for source_dir in source_dirs:
        local_dir = source_dir / "evolake_toy_campaign_local"
        if not local_dir.exists() or not local_dir.is_dir():
            continue
        candidates.extend(local_dir.glob("*.json"))
    if not candidates:
        return None, None
    latest = sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]
    return latest, _load_json(latest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        action="append",
        default=[],
        help="May be supplied multiple times. If omitted, uses atp_passive_window_sources.",
    )
    parser.add_argument("--require-green", action="store_true")
    args = parser.parse_args()

    if args.source_dir:
        source_dirs = [Path(path).resolve() for path in args.source_dir]
    else:
        source_dirs = [Path("/shared_folders/querylake_server/ray_testing/ray_SCE/atp_passive_window_sources")]

    atp_path = _find_first_existing(source_dirs, ATP_CANDIDATES)
    evolake_path = _find_first_existing(source_dirs, EVOLAKE_CANDIDATES)
    evolake_local_path, evolake_local_payload = _latest_local_evolake_payload(source_dirs)

    atp_payload = _load_json(atp_path) if atp_path else None
    evolake_payload = _load_json(evolake_path) if evolake_path else None

    atp_present = atp_path is not None
    evolake_present = (evolake_path is not None) or (evolake_local_path is not None)
    atp_green = _atp_ok(atp_payload)
    evolake_green = _evolake_ok(evolake_payload) or _evolake_ok(evolake_local_payload)

    present_ok = atp_present and evolake_present
    green_ok = atp_green and evolake_green

    if not present_ok:
        classification = "missing_sources"
        rc = 2
    elif args.require_green and not green_ok:
        classification = "sources_present_not_green"
        rc = 3
    else:
        classification = "ready" if green_ok else "sources_present"
        rc = 0

    result = {
        "schema": "breadboard.passive_window_source_drop_check.v1",
        "generated_at": time.time(),
        "classification": classification,
        "source_dirs": [str(path) for path in source_dirs],
        "atp": {
            "present": atp_present,
            "ok": atp_green,
            "path": str(atp_path) if atp_path else None,
        },
        "evolake": {
            "present": evolake_present,
            "ok": evolake_green,
            "path": str(evolake_path) if evolake_path else None,
            "local_path": str(evolake_local_path) if evolake_local_path else None,
        },
        "require_green": bool(args.require_green),
    }
    print(json.dumps(result, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
