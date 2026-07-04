from __future__ import annotations

from breadboard.rl.runtime import build_warm_vs_cold_report, summarize_stage_metrics


def test_stage_metrics_report_p50_p95() -> None:
    rows = [
        {"metrics_ms": {"total_ms": 10.0, "reset_ms": 2.0}},
        {"metrics_ms": {"total_ms": 20.0, "reset_ms": 4.0}},
        {"metrics_ms": {"total_ms": 30.0, "reset_ms": 6.0}},
    ]

    summary = summarize_stage_metrics(rows)

    assert summary["total_ms"]["p50"] == 20.0
    assert summary["total_ms"]["p95"] == 30.0
    assert summary["reset_ms"]["count"] == 3.0


def test_warm_vs_cold_report_preserves_claim_boundary() -> None:
    warm = [{"metrics_ms": {"total_ms": 10.0}}]
    cold = [{"metrics_ms": {"total_ms": 15.0}}]

    report = build_warm_vs_cold_report(warm_rows=warm, cold_rows=cold)

    assert report["warm"]["total_ms"]["p50"] == 10.0
    assert report["cold"]["total_ms"]["p50"] == 15.0
    assert report["claim_boundary"] == "local_ray_warm_pool_probe_not_production_scale"
