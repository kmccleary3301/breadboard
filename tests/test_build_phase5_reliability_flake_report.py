from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "build_phase5_reliability_flake_report.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("build_phase5_reliability_flake_report", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_report(path: Path, scenario_rows: list[tuple[str, bool, int]]):
    records = []
    for scenario, passed, isolated in scenario_rows:
        records.append(
            {
                "scenario": scenario,
                "checks": [{"name": "isolated_low_edge_rows_total", "pass": passed}],
                "metrics": {"isolated_low_edge_rows_total": isolated},
            }
        )
    payload = {"schema_version": "phase5_replay_reliability_report_v1", "records": records}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_build_report_classifies_flakes_and_persistent(tmp_path: Path):
    module = _load_module()
    f1 = tmp_path / "phase5_roundtrip_reliability_20260224-roundtrip1.json"
    f2 = tmp_path / "phase5_roundtrip_reliability_20260224-roundtrip2.json"
    f3 = tmp_path / "phase5_roundtrip_reliability_20260224-roundtrip3.json"

    # everything_showcase: fail then pass (intermittent-flake)
    # subagents: fail twice in a row (persistent-fail)
    _write_report(
        f1,
        [
            ("phase4_replay/everything_showcase_v1_fullpane_v1", False, 2),
            ("phase4_replay/subagents_v1_fullpane_v7", False, 3),
        ],
    )
    _write_report(
        f2,
        [
            ("phase4_replay/everything_showcase_v1_fullpane_v1", True, 0),
            ("phase4_replay/subagents_v1_fullpane_v7", False, 2),
        ],
    )
    _write_report(
        f3,
        [
            ("phase4_replay/everything_showcase_v1_fullpane_v1", True, 0),
            ("phase4_replay/subagents_v1_fullpane_v7", False, 2),
        ],
    )

    report = module.build_report(
        files=[f1, f2, f3],
        check_name="isolated_low_edge_rows_total",
        scenario_filter=None,
    )
    by_scenario = {row["scenario"]: row for row in report["scenarios"]}
    assert by_scenario["phase4_replay/everything_showcase_v1_fullpane_v1"]["classification"] == "intermittent-flake"
    assert by_scenario["phase4_replay/subagents_v1_fullpane_v7"]["classification"] == "persistent-fail"
    assert report["overall_ok"] is False


def test_build_report_filter_single_scenario(tmp_path: Path):
    module = _load_module()
    f1 = tmp_path / "phase5_roundtrip_reliability_20260224-roundtrip1.json"
    _write_report(
        f1,
        [
            ("phase4_replay/everything_showcase_v1_fullpane_v1", True, 0),
            ("phase4_replay/subagents_v1_fullpane_v7", True, 0),
        ],
    )
    report = module.build_report(
        files=[f1],
        check_name="isolated_low_edge_rows_total",
        scenario_filter="phase4_replay/subagents_v1_fullpane_v7",
    )
    assert report["scenario_count"] == 1
    assert report["scenarios"][0]["scenario"] == "phase4_replay/subagents_v1_fullpane_v7"


def test_build_report_classifies_transient_single_recovery(tmp_path: Path):
    module = _load_module()
    files = []
    statuses = [True, True, True, False, True, True, True]
    for i, passed in enumerate(statuses, start=1):
        fp = tmp_path / f"phase5_roundtrip_reliability_20260224-roundtrip{i}.json"
        _write_report(fp, [("phase4_replay/everything_showcase_v1_fullpane_v1", passed, 0 if passed else 2)])
        files.append(fp)

    report = module.build_report(
        files=files,
        check_name="isolated_low_edge_rows_total",
        scenario_filter=None,
    )
    row = report["scenarios"][0]
    assert row["classification"] == "transient-single-recovery"
    assert row["latest_pass"] is True
    assert row["fail_count"] == 1
    assert row["sample_count"] == 7
    assert report["overall_ok"] is True
