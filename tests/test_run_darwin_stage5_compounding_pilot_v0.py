from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot
from scripts.run_darwin_stage5_multilane_compounding_v0 import run_stage5_multilane_compounding
from scripts.run_darwin_stage5_systems_weighted_compounding_v0 import run_stage5_systems_weighted_compounding

ROOT = Path(__file__).resolve().parents[1]


def _repo_tmp(tmp_path: Path, name: str) -> Path:
    path = ROOT / "artifacts" / "test_tmp" / tmp_path.name / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_run_stage5_compounding_pilot_emits_compounding_cases_in_scaffold_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage5_compounding_pilot(lane_id="lane.repo_swe", out_dir=_repo_tmp(tmp_path, "lane_repo_swe"))
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["pilot_lane_id"] == "lane.repo_swe"
    assert payload["arm_count"] >= 3
    assert payload["comparison_count"] >= 2
    assert payload["compounding_case_count"] >= 1
    assert payload["claim_eligible_comparison_count"] >= 0
    assert "provider_origin_counts" in payload


def test_run_stage5_systems_compounding_pilot_emits_compounding_cases_in_scaffold_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage5_compounding_pilot(lane_id="lane.systems", out_dir=_repo_tmp(tmp_path, "lane_systems"))
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["pilot_lane_id"] == "lane.systems"
    assert payload["comparison_count"] >= 2
    assert payload["compounding_case_count"] >= 1


def test_run_stage5_multilane_compounding_emits_repo_and_systems_rows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage5_multilane_compounding(rounds=2, out_dir=_repo_tmp(tmp_path, "multilane"))
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["round_count"] == 2
    assert payload["lane_count"] == 2
    assert payload["row_count"] == 4
    lane_ids = {row["lane_id"] for row in payload["rows"]}
    assert lane_ids == {"lane.repo_swe", "lane.systems"}


def test_run_stage5_systems_weighted_compounding_emits_lane_weights(monkeypatch, tmp_path: Path) -> None:
    import scripts.run_darwin_stage5_systems_weighted_compounding_v0 as module

    def fake_run_stage5_compounding_pilot(*, lane_id: str, out_dir: Path, round_index: int, family_probe_override_kind: str | None = None) -> dict[str, object]:
        out_dir.mkdir(parents=True, exist_ok=True)
        policy_path = out_dir / "search_policy_v2.json"
        policy_path.write_text(
            json.dumps(
                {
                    "cross_lane_weighting": {
                        "lane_weight": "primary_proving_lane" if lane_id == "lane.systems" else "challenge_lane",
                    }
                }
            ),
            encoding="utf-8",
        )
        summary_path = out_dir / "compounding_pilot_v0.json"
        summary_path.write_text(
            json.dumps(
                    {
                        "run_completion_status": "complete",
                        "live_claim_surface_status": "claim_eligible_live",
                        "claim_eligible_comparison_count": 0,
                        "comparison_valid_count": 8,
                        "reuse_lift_count": 0,
                        "no_lift_count": 0,
                        "flat_count": 0,
                        "policy_ref": str(policy_path),
                    }
                ),
            encoding="utf-8",
        )
        return {"summary_path": str(summary_path)}

    monkeypatch.setattr(module, "run_stage5_compounding_pilot", fake_run_stage5_compounding_pilot)
    summary = run_stage5_systems_weighted_compounding(rounds=1, out_dir=_repo_tmp(tmp_path, "systems_weighted"))
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["round_count"] == 1
    assert payload["lane_count"] == 2
    assert payload["bundle_complete"] is True
    lane_map = {row["lane_id"]: row for row in payload["rows"]}
    assert lane_map["lane.systems"]["lane_weight"] in {"primary_proving_lane", "unset"}
    assert lane_map["lane.repo_swe"]["lane_weight"] in {"challenge_lane", "unset"}
    assert lane_map["lane.systems"]["lane_execution_status"] == "completed_live"
    assert lane_map["lane.repo_swe"]["lane_execution_status"] == "completed_live"


def test_run_stage5_systems_weighted_compounding_preserves_previous_lane_on_failure(monkeypatch, tmp_path: Path) -> None:
    import scripts.run_darwin_stage5_systems_weighted_compounding_v0 as module

    out_dir = _repo_tmp(tmp_path, "systems_weighted_preserve")
    existing_lane_dir = out_dir / "round_r1" / "lane_repo_swe"
    existing_lane_dir.mkdir(parents=True, exist_ok=True)
    (existing_lane_dir / "compounding_pilot_v0.json").write_text(
        json.dumps({"run_completion_status": "complete", "live_claim_surface_status": "claim_eligible_live"}),
        encoding="utf-8",
    )
    calls = {"count": 0}

    def fake_run_stage5_compounding_pilot(*, lane_id: str, out_dir: Path, round_index: int, family_probe_override_kind: str | None = None) -> dict[str, object]:
        calls["count"] += 1
        if lane_id == "lane.repo_swe":
            raise RuntimeError("boom")
        out_dir.mkdir(parents=True, exist_ok=True)
        policy_path = out_dir / "search_policy_v2.json"
        policy_path.write_text(json.dumps({"cross_lane_weighting": {"lane_weight": "primary_proving_lane"}}), encoding="utf-8")
        summary_path = out_dir / "compounding_pilot_v0.json"
        summary_path.write_text(
            json.dumps(
                {
                    "run_completion_status": "complete",
                    "live_claim_surface_status": "claim_eligible_live",
                    "claim_eligible_comparison_count": 8,
                    "comparison_valid_count": 8,
                    "reuse_lift_count": 1,
                    "no_lift_count": 0,
                    "flat_count": 0,
                    "policy_ref": str(policy_path),
                }
            ),
            encoding="utf-8",
        )
        return {"summary_path": str(summary_path)}

    monkeypatch.setattr(module, "run_stage5_compounding_pilot", fake_run_stage5_compounding_pilot)
    summary = run_stage5_systems_weighted_compounding(rounds=1, out_dir=out_dir)
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    lane_map = {row["lane_id"]: row for row in payload["rows"]}
    assert payload["bundle_complete"] is False
    assert lane_map["lane.repo_swe"]["lane_execution_status"] == "failed"
    assert json.loads((existing_lane_dir / "compounding_pilot_v0.json").read_text(encoding="utf-8"))["run_completion_status"] == "complete"


def test_run_stage5_compounding_pilot_normalizes_relative_out_dir(monkeypatch, tmp_path: Path) -> None:
    import scripts.run_darwin_stage5_compounding_pilot_v0 as module

    relative_out_dir = Path("artifacts") / "test_tmp" / tmp_path.name / "relative_stage5_pilot"

    monkeypatch.setattr(
        module,
        "build_stage5_search_policy_v2",
        lambda **_: {
            "policy_id": "policy.stage5.repo_swe.v2",
            "campaign_class": "C1 Discovery",
            "repetition_count": 1,
            "policy_digest": "digest",
        },
    )
    monkeypatch.setattr(
        module,
        "select_stage5_search_policy_arms",
        lambda **_: [
            {
                "campaign_arm_id": "arm.repo_swe.control.stage5.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "baseline_seed",
                "budget_class": "class_a",
                "control_tag": "control",
                "comparison_mode": "cold_start",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": []},
                "search_policy_selection": {"policy_digest": "digest"},
            }
        ],
    )
    monkeypatch.setattr(module, "_campaign_lookup", lambda: {"lane.repo_swe": {"lane_id": "lane.repo_swe"}})
    monkeypatch.setattr(module, "_candidate_universe", lambda **_: [])
    monkeypatch.setattr(
        module,
        "_run_arm",
        lambda **_: (
            {
                "campaign_arm_id": "arm.repo_swe.control.stage5.v0",
                "lane_id": "lane.repo_swe",
                "operator_id": "baseline_seed",
                "budget_class": "class_a",
                "control_tag": "control",
            },
            [
                {
                    "campaign_arm_id": "arm.repo_swe.control.stage5.v0",
                    "lane_id": "lane.repo_swe",
                    "operator_id": "baseline_seed",
                    "repetition_index": 1,
                    "execution_mode": "live",
                    "comparison_mode": "cold_start",
                    "search_policy_selection": {"policy_digest": "digest"},
                }
            ],
            [{"provider_origin": "openai"}],
        ),
    )
    monkeypatch.setattr(module, "build_stage4_live_comparisons", lambda *_: [])
    monkeypatch.setattr(module, "build_stage5_compounding_cases", lambda **_: [])

    summary = run_stage5_compounding_pilot(lane_id="lane.repo_swe", out_dir=relative_out_dir)
    payload = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
    assert payload["policy_ref"].startswith("artifacts/test_tmp/")
    assert Path(summary["summary_path"]).is_absolute()
