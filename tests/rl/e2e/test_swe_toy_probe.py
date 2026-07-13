from __future__ import annotations

from pathlib import Path

from breadboard.rl.e2e import run_controlled_swe_probe


REPO_ROOT = Path(__file__).resolve().parents[3]
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def test_controlled_swe_toy_probe_runs_10_tasks_with_metrics_and_qc(tmp_path) -> None:
    run = run_controlled_swe_probe(
        package_path=SWE_TOY,
        output_dir=tmp_path,
        run_id="test_m6",
        limit=10,
    )

    assert len(run.rows) == 10
    assert sum(row.row_status == "accepted" for row in run.rows) >= 7
    assert any(row.row_status == "rejected" for row in run.rows)
    assert any(row.row_status == "quarantined" for row in run.rows)
    assert "total_ms" in run.metrics_summary
    assert run.metrics_summary["total_ms"]["p95"] >= run.metrics_summary["total_ms"]["p50"]
    assert run.qc_report["accepted_sample"]
    assert run.qc_report["rejected_sample"]
    assert run.qc_report["quarantined_sample"]
    assert (tmp_path / "run_ledger.jsonl").exists()
    assert (tmp_path / "metrics_summary.json").exists()
    assert (tmp_path / "qc_report.json").exists()


def test_controlled_swe_toy_claim_names_source() -> None:
    run = run_controlled_swe_probe(package_path=SWE_TOY, run_id="test_m6", limit=10)

    assert run.source_claim == "controlled_swe_toy_slice"
    assert "swe_rebench" not in run.source_claim
    assert "swe_gym" not in run.source_claim
