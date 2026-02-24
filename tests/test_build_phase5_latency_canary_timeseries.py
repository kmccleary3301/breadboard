from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "build_phase5_latency_canary_timeseries.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("build_phase5_latency_canary_timeseries", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_trend(path: Path, *, scenario: str, observed: float, threshold: float) -> None:
    payload = {
        "thresholds": {"max_frame_gap_seconds": threshold},
        "records": [
            {
                "scenario": scenario,
                "metrics": {"max_frame_gap_seconds": observed},
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_build_phase5_latency_canary_timeseries(tmp_path: Path):
    module = _load_module()
    scenario = "phase4_replay/subagents_concurrency_20_v1"
    p1 = tmp_path / "phase5_roundtrip_latency_trend_20260224-roundtrip5.json"
    p2 = tmp_path / "phase5_roundtrip_latency_trend_20260224-roundtrip10.json"
    p3 = tmp_path / "phase5_roundtrip_latency_trend_20260224-roundtrip11.json"
    _write_trend(p1, scenario=scenario, observed=2.80, threshold=3.2)
    _write_trend(p2, scenario=scenario, observed=2.90, threshold=3.2)
    _write_trend(p3, scenario=scenario, observed=2.95, threshold=3.2)

    payload = module.build_timeseries(
        trend_paths=[p3, p2, p1],  # intentionally unsorted input
        scenario=scenario,
        metric="max_frame_gap_seconds",
    )
    assert payload["schema_version"] == "phase5_latency_canary_timeseries_v1"
    assert payload["summary"]["count"] == 3
    assert payload["points"][0]["stamp"] == "20260224-roundtrip5"
    assert payload["points"][1]["stamp"] == "20260224-roundtrip10"
    assert payload["points"][2]["stamp"] == "20260224-roundtrip11"
    assert payload["summary"]["latest_ratio"] > payload["summary"]["min_ratio"]
