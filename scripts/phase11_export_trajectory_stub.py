#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.optimize.trajectory_ir import build_stub_episode


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a stub TrajectoryIR from a run directory")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    run_summary = _load_json(run_dir / "meta" / "run_summary.json")
    reward_metrics = _load_json(run_dir / "meta" / "reward_metrics.json")
    reward_v1 = _load_json(run_dir / "meta" / "reward_v1.json") or run_summary.get("reward_v1")

    episode = build_stub_episode(
        run_id=run_dir.name,
        reward_metrics=reward_metrics,
        run_summary=run_summary,
        reward_v1=reward_v1,
    )
    payload = {
        "run_id": episode.run_id,
        "steps": [step.__dict__ for step in episode.steps],
        "summary": episode.summary,
        "reward_v1": episode.reward_v1,
        "notes": episode.notes,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
