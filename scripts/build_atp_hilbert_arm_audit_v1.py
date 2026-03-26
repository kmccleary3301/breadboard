#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"

ARM_AUDIT: List[Dict[str, Any]] = [
    {
        "pack_id": "pack_b_medium_noimo530_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_algebra_156",
            "numbertheory_2dvd4expn",
            "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
        ],
        "note": "Filtered support lane closed by theorem-local guidance and focused repair seeding.",
    },
    {
        "pack_id": "pack_b_core_noimo_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_algebra_156",
            "mathd_numbertheory_530",
            "numbertheory_2dvd4expn",
            "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
        ],
        "note": "Primary Pack B lane reaches canonical form only after focused theorem-local repair and seed reuse.",
    },
    {
        "pack_id": "pack_c_imo1977_p6_stress_minif2f_v1",
        "candidate_arm_mode": "boundary_stress",
        "focused_task_ids": [],
        "note": "Stress-only pack; no focused repair used in canonical result.",
    },
    {
        "pack_id": "pack_d_induction_core_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "induction_sumkexp3eqsumksq",
            "induction_12dvd4expnp1p20",
        ],
        "note": "Induction split was initially 0/2 and then closed with theorem-local scaffolds.",
    },
    {
        "pack_id": "pack_d_numbertheory_core_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "numbertheory_2pownm1prime_nprime",
            "mathd_numbertheory_427",
        ],
        "note": "Number-theory split required focused repair on the two remaining shared-unsolved tasks.",
    },
    {
        "pack_id": "pack_d_mixed_induction_numbertheory_minif2f_v1",
        "candidate_arm_mode": "split_rollup",
        "focused_task_ids": [
            "induction_sumkexp3eqsumksq",
            "induction_12dvd4expnp1p20",
            "numbertheory_2pownm1prime_nprime",
            "mathd_numbertheory_427",
        ],
        "note": "Canonical mixed Pack D result is a roll-up of the focused split follow-up packs.",
    },
    {
        "pack_id": "pack_e_algebra_focus_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_algebra_73",
            "mathd_algebra_131",
        ],
        "note": "Focused algebra follow-up exists specifically to close the residual valid Pack E gaps.",
    },
    {
        "pack_id": "pack_e_algebra_core_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_algebra_73",
            "mathd_algebra_131",
        ],
        "note": "Canonical Pack E core inherits the focused follow-up wins after invalid-task filtering.",
    },
    {
        "pack_id": "pack_f_discrete_arithmetic_mix_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "amc12_2001_p9",
            "mathd_numbertheory_33",
            "amc12a_2015_p10",
            "amc12a_2008_p4",
            "aime_1991_p1",
        ],
        "note": "Canonical Pack F rowset is a merged repair result, not the base full-pack pass.",
    },
    {
        "pack_id": "pack_g_arithmetic_sanity_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_numbertheory_353",
            "mathd_numbertheory_430",
        ],
        "note": "Pack G closed its two shared misses through focused BreadBoard reruns.",
    },
    {
        "pack_id": "pack_h_modular_closedform_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_numbertheory_5",
            "mathd_numbertheory_24",
            "mathd_numbertheory_99",
            "mathd_numbertheory_109",
        ],
        "note": "Pack H canonical form comes from a focused subset that closed all four initial misses.",
    },
    {
        "pack_id": "pack_i_divisors_modmix_minif2f_v1",
        "candidate_arm_mode": "focused_repaired",
        "focused_task_ids": [
            "mathd_numbertheory_221",
        ],
        "note": "Pack I needed one focused correction on task 221 after statement canonicalization drift.",
    },
    {
        "pack_id": "pack_j_residue_gcd_mix_minif2f_v1",
        "candidate_arm_mode": "baseline_only",
        "focused_task_ids": [],
        "note": "Pack J saturated without focused repair.",
    },
    {
        "pack_id": "pack_k_moddigit_closedform_minif2f_v1",
        "candidate_arm_mode": "baseline_only",
        "focused_task_ids": [],
        "note": "Pack K is a cheap baseline-only tranche; BreadBoard leads without focused repair.",
    },
    {
        "pack_id": "pack_l_algebra_linear_equiv_minif2f_v1",
        "candidate_arm_mode": "baseline_only",
        "focused_task_ids": [],
        "note": "Pack L is treated as baseline-only after statement canonicalization; no focused subset or seed reuse was used.",
    },
    {
        "pack_id": "pack_m_boundary_olympiad_mix_minif2f_v1",
        "candidate_arm_mode": "boundary_stress",
        "focused_task_ids": [],
        "note": "Pack M is a harder boundary-stress tranche; both systems fail it cleanly under the current bounded caps.",
    },
]


def build_payload() -> Dict[str, Any]:
    entries = []
    for row in ARM_AUDIT:
        entries.append(
            {
                **row,
                "focused_task_count": len(row["focused_task_ids"]),
            }
        )
    mode_counts: Dict[str, int] = {}
    for entry in entries:
        mode = str(entry["candidate_arm_mode"])
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    return {
        "schema": "breadboard.atp_hilbert_arm_audit.v1",
        "entry_count": len(entries),
        "mode_counts": mode_counts,
        "entries": entries,
    }


def _to_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# ATP Hilbert Arm Audit v1",
        "",
        f"- entry_count: `{payload['entry_count']}`",
        "",
        "| pack | mode | focused tasks |",
        "| --- | --- | ---: |",
    ]
    for entry in payload["entries"]:
        lines.append(
            f"| {entry['pack_id']} | {entry['candidate_arm_mode']} | {entry['focused_task_count']} |"
        )
    lines.extend(["", "## Notes", ""])
    for entry in payload["entries"]:
        lines.append(f"- `{entry['pack_id']}` — {entry['note']}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_ROOT / "arm_audit_v1.json"))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_ROOT / "arm_audit_v1.md"))
    args = parser.parse_args()
    payload = build_payload()
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-hilbert-arm-audit-v1] entries={payload['entry_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
