from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_frame(run_dir: Path, index: int, text: str) -> str:
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rel = f"frames/frame_{index:04d}.txt"
    (run_dir / rel).write_text(text, encoding="utf-8")
    return rel


def _write_index(run_dir: Path, rows: list[dict[str, object]]) -> None:
    with (run_dir / "index.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row))
            handle.write("\n")


def test_export_health_includes_qos_metrics(tmp_path: Path):
    module = _load_module("export_phase4_tier3_health_qos", "scripts/export_phase4_tier3_health.py")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        {"timestamp": 100.0, "text": _write_frame(run_dir, 1, "header\nReady\n? for shortcuts")},
        {
            "timestamp": 100.5,
            "text": _write_frame(
                run_dir,
                2,
                "\n".join(
                    [
                        "header",
                        "Working (1s • esc to interrupt)",
                        "? for shortcuts",
                        "statusTransitions=4 statusCommits=3 statusCoalesced=1",
                        "eventCoalesced=2 eventMaxQueueDepth=5 workgraphMaxQueueDepth=2 adaptiveCadenceAdjustments=1",
                    ],
                ),
            ),
        },
        {
            "timestamp": 101.0,
            "text": _write_frame(
                run_dir,
                3,
                "\n".join(
                    [
                        "header",
                        "Working (2s • esc to interrupt)",
                        "? for shortcuts",
                        "statusTransitions=7 statusCommits=6 statusCoalesced=2",
                        "eventCoalesced=9 eventMaxQueueDepth=11 workgraphMaxQueueDepth=4 adaptiveCadenceAdjustments=2",
                    ],
                ),
            ),
        },
        {"timestamp": 101.6, "text": _write_frame(run_dir, 4, "header\nDone\n? for shortcuts")},
    ]
    _write_index(run_dir, rows)

    perf = module._perf_metrics(run_dir)

    assert perf["frame_count"] == 4
    assert perf["interval_p50_ms"] is not None
    assert perf["line_churn_p95"] is not None
    assert perf["status_line_samples"] == 4
    assert perf["status_line_changes"] == 3
    assert perf["status_line_unique"] == 4
    assert perf["status_line_churn_rate"] == 1.0
    assert perf["telemetry_statusTransitions_max"] == 7
    assert perf["telemetry_statusCommits_max"] == 6
    assert perf["telemetry_statusCoalesced_max"] == 2
    assert perf["telemetry_eventCoalesced_max"] == 9
    assert perf["telemetry_eventMaxQueueDepth_max"] == 11
    assert perf["telemetry_workgraphMaxQueueDepth_max"] == 4
    assert perf["telemetry_adaptiveCadenceAdjustments_max"] == 2
    assert perf["telemetry_samples"] == 2


def test_aggregate_and_trend_check_include_qos_thresholds(tmp_path: Path):
    aggregate_mod = _load_module("aggregate_phase4_tier3_health_qos", "scripts/aggregate_phase4_tier3_health.py")
    check_mod = _load_module("check_phase4_tier3_trends_qos", "scripts/check_phase4_tier3_trends.py")

    base_health = {
        "schema_version": "phase4_tier3_health_v2",
        "generated_at_utc": "2026-02-18T00:00:00+00:00",
        "overall_ok": True,
        "rc": {},
        "lanes": {
            "resize_storm": {
                "perf": {
                    "interval_p95_ms": 500.0,
                    "line_churn_p95": 0.12,
                    "status_line_churn_rate": 0.4,
                    "telemetry_eventMaxQueueDepth_max": 20,
                    "telemetry_workgraphMaxQueueDepth_max": 8,
                    "telemetry_statusTransitions_max": 15,
                }
            },
            "concurrency_20": {
                "perf": {
                    "interval_p95_ms": 720.0,
                    "line_churn_p95": 0.18,
                    "status_line_churn_rate": 0.6,
                    "telemetry_eventMaxQueueDepth_max": 42,
                    "telemetry_workgraphMaxQueueDepth_max": 11,
                    "telemetry_statusTransitions_max": 26,
                }
            },
        },
    }

    health_1 = tmp_path / "phase4_tier3_health_01.json"
    health_2 = tmp_path / "phase4_tier3_health_02.json"
    health_1.write_text(json.dumps(base_health), encoding="utf-8")
    base_health["generated_at_utc"] = "2026-02-18T01:00:00+00:00"
    base_health["lanes"]["concurrency_20"]["perf"]["status_line_churn_rate"] = 0.9
    base_health["lanes"]["concurrency_20"]["perf"]["telemetry_eventMaxQueueDepth_max"] = 333
    health_2.write_text(json.dumps(base_health), encoding="utf-8")

    aggregate_out = tmp_path / "aggregate.json"
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "aggregate_phase4_tier3_health.py",
            "--glob",
            str(tmp_path / "phase4_tier3_health_*.json"),
            "--output-json",
            str(aggregate_out),
        ]
        rc = aggregate_mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    aggregate_payload = json.loads(aggregate_out.read_text(encoding="utf-8"))
    assert aggregate_payload["schema_version"] == "phase4_tier3_health_aggregate_v2"
    latest_status_churn = (
        aggregate_payload["trend"]["concurrency_20"]["status_line_churn_rate"]["latest"]
    )
    assert latest_status_churn == 0.9

    trend_report = tmp_path / "trend_report.json"
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "check_phase4_tier3_trends.py",
            "--aggregate-json",
            str(aggregate_out),
            "--mode",
            "strict",
            "--max-status-line-churn-rate",
            "0.7",
            "--max-event-max-queue-depth",
            "256",
            "--output-json",
            str(trend_report),
        ]
        rc = check_mod.main()
    finally:
        sys.argv = old_argv

    assert rc == 2
    report_payload = json.loads(trend_report.read_text(encoding="utf-8"))
    assert report_payload["schema_version"] == "phase4_tier3_trend_check_v2"
    assert any("status_line_churn_rate.latest" in item for item in report_payload["failures"])
    assert any("telemetry_eventMaxQueueDepth_max.latest" in item for item in report_payload["failures"])
