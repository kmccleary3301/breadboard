#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from _cross_system_eval_v1 import dump_json
from build_atp_hilbert_arm_audit_v1 import build_payload as build_arm_audit_payload
from backfill_atp_hilbert_bb_spend_v1 import build_payload as build_bb_spend_payload
from build_atp_hilbert_canonical_baselines_v1 import build_payload as build_baseline_payload
from build_atp_hilbert_canonical_baselines_v1 import _to_markdown as baselines_to_markdown
from build_atp_hilbert_scoreboard_v1 import _to_markdown as scoreboard_to_markdown
from build_atp_hilbert_scoreboard_v1 import build_payload as build_scoreboard_payload


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_rollup(out_root: Path) -> Dict[str, Any]:
    out_root = out_root.resolve()
    baseline_json = out_root / "canonical_baseline_index_v1.json"
    baseline_md = out_root / "canonical_baseline_index_v1.md"
    arm_audit_json = out_root / "arm_audit_v1.json"
    arm_audit_md = out_root / "arm_audit_v1.md"
    bb_spend_json = out_root / "bb_spend_backfill_v1.json"
    scoreboard_json = out_root / "scoreboard_v1.json"
    scoreboard_md = out_root / "scoreboard_v1.md"
    rollup_json = out_root / "rollup_manifest_v1.json"

    baseline_payload = build_baseline_payload()
    dump_json(baseline_json, baseline_payload)
    baseline_md.write_text(baselines_to_markdown(baseline_payload), encoding="utf-8")

    arm_audit_payload = build_arm_audit_payload()
    dump_json(arm_audit_json, arm_audit_payload)
    from build_atp_hilbert_arm_audit_v1 import _to_markdown as arm_audit_to_markdown
    arm_audit_md.write_text(arm_audit_to_markdown(arm_audit_payload), encoding="utf-8")

    bb_spend_payload = build_bb_spend_payload(baseline_json)
    dump_json(bb_spend_json, bb_spend_payload)

    scoreboard_payload = build_scoreboard_payload(baseline_json, bb_spend_json, arm_audit_json)
    dump_json(scoreboard_json, scoreboard_payload)
    scoreboard_md.write_text(scoreboard_to_markdown(scoreboard_payload), encoding="utf-8")

    payload = {
        "schema": "breadboard.atp_hilbert_rollup_manifest.v1",
        "out_root": _display_path(out_root),
        "canonical_baselines": _display_path(baseline_json),
        "canonical_baselines_md": _display_path(baseline_md),
        "arm_audit": _display_path(arm_audit_json),
        "arm_audit_md": _display_path(arm_audit_md),
        "bb_spend_backfill": _display_path(bb_spend_json),
        "scoreboard": _display_path(scoreboard_json),
        "scoreboard_md": _display_path(scoreboard_md),
        "headline_pack_count": int(scoreboard_payload["headline_totals"]["pack_count"]),
        "headline_task_count": int(scoreboard_payload["headline_totals"]["task_count"]),
    }
    dump_json(rollup_json, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    args = parser.parse_args()
    payload = build_rollup(Path(args.out_root))
    print(
        "[atp-hilbert-rollup-v1] headline_packs="
        f"{payload['headline_pack_count']} headline_tasks={payload['headline_task_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
