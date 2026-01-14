#!/usr/bin/env python3
"""
Validate parity fixture paths referenced by the parity manifest.

Usage:
  python scripts/fixtures_doctor.py
  python scripts/fixtures_doctor.py --manifest misc/opencode_runs/parity_scenarios.yaml
  python scripts/fixtures_doctor.py --fixture-root /path/to/fixtures --strict
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agentic_coder_prototype.parity_manifest import DEFAULT_MANIFEST, load_parity_scenarios


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate parity fixture paths referenced by the manifest.")
    parser.add_argument("--manifest", help="Path to parity_scenarios.yaml")
    parser.add_argument("--fixture-root", help="Root directory for external fixtures")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any fixture is missing")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include disabled scenarios when scanning fixtures (default: skip disabled).",
    )
    parser.add_argument(
        "--strict-live",
        action="store_true",
        help="Treat live task fixtures as strict even if PARITY_RUN_LIVE is not enabled.",
    )
    parser.add_argument(
        "--max-gate",
        type=int,
        default=None,
        help="Only treat missing fixtures as errors for gate tiers <= this value (default: env PARITY_STRICT_GATE or 1).",
    )
    return parser.parse_args()


def _record_missing(
    missing: List[Dict[str, str]],
    scenario: str,
    label: str,
    path: Path,
) -> None:
    missing.append({"scenario": scenario, "path": str(path), "label": label})


def _tier_rank(value: str) -> int:
    if not value:
        return 99
    normalized = value.strip().upper()
    if normalized.startswith("E") and normalized[1:].isdigit():
        return int(normalized[1:])
    return 99


def main() -> int:
    args = _parse_args()
    if args.fixture_root:
        os.environ["BREADBOARD_FIXTURE_ROOT"] = args.fixture_root

    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    if manifest_path is None:
        env_manifest = os.environ.get("BREADBOARD_PARITY_MANIFEST")
        manifest_path = Path(env_manifest).resolve() if env_manifest else DEFAULT_MANIFEST

    if not manifest_path.exists():
        print(f"[fixtures] manifest not found: {manifest_path}")
        return 2

    scenarios = load_parity_scenarios(manifest_path)
    missing: List[Dict[str, str]] = []
    strict_gate = args.max_gate
    if strict_gate is None:
        strict_gate = int(os.environ.get("PARITY_STRICT_GATE", "1") or "1")
    strict_missing: List[Dict[str, str]] = []

    live_enabled = os.environ.get("PARITY_RUN_LIVE", "").lower() in {"1", "true", "yes"}
    for scenario in scenarios:
        if not scenario.enabled and not args.include_disabled:
            continue
        items: List[Tuple[str, Path | None]] = [
            ("config", scenario.config),
            ("session", scenario.session),
            ("task", scenario.task),
            ("golden_workspace", scenario.golden_workspace),
            ("workspace_seed", scenario.workspace_seed),
            ("golden_meta", scenario.golden_meta),
            ("script", scenario.script),
            ("guard_fixture", scenario.guard_fixture),
            ("todo_expected", scenario.todo_expected),
            ("guardrails_expected", scenario.guardrails_expected),
        ]
        gate_rank = _tier_rank(scenario.gate_tier)
        for label, path in items:
            if path is None:
                continue
            if not path.exists():
                _record_missing(missing, scenario.name, label, path)
                if args.strict and gate_rank <= strict_gate:
                    if scenario.mode == "task" and not live_enabled and not args.strict_live:
                        continue
                    _record_missing(strict_missing, scenario.name, label, path)

    print(f"[fixtures] manifest: {manifest_path}")
    fixture_root = os.environ.get("BREADBOARD_FIXTURE_ROOT") or os.environ.get("BREADBOARD_PARITY_FIXTURE_ROOT")
    if fixture_root:
        print(f"[fixtures] fixture_root: {fixture_root}")

    if not missing:
        print("[fixtures] ok: all referenced paths exist.")
        return 0

    print(f"[fixtures] missing: {len(missing)}")
    for entry in missing:
        print(f"- {entry['scenario']}: {entry['label']} -> {entry['path']}")

    if args.strict:
        if strict_missing:
            print(f"[fixtures] strict missing (gate<=E{strict_gate}): {len(strict_missing)}")
            return 1
        print(f"[fixtures] strict mode: no missing gate-tier fixtures (E<= {strict_gate})")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
