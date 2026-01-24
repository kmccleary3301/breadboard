#!/usr/bin/env python3
"""Offline C-Trees policy experiment harness."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentic_coder_prototype.ctrees.context_engine import select_ctree_context
from agentic_coder_prototype.ctrees.store import CTreeStore


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict) and parsed.get("_type") == "ctree_eventlog_header":
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def run_experiments(registry_path: Path, *, out_path: Path) -> Dict[str, Any]:
    registry = _load_json(registry_path)
    scenarios = registry.get("scenarios") if isinstance(registry.get("scenarios"), list) else []
    policies = registry.get("policies") if isinstance(registry.get("policies"), list) else []

    results: List[Dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_id = scenario.get("id")
        scenario_path = scenario.get("path")
        if not isinstance(scenario_id, str) or not isinstance(scenario_path, str):
            continue
        events = _load_events((_REPO_ROOT / scenario_path).resolve())
        store = CTreeStore.from_events(events)
        base_hash = store.hashes().get("node_hash")

        for policy in policies:
            if not isinstance(policy, dict):
                continue
            policy_id = policy.get("id")
            if not isinstance(policy_id, str):
                continue
            selection_config = policy.get("selection_config") if isinstance(policy.get("selection_config"), dict) else {}
            header_config = policy.get("header_config") if isinstance(policy.get("header_config"), dict) else {}
            collapse_target = policy.get("collapse_target")
            collapse_mode = policy.get("collapse_mode") or "all_but_last"
            stage = policy.get("stage") or "FROZEN"

            selection_a, _compiled_a = select_ctree_context(
                store,
                selection_config=dict(selection_config),
                header_config=dict(header_config),
                collapse_target=collapse_target,
                collapse_mode=str(collapse_mode),
                stage=str(stage),
                pin_latest=True,
            )
            selection_b, _compiled_b = select_ctree_context(
                store,
                selection_config=dict(selection_config),
                header_config=dict(header_config),
                collapse_target=collapse_target,
                collapse_mode=str(collapse_mode),
                stage=str(stage),
                pin_latest=True,
            )

            stable = selection_a.get("selection_sha256") == selection_b.get("selection_sha256")
            results.append(
                {
                    "scenario_id": scenario_id,
                    "policy_id": policy_id,
                    "node_hash": base_hash,
                    "selection_sha256": selection_a.get("selection_sha256"),
                    "candidate_count": selection_a.get("candidate_count"),
                    "kept_count": selection_a.get("kept_count"),
                    "dropped_count": selection_a.get("dropped_count"),
                    "collapsed_count": selection_a.get("collapsed_count"),
                    "stable": stable,
                }
            )

    payload = {
        "generated_at": _now_iso(),
        "registry": str(registry_path),
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline C-Trees policy experiment harness.")
    parser.add_argument(
        "--registry",
        default="experiments/ctrees/registry.json",
        help="Path to experiment registry JSON (default: experiments/ctrees/registry.json).",
    )
    parser.add_argument(
        "--out",
        default="experiments/ctrees/results.json",
        help="Output path for results JSON (default: experiments/ctrees/results.json).",
    )
    args = parser.parse_args()
    run_experiments(Path(args.registry), out_path=Path(args.out))
    print(f"[ctlab] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
