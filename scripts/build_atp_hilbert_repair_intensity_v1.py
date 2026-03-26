#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARM_AUDIT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "arm_audit_v1.json"
DEFAULT_INDEX = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "canonical_baseline_index_v1.json"
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"
PRIMARY_ROLES = {"canonical_primary", "canonical_rollup", "boundary_stress"}


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload: {path}")
    return payload


def build_payload(index_path: Path, arm_audit_path: Path) -> Dict[str, Any]:
    canonical_index = _load_json(index_path)
    arm_audit = _load_json(arm_audit_path)
    canonical_entries = {str(entry["pack_id"]): entry for entry in (canonical_index.get("entries") or [])}
    entries: List[Dict[str, Any]] = []
    for arm_entry in arm_audit.get("entries") or []:
        if not isinstance(arm_entry, dict) or not arm_entry.get("pack_id"):
            continue
        pack_id = str(arm_entry["pack_id"])
        canonical = canonical_entries.get(pack_id) or {}
        task_count = int(canonical.get("task_count") or 0)
        focused_task_count = int(arm_entry.get("focused_task_count") or 0)
        entries.append(
            {
                "pack_id": pack_id,
                "role": canonical.get("role"),
                "candidate_arm_mode": arm_entry.get("candidate_arm_mode"),
                "focused_task_count": focused_task_count,
                "task_count": task_count,
                "focused_task_ratio": round((focused_task_count / task_count), 6) if task_count else 0.0,
                "is_primary": str(canonical.get("role") or "") in PRIMARY_ROLES,
            }
        )
    primary_entries = [entry for entry in entries if entry["is_primary"]]
    focused_primary = [entry for entry in primary_entries if entry["candidate_arm_mode"] == "focused_repaired"]
    return {
        "schema": "breadboard.atp_hilbert_repair_intensity.v1",
        "entry_count": len(entries),
        "primary_pack_count": len(primary_entries),
        "primary_focused_pack_count": len(focused_primary),
        "primary_baseline_only_pack_count": sum(1 for entry in primary_entries if entry["candidate_arm_mode"] == "baseline_only"),
        "primary_focused_task_total": sum(int(entry["focused_task_count"]) for entry in primary_entries),
        "primary_task_total": sum(int(entry["task_count"]) for entry in primary_entries),
        "primary_focused_task_ratio": round(
            (
                sum(int(entry["focused_task_count"]) for entry in primary_entries)
                / max(1, sum(int(entry["task_count"]) for entry in primary_entries))
            ),
            6,
        ),
        "entries": entries,
    }


def _to_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# ATP Hilbert Repair Intensity v1",
        "",
        f"- primary_pack_count: `{payload['primary_pack_count']}`",
        f"- primary_focused_pack_count: `{payload['primary_focused_pack_count']}`",
        f"- primary_baseline_only_pack_count: `{payload['primary_baseline_only_pack_count']}`",
        f"- primary_focused_task_total: `{payload['primary_focused_task_total']}`",
        f"- primary_task_total: `{payload['primary_task_total']}`",
        f"- primary_focused_task_ratio: `{payload['primary_focused_task_ratio']}`",
        "",
        "| pack | role | arm mode | focused tasks | task count | focused ratio |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for entry in payload["entries"]:
        lines.append(
            f"| {entry['pack_id']} | {entry.get('role') or 'n/a'} | {entry['candidate_arm_mode']} | {entry['focused_task_count']} | {entry['task_count']} | {entry['focused_task_ratio']:.3f} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-json", default=str(DEFAULT_INDEX))
    parser.add_argument("--arm-audit-json", default=str(DEFAULT_ARM_AUDIT))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_ROOT / "repair_intensity_v1.json"))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_ROOT / "repair_intensity_v1.md"))
    args = parser.parse_args()
    payload = build_payload(Path(args.index_json).resolve(), Path(args.arm_audit_json).resolve())
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-hilbert-repair-intensity-v1] primary_focused_task_ratio={payload['primary_focused_task_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
