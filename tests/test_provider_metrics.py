from agentic_coder_prototype.provider_metrics import ProviderMetricsCollector


def test_provider_metrics_records_dialect_stats():
    collector = ProviderMetricsCollector()
    collector.add_dialect_metric(dialect="aider_diff", tool="apply_search_replace", pas=1.0, hmr=1.0)
    collector.add_dialect_metric(dialect="aider_diff", tool="apply_search_replace", pas=0.0, hmr=0.0)
    collector.add_dialect_metric(dialect="unified_diff", tool="apply_unified_patch", pas=1.0, hmr=0.75)

    snapshot = collector.snapshot()
    dialects = snapshot["dialects"]

    assert "aider_diff" in dialects
    assert dialects["aider_diff"]["pas_avg"] == 0.5
    assert dialects["aider_diff"]["hmr_avg"] == 0.5
    assert dialects["aider_diff"]["tools"]["apply_search_replace"]["calls"] == 2

    assert "unified_diff" in dialects
    assert dialects["unified_diff"]["pas_avg"] == 1.0
    assert dialects["unified_diff"]["hmr_avg"] == 0.75


def test_provider_metrics_concurrency_samples_snapshot():
    collector = ProviderMetricsCollector()
    collector.add_concurrency_sample(
        turn=3,
        plan={
            "strategy": "nonblocking_concurrent",
            "can_run_concurrent": True,
            "max_workers": 2,
            "group_counts": {"reads": 3},
            "group_limits": {"reads": 2},
            "total_calls": 4,
            "executed_calls": 3,
        },
    )

    snapshot = collector.snapshot()
    samples = snapshot["concurrency_samples"]
    assert len(samples) == 1
    entry = samples[0]
    assert entry["turn"] == 3
    assert entry["strategy"] == "nonblocking_concurrent"
    assert entry["group_counts"]["reads"] == 3
