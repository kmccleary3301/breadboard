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
        if not isinstance(arm_entry, dict):
            continue
        if str(arm_entry.get("candidate_arm_mode") or "") != "baseline_only":
            continue
        pack_id = str(arm_entry["pack_id"])
        canonical = canonical_entries.get(pack_id) or {}
        entries.append(
            {
                "pack_id": pack_id,
                "role": canonical.get("role"),
                "task_count": canonical.get("task_count"),
                "candidate_solved": canonical.get("candidate_solved"),
                "baseline_solved": canonical.get("baseline_solved"),
                "report": canonical.get("report"),
                "validation": canonical.get("validation"),
            }
        )
    return {
        "schema": "breadboard.atp_hilbert_no_repair_slice.v1",
        "entry_count": len(entries),
        "entries": entries,
    }


def _to_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# ATP Hilbert No-Repair Slice v1",
        "",
        f"- entry_count: `{payload['entry_count']}`",
        "",
        "| pack | role | tasks | BB solved | Hilbert solved | report |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for entry in payload["entries"]:
        lines.append(
            f"| {entry['pack_id']} | {entry.get('role') or 'n/a'} | {entry.get('task_count') or 0} | {entry.get('candidate_solved') or 0} | {entry.get('baseline_solved') or 0} | `{entry.get('report')}` |"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-json", default=str(DEFAULT_INDEX))
    parser.add_argument("--arm-audit-json", default=str(DEFAULT_ARM_AUDIT))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_ROOT / "no_repair_slice_v1.json"))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_ROOT / "no_repair_slice_v1.md"))
    args = parser.parse_args()
    payload = build_payload(Path(args.index_json).resolve(), Path(args.arm_audit_json).resolve())
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-hilbert-no-repair-slice-v1] entries={payload['entry_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
