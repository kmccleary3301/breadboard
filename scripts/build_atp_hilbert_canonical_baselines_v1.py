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


def _get_spec_for_pack(pack_id: str) -> Dict[str, Any]:
    for spec in CANONICAL_BASELINES:
        if spec["pack_id"] == pack_id:
            return spec
    raise KeyError(f"unknown canonical baseline pack: {pack_id}")


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _missing_paths(spec: Dict[str, Any]) -> List[str]:
    candidates = [
        (REPO_ROOT / spec["status_doc"]).resolve(),
        (REPO_ROOT / spec["report"]).resolve(),
        (REPO_ROOT / spec["validation"]).resolve(),
    ]
    return [str(path) for path in candidates if not path.exists()]


def build_preflight_payload() -> Dict[str, Any]:
    entries = []
    ready_count = 0
    for spec in CANONICAL_BASELINES:
        missing = _missing_paths(spec)
        ready = not missing
        if ready:
            ready_count += 1
        entries.append(
            {
                "pack_id": spec["pack_id"],
                "role": spec["role"],
                "ready": ready,
                "missing_paths": missing,
            }
        )
    return {
        "schema": "breadboard.atp_hilbert_canonical_baselines_preflight.v1",
        "generated_from": "scripts/build_atp_hilbert_canonical_baselines_v1.py",
        "entry_count": len(entries),
        "ready_count": ready_count,
        "missing_count": len(entries) - ready_count,
        "entries": entries,
    }


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


def build_bounded_live_publication_payload(
    *,
    pack_manifest_path: Path,
    cross_system_manifest_path: Path,
    bundle_summary_path: Path,
    status_doc_out: Path,
    report_out: Path,
    validation_out: Path,
) -> Dict[str, Any]:
    pack_manifest = _load_json(pack_manifest_path)
    cross_system_manifest = _load_json(cross_system_manifest_path)
    bundle_summary = _load_json(bundle_summary_path)

    pack_id = str(pack_manifest.get("pack_name") or "").strip()
    if not pack_id:
        raise ValueError(f"missing pack_name in pack manifest: {pack_manifest_path}")
    spec = _get_spec_for_pack(pack_id)

    systems = cross_system_manifest.get("systems") or []
    if len(systems) < 2:
        raise ValueError(f"expected at least two systems in cross-system manifest: {cross_system_manifest_path}")
    candidate_system = str((systems[0] or {}).get("system_id") or "").strip()
    baseline_system = str((systems[1] or {}).get("system_id") or "").strip()
    if not candidate_system or not baseline_system:
        raise ValueError(f"missing system ids in cross-system manifest: {cross_system_manifest_path}")

    bundle_rows = bundle_summary.get("generated") or []
    matching_bundle_row = None
    pack_manifest_abs = str(pack_manifest_path.resolve())
    for row in bundle_rows:
        if str((row or {}).get("pack_manifest_path") or "") == pack_manifest_abs:
            matching_bundle_row = row
            break
    if matching_bundle_row is None:
        raise ValueError(
            "bundle summary did not contain a row for the bounded pack manifest: "
            f"{pack_manifest_path}"
        )

    task_ids = tuple(pack_manifest.get("included_task_ids") or ())
    task_count = len(task_ids) or int(
        (((cross_system_manifest.get("benchmark") or {}).get("slice") or {}).get("n_tasks") or 0)
    )
    excluded_tasks = tuple(pack_manifest.get("excluded_tasks") or ())
    bundle_mode = str(matching_bundle_row.get("bundle_mode") or "unknown")
    run_id = str(cross_system_manifest.get("run_id") or "").strip()

    status_doc_out.parent.mkdir(parents=True, exist_ok=True)
    status_doc_out.write_text(
        "\n".join(
            [
                f"# {pack_id}",
                "",
                "- publication_kind: `bounded_live_pack`",
                f"- role: `{spec['role']}`",
                f"- run_id: `{run_id}`",
                f"- candidate_system: `{candidate_system}`",
                f"- baseline_system: `{baseline_system}`",
                f"- task_count: `{task_count}`",
                f"- excluded_task_count: `{len(excluded_tasks)}`",
                f"- bundle_mode: `{bundle_mode}`",
                f"- pack_manifest: `{pack_manifest_path}`",
                f"- cross_system_manifest: `{cross_system_manifest_path}`",
                f"- bundle_summary: `{bundle_summary_path}`",
                f"- note: {spec['note']}",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report_payload = {
        "schema": "breadboard.atp_hilbert_canonical_baseline_live_publication_report.v1",
        "publication_kind": "bounded_live_pack",
        "pack_id": pack_id,
        "role": spec["role"],
        "note": spec["note"],
        "task_count": task_count,
        "excluded_task_count": len(excluded_tasks),
        "candidate_system": candidate_system,
        "baseline_system": baseline_system,
        "bundle_mode": bundle_mode,
        "run_id": run_id,
        "source_artifacts": {
            "pack_manifest": str(pack_manifest_path.resolve()),
            "cross_system_manifest": str(cross_system_manifest_path.resolve()),
            "bundle_summary": str(bundle_summary_path.resolve()),
        },
        "metrics_status": "artifact_only_live_publication",
    }
    validation_payload = {
        "schema": "breadboard.atp_hilbert_canonical_baseline_live_publication_validation.v1",
        "ok": True,
        "publication_kind": "bounded_live_pack",
        "pack_id": pack_id,
        "checks": [
            "pack_manifest_present",
            "cross_system_manifest_present",
            "bundle_summary_present",
            "bundle_summary_row_matches_pack_manifest",
            "canonical_spec_known",
            "system_ids_present",
        ],
    }

    dump_json(report_out, report_payload)
    dump_json(validation_out, validation_payload)

    entry = {
        "pack_id": pack_id,
        "role": spec["role"],
        "note": spec["note"],
        "status_doc": _display_path(status_doc_out),
        "report": _display_path(report_out),
        "validation": _display_path(validation_out),
        "task_count": task_count,
        "candidate_system": candidate_system,
        "baseline_system": baseline_system,
        "candidate_solved": 0,
        "baseline_solved": 0,
        "candidate_only": 0,
        "baseline_only": 0,
        "both_solved": 0,
        "both_unsolved": task_count,
        "candidate_minus_baseline_solve_rate": 0.0,
        "metrics_status": "artifact_only_live_publication",
        "bundle_mode": bundle_mode,
    }
    return {
        "schema": "breadboard.atp_hilbert_canonical_baselines_bounded_live.v1",
        "generated_from": "scripts/build_atp_hilbert_canonical_baselines_v1.py",
        "publication_kind": "bounded_live_pack",
        "candidate_system": candidate_system,
        "baseline_system": baseline_system,
        "entry_count": 1,
        "entries": [entry],
    }


def build_payload(*, allow_missing: bool = False) -> Dict[str, Any]:
    if allow_missing:
        entries = []
        for spec in CANONICAL_BASELINES:
            missing = _missing_paths(spec)
            if missing:
                continue
            entries.append(_build_entry(spec))
    else:
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
    parser.add_argument(
        "--preflight-out",
        default="",
        help="Optional JSON path for a missing-artifact preflight report.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Write a missing-artifact preflight report and exit without requiring all artifact roots.",
    )
    parser.add_argument(
        "--allow-missing-artifacts",
        action="store_true",
        help="Build an index from only the entries whose referenced artifacts are present.",
    )
    parser.add_argument("--bounded-live-pack-manifest", default="")
    parser.add_argument("--bounded-live-cross-system-manifest", default="")
    parser.add_argument("--bounded-live-bundle-summary", default="")
    parser.add_argument("--bounded-live-status-doc-out", default="")
    parser.add_argument("--bounded-live-report-out", default="")
    parser.add_argument("--bounded-live-validation-out", default="")
    args = parser.parse_args()

    if args.bounded_live_pack_manifest:
        if not all(
            [
                args.bounded_live_cross_system_manifest,
                args.bounded_live_bundle_summary,
                args.bounded_live_status_doc_out,
                args.bounded_live_report_out,
                args.bounded_live_validation_out,
            ]
        ):
            raise SystemExit(
                "--bounded-live-pack-manifest requires "
                "--bounded-live-cross-system-manifest, --bounded-live-bundle-summary, "
                "--bounded-live-status-doc-out, --bounded-live-report-out, and "
                "--bounded-live-validation-out"
            )
        payload = build_bounded_live_publication_payload(
            pack_manifest_path=Path(args.bounded_live_pack_manifest).resolve(),
            cross_system_manifest_path=Path(args.bounded_live_cross_system_manifest).resolve(),
            bundle_summary_path=Path(args.bounded_live_bundle_summary).resolve(),
            status_doc_out=Path(args.bounded_live_status_doc_out).resolve(),
            report_out=Path(args.bounded_live_report_out).resolve(),
            validation_out=Path(args.bounded_live_validation_out).resolve(),
        )
        out_json = Path(args.out_json).resolve()
        out_md = Path(args.out_md).resolve()
        dump_json(out_json, payload)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_to_markdown(payload), encoding="utf-8")
        print(
            "[atp-hilbert-canonical-baselines-v1:bounded-live] "
            f"entries={payload['entry_count']} out_json={out_json}"
        )
        return 0

    if args.preflight_only:
        payload = build_preflight_payload()
        out_json = Path(args.preflight_out or args.out_json).resolve()
        dump_json(out_json, payload)
        print(
            f"[atp-hilbert-canonical-baselines-v1:preflight] ready={payload['ready_count']} missing={payload['missing_count']} out_json={out_json}"
        )
        return 0

    payload = build_payload(allow_missing=bool(args.allow_missing_artifacts))
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-hilbert-canonical-baselines-v1] entries={payload['entry_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
