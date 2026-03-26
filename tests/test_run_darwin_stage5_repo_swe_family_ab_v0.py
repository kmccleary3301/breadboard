from __future__ import annotations

import json
from pathlib import Path

from scripts.bootstrap_darwin_campaign_specs_v0 import write_bootstrap_specs
from scripts.run_darwin_stage5_repo_swe_family_ab_v0 import run_stage5_repo_swe_family_ab

ROOT = Path(__file__).resolve().parents[1]


def _repo_tmp(tmp_path: Path, name: str) -> Path:
    path = ROOT / "artifacts" / "test_tmp" / tmp_path.name / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_run_stage5_repo_swe_family_ab_emits_both_family_variants(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    write_bootstrap_specs()
    summary = run_stage5_repo_swe_family_ab(rounds=1, out_dir=_repo_tmp(tmp_path, "repo_swe_family_ab"))
    payload = json.loads(open(summary["summary_path"], "r", encoding="utf-8").read())
    assert payload["family_count"] == 2
    assert payload["row_count"] == 2
    assert set(payload["family_totals"]) == {"topology", "tool_scope"}
    assert payload["expected_row_count"] == 2
    assert payload["completion_status"] in {"complete", "stale_or_incomplete"}


def test_run_stage5_repo_swe_family_ab_does_not_overwrite_previous_bundle_on_failure(monkeypatch, tmp_path: Path) -> None:
    import scripts.run_darwin_stage5_repo_swe_family_ab_v0 as module

    out_dir = tmp_path / "repo_swe_family_ab"
    out_path = out_dir / "repo_swe_family_ab_v0.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"sentinel": "previous"}), encoding="utf-8")
    calls = {"count": 0}

    def fake_run_stage5_compounding_pilot(*, lane_id: str, out_dir: Path, round_index: int, family_probe_override_kind: str | None = None) -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("interrupted")
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / "compounding_pilot_v0.json"
        summary_path.write_text(
            json.dumps(
                {
                    "claim_eligible_comparison_count": 6,
                    "comparison_valid_count": 6,
                    "reuse_lift_count": 2,
                    "flat_count": 1,
                    "no_lift_count": 0,
                }
            ),
            encoding="utf-8",
        )
        return {"summary_path": str(summary_path)}

    monkeypatch.setattr(module, "run_stage5_compounding_pilot", fake_run_stage5_compounding_pilot)
    try:
        run_stage5_repo_swe_family_ab(rounds=1, out_dir=out_dir)
    except RuntimeError:
        pass
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload == {"sentinel": "previous"}
