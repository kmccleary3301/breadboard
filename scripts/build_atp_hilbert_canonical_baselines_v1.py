#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"


CANONICAL_BASELINES: List[Dict[str, Any]] = [
    {
        "pack_id": "pack_b_medium_noimo530_minif2f_v1",
        "role": "supporting_filtered_lane",
        "status_doc": "docs/atp_hilbert_pack_b_medium_status_2026-03-10.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/cross_system_pilot_report_v7.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/cross_system_validation_report_v7.json",
        "note": "Filtered Pack B lane after removing invalid or cost-skewed tasks; kept as supporting evidence.",
    },
    {
        "pack_id": "pack_b_core_noimo_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_b_core_noimo_status_2026-03-12.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/cross_system_pilot_report_v3.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/cross_system_validation_report_v3.json",
        "note": "Primary valid Pack B tranche after excluding invalid and spend-skewed tasks.",
    },
    {
        "pack_id": "pack_c_imo1977_p6_stress_minif2f_v1",
        "role": "boundary_stress",
        "status_doc": "docs/atp_hilbert_pack_c_imo1977_stress_status_2026-03-12.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_c_imo1977_p6_stress_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_c_imo1977_p6_stress_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Stress-only boundary tranche; do not mix with calibration/comparator slices.",
    },
    {
        "pack_id": "pack_d_induction_core_minif2f_v1",
        "role": "supporting_split",
        "status_doc": "docs/atp_hilbert_pack_d_split_status_2026-03-12.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_induction_core_minif2f_v1/cross_system_pilot_report_v2.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_induction_core_minif2f_v1/cross_system_validation_report_v2.json",
        "note": "Canonical induction split used to saturate Pack D.",
    },
    {
        "pack_id": "pack_d_numbertheory_core_minif2f_v1",
        "role": "supporting_split",
        "status_doc": "docs/atp_hilbert_pack_d_split_status_2026-03-12.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_numbertheory_core_minif2f_v1/cross_system_pilot_report_v2.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_numbertheory_core_minif2f_v1/cross_system_validation_report_v2.json",
        "note": "Canonical number-theory split used to saturate Pack D.",
    },
    {
        "pack_id": "pack_d_mixed_induction_numbertheory_minif2f_v1",
        "role": "canonical_rollup",
        "status_doc": "docs/atp_hilbert_pack_d_mixed_status_2026-03-12.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/cross_system_pilot_report_v4.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/cross_system_validation_report_v4.json",
        "note": "Mixed Pack D roll-up derived from the canonical split follow-up packs.",
    },
    {
        "pack_id": "pack_e_algebra_focus_minif2f_v1",
        "role": "supporting_focus",
        "status_doc": "docs/atp_hilbert_pack_e_algebra_focus_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_focus_minif2f_v1/cross_system_pilot_report_v3.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_focus_minif2f_v1/cross_system_validation_report_v3.json",
        "note": "Focused algebra follow-up used to close the remaining valid Pack E gaps.",
    },
    {
        "pack_id": "pack_e_algebra_core_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_e_algebra_status_2026-03-12.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_core_minif2f_v1/cross_system_pilot_report_v2.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_core_minif2f_v1/cross_system_validation_report_v2.json",
        "note": "Canonical valid Pack E core slice after excluding the unsound task.",
    },
    {
        "pack_id": "pack_f_discrete_arithmetic_mix_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_f_discrete_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/cross_system_pilot_report_v4.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/cross_system_validation_report_v4.json",
        "note": "Canonical merged Pack F rowset after focused BreadBoard repairs.",
    },
    {
        "pack_id": "pack_g_arithmetic_sanity_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_g_arithmetic_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/cross_system_pilot_report_v2.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/cross_system_validation_report_v2.json",
        "note": "Canonical arithmetic-sanity tranche after focused closure of the two shared misses.",
    },
    {
        "pack_id": "pack_h_modular_closedform_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_h_modular_closedform_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_h_modular_closedform_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_h_modular_closedform_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Canonical modular/closed-form tranche after focused BreadBoard repairs.",
    },
    {
        "pack_id": "pack_i_divisors_modmix_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_i_divisors_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_i_divisors_modmix_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_i_divisors_modmix_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Canonical divisors/mod tranche after one corrected focused Hilbert rerun on task 221.",
    },
    {
        "pack_id": "pack_j_residue_gcd_mix_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_j_residue_gcd_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_j_residue_gcd_mix_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_j_residue_gcd_mix_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Canonical residue/gcd tranche with no invalid extracted statements and no focused repair requirement.",
    },
    {
        "pack_id": "pack_k_moddigit_closedform_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_k_moddigit_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Canonical mod-digit tranche after excluding the unsound closed arithmetic target 728.",
    },
    {
        "pack_id": "pack_l_algebra_linear_equiv_minif2f_v1",
        "role": "canonical_primary",
        "status_doc": "docs/atp_hilbert_pack_l_algebra_linear_equiv_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Canonical linear/equivalence tranche after namespace and field-name normalization.",
    },
    {
        "pack_id": "pack_m_boundary_olympiad_mix_minif2f_v1",
        "role": "boundary_stress",
        "status_doc": "docs/atp_hilbert_pack_m_boundary_status_2026-03-13.md",
        "report": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/cross_system_pilot_report_v1.json",
        "validation": "artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/cross_system_validation_report_v1.json",
        "note": "Boundary-stress olympiad mix used to identify a harder region where both systems currently fail under bounded caps.",
    },
]


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object payload: {path}")
    return payload


def _build_entry(spec: Dict[str, Any]) -> Dict[str, Any]:
    report_path = (REPO_ROOT / spec["report"]).resolve()
    validation_path = (REPO_ROOT / spec["validation"]).resolve()
    status_doc_path = (REPO_ROOT / spec["status_doc"]).resolve()
    for path in (report_path, validation_path, status_doc_path):
        if not path.exists():
            raise FileNotFoundError(path)
    report = _load_json(report_path)
    validation = _load_json(validation_path)
    if not bool(validation.get("ok")):
        raise ValueError(f"validation report not ok: {validation_path}")
    candidate_system = str(report.get("candidate_system") or "").strip()
    baseline_system = str(report.get("baseline_system") or "").strip()
    system_metrics = report.get("system_metrics") or {}
    candidate_metrics = system_metrics.get(candidate_system) or {}
    baseline_metrics = system_metrics.get(baseline_system) or {}
    paired = report.get("paired_outcomes") or {}
    directional = report.get("directional_summary") or {}
    return {
        "pack_id": spec["pack_id"],
        "role": spec["role"],
        "note": spec["note"],
        "status_doc": str(status_doc_path.relative_to(REPO_ROOT)),
        "report": str(report_path.relative_to(REPO_ROOT)),
        "validation": str(validation_path.relative_to(REPO_ROOT)),
        "task_count": int(report.get("task_count") or 0),
        "candidate_system": candidate_system,
        "baseline_system": baseline_system,
        "candidate_solved": int(candidate_metrics.get("solved_count") or 0),
        "baseline_solved": int(baseline_metrics.get("solved_count") or 0),
        "candidate_only": int(paired.get("n10_candidate_only") or 0),
        "baseline_only": int(paired.get("n01_baseline_only") or 0),
        "both_solved": int(paired.get("n11_both_solved") or 0),
        "both_unsolved": int(paired.get("n00_both_unsolved") or 0),
        "candidate_minus_baseline_solve_rate": float(directional.get("candidate_minus_baseline_solve_rate") or 0.0),
    }


def build_payload() -> Dict[str, Any]:
    entries = [_build_entry(spec) for spec in CANONICAL_BASELINES]
    role_counts: Dict[str, int] = {}
    for entry in entries:
        role = str(entry["role"])
        role_counts[role] = role_counts.get(role, 0) + 1
    return {
        "schema": "breadboard.atp_hilbert_canonical_baselines.v1",
        "generated_from": "scripts/build_atp_hilbert_canonical_baselines_v1.py",
        "candidate_system": "bb_hilbert_like",
        "baseline_system": "hilbert_roselab",
        "entry_count": len(entries),
        "role_counts": role_counts,
        "entries": entries,
    }


def _to_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# ATP Hilbert Canonical Baselines v1",
        "",
        "- candidate_system: `bb_hilbert_like`",
        "- baseline_system: `hilbert_roselab`",
        f"- entry_count: `{payload.get('entry_count', 0)}`",
        "",
        "| pack | role | tasks | BB solved | Hilbert solved | BB-only | Hilbert-only | report |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for entry in payload.get("entries") or []:
        lines.append(
            "| {pack_id} | {role} | {task_count} | {candidate_solved} | {baseline_solved} | {candidate_only} | {baseline_only} | `{report}` |".format(
                **entry
            )
        )
    lines.extend(["", "## Notes", ""])
    for entry in payload.get("entries") or []:
        lines.extend(
            [
                f"### `{entry['pack_id']}`",
                f"- role: `{entry['role']}`",
                f"- status_doc: `{entry['status_doc']}`",
                f"- validation: `{entry['validation']}`",
                f"- note: {entry['note']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-json",
        default=str(DEFAULT_OUT_ROOT / "canonical_baseline_index_v1.json"),
    )
    parser.add_argument(
        "--out-md",
        default=str(DEFAULT_OUT_ROOT / "canonical_baseline_index_v1.md"),
    )
    args = parser.parse_args()

    payload = build_payload()
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-hilbert-canonical-baselines-v1] entries={payload['entry_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
