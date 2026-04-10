from __future__ import annotations

import json
from pathlib import Path

from scripts.run_darwin_stage6_broader_compounding_v0 import ROOT, run_stage6_broader_compounding


def test_run_stage6_broader_compounding_emits_positive_rows(tmp_path: Path) -> None:
    repo_tmp = ROOT / "artifacts" / "tmp_stage6_pytest" / tmp_path.name
    tranche2 = repo_tmp / "tranche2"
    tranche2.mkdir(parents=True, exist_ok=True)
    (tranche2 / "transfer_outcome_summary_v1.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "transfer_case_id": "transfer.systems.scheduling",
                        "source_lane_id": "lane.systems",
                        "target_lane_id": "lane.scheduling",
                        "family_id": "family.systems.policy",
                        "family_kind": "policy",
                        "transfer_status": "retained",
                        "replay_status": "supported",
                    },
                    {
                        "transfer_case_id": "transfer.repo.systems",
                        "source_lane_id": "lane.repo_swe",
                        "target_lane_id": "lane.systems",
                        "family_id": "family.repo.topology",
                        "family_kind": "topology",
                        "transfer_status": "activation_probe",
                        "replay_status": "observed",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tranche2 / "broader_transfer_matrix_v0.json").write_text(
        json.dumps(
            {
                "provider_segmentation_status": "claim_rows_segmented",
                "claim_rows_have_canonical_provider_segmentation": True,
            }
        ),
        encoding="utf-8",
    )
    out_dir = repo_tmp / "out"
    summary = run_stage6_broader_compounding(tranche2_dir=tranche2, out_dir=out_dir, rounds=1)
    payload = json.loads((out_dir / "broader_compounding_v0.json").read_text(encoding="utf-8"))
    assert summary["positive_count"] == 1
    assert payload["family_center_decision"] == "hold_single_retained_family_center"
    assert payload["rows"][0]["broader_compounding_status"] == "positive_broader_compounding"
