#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "canonical_baseline_index_v1.json"
DEFAULT_BB_SPEND = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "bb_spend_backfill_v1.json"
DEFAULT_ARM_AUDIT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2" / "arm_audit_v1.json"
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"
PRIMARY_ROLES = {"canonical_primary", "canonical_rollup", "boundary_stress"}


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload: {path}")
    return payload


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _extract_hilbert_spend(status_doc: Path) -> float | None:
    text = status_doc.read_text(encoding="utf-8")
    matches = re.findall(r"~\$(\d+(?:\.\d+)?)", text)
    if not matches:
        exact = re.findall(r"Estimated cost at OpenRouter .*?: `?\$?(\d+(?:\.\d+)?)`?", text)
        matches.extend(exact)
    if not matches:
        return None
    return float(matches[-1])


def _augment_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    status_doc = REPO_ROOT / str(entry["status_doc"])
    augmented = dict(entry)
    augmented["pack_family"] = str(entry["pack_id"]).split("_")[1]
    augmented["is_primary"] = str(entry["role"]) in PRIMARY_ROLES
    augmented["hilbert_exact_spend_usd"] = _extract_hilbert_spend(status_doc)
    return augmented


def _totals(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    headline = [entry for entry in entries if entry["is_primary"]]
    spend_values = [float(entry["hilbert_exact_spend_usd"]) for entry in headline if entry["hilbert_exact_spend_usd"] is not None]
    bb_spend_values = [float(entry["candidate_estimated_spend_usd"]) for entry in headline if entry.get("candidate_estimated_spend_usd") is not None]
    return {
        "pack_count": len(headline),
        "task_count": sum(int(entry["task_count"]) for entry in headline),
        "candidate_solved_total": sum(int(entry["candidate_solved"]) for entry in headline),
        "baseline_solved_total": sum(int(entry["baseline_solved"]) for entry in headline),
        "candidate_only_total": sum(int(entry["candidate_only"]) for entry in headline),
        "baseline_only_total": sum(int(entry["baseline_only"]) for entry in headline),
        "both_solved_total": sum(int(entry["both_solved"]) for entry in headline),
        "both_unsolved_total": sum(int(entry["both_unsolved"]) for entry in headline),
        "candidate_ahead_pack_count": sum(1 for entry in headline if int(entry["candidate_solved"]) > int(entry["baseline_solved"])),
        "tied_pack_count": sum(1 for entry in headline if int(entry["candidate_solved"]) == int(entry["baseline_solved"])),
        "baseline_ahead_pack_count": sum(1 for entry in headline if int(entry["candidate_solved"]) < int(entry["baseline_solved"])),
        "hilbert_exact_spend_usd_total": round(sum(spend_values), 6) if spend_values else None,
        "breadboard_estimated_spend_usd_total": round(sum(bb_spend_values), 6) if bb_spend_values else None,
    }


def _load_bb_spend_index(path: Path | None) -> Dict[str, Dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = _load_json(path)
    out: Dict[str, Dict[str, Any]] = {}
    for entry in payload.get("entries") or []:
        if isinstance(entry, dict) and entry.get("pack_id"):
            out[str(entry["pack_id"])] = entry
    return out


def _load_arm_audit_index(path: Path | None) -> Dict[str, Dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = _load_json(path)
    out: Dict[str, Dict[str, Any]] = {}
    for entry in payload.get("entries") or []:
        if isinstance(entry, dict) and entry.get("pack_id"):
            out[str(entry["pack_id"])] = entry
    return out


def build_payload(index_path: Path, bb_spend_path: Path | None = None, arm_audit_path: Path | None = None) -> Dict[str, Any]:
    canonical_index = _load_json(index_path)
    bb_spend_index = _load_bb_spend_index(bb_spend_path)
    arm_audit_index = _load_arm_audit_index(arm_audit_path)
    entries = []
    for entry in (canonical_index.get("entries") or []):
        augmented = _augment_entry(entry)
        bb_spend = bb_spend_index.get(str(augmented["pack_id"])) or {}
        arm_audit = arm_audit_index.get(str(augmented["pack_id"])) or {}
        augmented["candidate_estimated_spend_usd"] = bb_spend.get("estimated_total_cost_usd")
        augmented["candidate_arm_mode"] = arm_audit.get("candidate_arm_mode")
        augmented["focused_task_count"] = arm_audit.get("focused_task_count")
        entries.append(augmented)
    role_breakdown: Dict[str, int] = {}
    family_breakdown: Dict[str, Dict[str, int]] = {}
    arm_mode_breakdown: Dict[str, int] = {}
    for entry in entries:
        role = str(entry["role"])
        family = str(entry["pack_family"])
        role_breakdown[role] = role_breakdown.get(role, 0) + 1
        bucket = family_breakdown.setdefault(family, {"entry_count": 0, "candidate_solved_total": 0, "baseline_solved_total": 0})
        bucket["entry_count"] += 1
        bucket["candidate_solved_total"] += int(entry["candidate_solved"])
        bucket["baseline_solved_total"] += int(entry["baseline_solved"])
        mode = str(entry.get("candidate_arm_mode") or "unknown")
        arm_mode_breakdown[mode] = arm_mode_breakdown.get(mode, 0) + 1
    return {
        "schema": "breadboard.atp_hilbert_scoreboard.v1",
        "index_path": _display_path(index_path),
        "bb_spend_backfill_path": _display_path(bb_spend_path) if bb_spend_path and bb_spend_path.exists() else None,
        "arm_audit_path": _display_path(arm_audit_path) if arm_audit_path and arm_audit_path.exists() else None,
        "headline_roles": sorted(PRIMARY_ROLES),
        "candidate_system": str(canonical_index.get("candidate_system") or ""),
        "baseline_system": str(canonical_index.get("baseline_system") or ""),
        "headline_totals": _totals(entries),
        "role_breakdown": role_breakdown,
        "family_breakdown": family_breakdown,
        "arm_mode_breakdown": arm_mode_breakdown,
        "entries": entries,
    }


def _to_markdown(payload: Dict[str, Any]) -> str:
    totals = payload["headline_totals"]
    lines = [
        "# ATP Hilbert Scoreboard v1",
        "",
        f"- candidate_system: `{payload['candidate_system']}`",
        f"- baseline_system: `{payload['baseline_system']}`",
        f"- headline primary packs: `{totals['pack_count']}`",
        f"- headline tasks: `{totals['task_count']}`",
        f"- BreadBoard solved total: `{totals['candidate_solved_total']}`",
        f"- Hilbert solved total: `{totals['baseline_solved_total']}`",
        f"- BreadBoard-only total: `{totals['candidate_only_total']}`",
        f"- Hilbert-only total: `{totals['baseline_only_total']}`",
        f"- primary packs with BreadBoard ahead: `{totals['candidate_ahead_pack_count']}`",
        f"- primary tied packs: `{totals['tied_pack_count']}`",
        f"- primary packs with Hilbert ahead: `{totals['baseline_ahead_pack_count']}`",
        f"- Hilbert exact spend total (headline packs): `{totals['hilbert_exact_spend_usd_total']}`",
        f"- BreadBoard estimated spend total (headline packs): `{totals['breadboard_estimated_spend_usd_total']}`",
        f"- arm modes: `{payload.get('arm_mode_breakdown', {})}`",
        "",
        "## Headline Packs",
        "",
        "| pack | role | arm mode | focused tasks | tasks | BB solved | Hilbert solved | BB-only | Hilbert-only | BB spend | Hilbert spend |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for entry in payload["entries"]:
        if not entry["is_primary"]:
            continue
        spend = entry["hilbert_exact_spend_usd"]
        spend_text = f"{spend:.6f}" if spend is not None else "n/a"
        bb_spend = entry.get("candidate_estimated_spend_usd")
        bb_spend_text = f"{float(bb_spend):.6f}" if bb_spend is not None else "n/a"
        lines.append(
            f"| {entry['pack_id']} | {entry['role']} | {entry.get('candidate_arm_mode') or 'n/a'} | {entry.get('focused_task_count') or 0} | {entry['task_count']} | {entry['candidate_solved']} | {entry['baseline_solved']} | {entry['candidate_only']} | {entry['baseline_only']} | {bb_spend_text} | {spend_text} |"
        )
    lines.extend(["", "## Supporting Packs", "", "| pack | role | tasks | BB solved | Hilbert solved | report |", "| --- | --- | ---: | ---: | ---: | --- |"])
    for entry in payload["entries"]:
        if entry["is_primary"]:
            continue
        lines.append(
            f"| {entry['pack_id']} | {entry['role']} | {entry['task_count']} | {entry['candidate_solved']} | {entry['baseline_solved']} | `{entry['report']}` |"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-json", default=str(DEFAULT_INDEX))
    parser.add_argument("--bb-spend-json", default=str(DEFAULT_BB_SPEND))
    parser.add_argument("--arm-audit-json", default=str(DEFAULT_ARM_AUDIT))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_ROOT / "scoreboard_v1.json"))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_ROOT / "scoreboard_v1.md"))
    args = parser.parse_args()

    payload = build_payload(
        Path(args.index_json).resolve(),
        Path(args.bb_spend_json).resolve() if str(args.bb_spend_json).strip() else None,
        Path(args.arm_audit_json).resolve() if str(args.arm_audit_json).strip() else None,
    )
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-hilbert-scoreboard-v1] headline_packs={payload['headline_totals']['pack_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
