from __future__ import annotations

import json
from pathlib import Path

from scripts.build_darwin_stage6_canonical_artifact_index_v0 import build_stage6_canonical_artifact_index
from scripts.build_darwin_stage6_comparative_bundle_v0 import build_stage6_comparative_bundle
from scripts.build_darwin_stage6_composition_canary_v0 import build_stage6_composition_canary
from scripts.build_darwin_stage6_tranche4_family_registry_v0 import build_stage6_tranche4_family_registry
from scripts.build_darwin_stage6_tranche4_scorecard_v0 import build_stage6_tranche4_scorecard


def test_build_stage6_composition_canary_marks_not_authorized(tmp_path: Path) -> None:
    registry_path = tmp_path / "family_registry_v0.json"
    scorecard_path = tmp_path / "scorecard_v0.json"
    registry_path.write_text(
        json.dumps(
            {
                "family_center_decision": "hold_single_retained_family_center",
                "rows": [
                    {
                        "family_id": "family.systems.policy",
                        "transfer_status": "retained",
                    },
                    {
                        "family_id": "family.repo.topology",
                        "transfer_status": "activation_probe",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    scorecard_path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "family_id": "family.systems.policy",
                        "transfer_status": "retained",
                        "broader_compounding_confidence": "positive",
                        "interpretation_note": "retained_transfer_backed_positive_compounding",
                    },
                    {
                        "family_id": "family.repo.topology",
                        "transfer_status": "activation_probe",
                        "interpretation_note": "challenge_context_only",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = build_stage6_composition_canary(registry_path=registry_path, scorecard_path=scorecard_path, out_dir=tmp_path / "composition")
    payload = json.loads((tmp_path / "composition" / "composition_canary_v0.json").read_text(encoding="utf-8"))
    assert summary["result"] == "composition_not_authorized"
    assert payload["replay_status"] == "not_required"


def test_build_stage6_tranche4_registry_and_scorecard(tmp_path: Path) -> None:
    registry_path = tmp_path / "tranche3_family_registry_v0.json"
    scorecard_path = tmp_path / "tranche3_scorecard_v0.json"
    composition_dir = tmp_path / "composition"
    composition_dir.mkdir(parents=True, exist_ok=True)
    composition_path = composition_dir / "composition_canary_v0.json"
    registry_path.write_text(
        json.dumps(
            {
                "family_center_decision": "hold_single_retained_family_center",
                "rows": [
                    {"family_id": "family.systems.policy", "lane_id": "lane.systems", "family_state": "retained_transfer_source", "transfer_status": "retained", "replay_status": "supported"},
                    {"family_id": "family.repo.topology", "lane_id": "lane.repo_swe", "family_state": "challenge_only", "transfer_status": "activation_probe", "replay_status": "observed"},
                ],
            }
        ),
        encoding="utf-8",
    )
    scorecard_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"family_id": "family.systems.policy", "transfer_status": "retained", "broader_compounding_confidence": "positive", "broader_compounding_positive_rate": 1.0, "score_lift_vs_family_lockout": 0.2, "score_lift_vs_single_lockout": 0.4, "linkage_result": "retained_transfer_compounds", "replay_status": "supported", "interpretation_note": "retained_transfer_backed_positive_compounding"},
                    {"family_id": "family.repo.topology", "transfer_status": "activation_probe", "broader_compounding_confidence": "positive", "broader_compounding_positive_rate": 1.0, "score_lift_vs_family_lockout": 0.2, "score_lift_vs_single_lockout": 0.4, "linkage_result": "challenge_context_only", "replay_status": "observed", "interpretation_note": "challenge_context_only"},
                ]
            }
        ),
        encoding="utf-8",
    )
    composition_path.write_text(json.dumps({"result": "composition_not_authorized"}), encoding="utf-8")
    registry_summary = build_stage6_tranche4_family_registry(
        registry_path=registry_path,
        composition_path=composition_path,
        out_dir=tmp_path / "tranche4_family_registry",
    )
    scorecard_summary = build_stage6_tranche4_scorecard(
        registry_path=Path(registry_summary["out_json"]),
        tranche3_scorecard_path=scorecard_path,
        composition_path=composition_path,
        out_dir=tmp_path / "tranche4_scorecard",
    )
    registry_payload = json.loads(Path(registry_summary["out_json"]).read_text(encoding="utf-8"))
    scorecard_payload = json.loads(Path(scorecard_summary["out_json"]).read_text(encoding="utf-8"))
    assert registry_payload["composition_decision"] == "composition_not_authorized"
    assert registry_payload["rows"][1]["challenge_relevance"] == "challenge_context_only"
    assert scorecard_payload["composition_decision"] == "composition_not_authorized"
    assert scorecard_payload["rows"][0]["composition_baseline_role"] == "retained_single_family_control"


def test_build_stage6_precloseout_artifacts_emit_refs() -> None:
    canon = build_stage6_canonical_artifact_index()
    bundle = build_stage6_comparative_bundle()
    canon_payload = json.loads(Path(canon["out_json"]).read_text(encoding="utf-8"))
    bundle_payload = json.loads(Path(bundle["out_json"]).read_text(encoding="utf-8"))
    assert any(row["path"] == "artifacts/darwin/stage6/tranche4/composition_canary/composition_canary_v0.json" for row in canon_payload["rows"])
    assert any(row["path"] == "artifacts/darwin/stage6/tranche4/family_registry/family_registry_v0.json" for row in canon_payload["rows"])
    assert bundle_payload["composition_canary_ref"] == "artifacts/darwin/stage6/tranche4/composition_canary/composition_canary_v0.json"
    assert bundle_payload["family_registry_ref"] == "artifacts/darwin/stage6/tranche4/family_registry/family_registry_v0.json"
