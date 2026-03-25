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
    lane_map = {row["lane_id"]: row for row in payload["rows"]}
    assert lane_map["lane.systems"]["lane_weight"] in {"primary_proving_lane", "unset"}
    assert lane_map["lane.repo_swe"]["lane_weight"] in {"challenge_lane", "unset"}
