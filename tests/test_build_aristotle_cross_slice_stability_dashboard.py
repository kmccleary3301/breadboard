from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module():
    repo = _repo_root()
    path = repo / "scripts" / "build_aristotle_cross_slice_stability_dashboard.py"
    scripts_dir = str((repo / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("build_aristotle_cross_slice_stability_dashboard", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_aristotle_cross_slice_stability_dashboard"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_summary(path: Path, *, baseline_key: str, baseline_status: dict, rerun3_status: dict) -> None:
    payload = {
        "schema": "breadboard.aristotle_stability_drift_summary.v1",
        "runs": {
            baseline_key: {
                "status_counts": baseline_status,
                "wall_clock_ms": {"mean": 100.0, "median": 90.0, "max": 120.0},
            },
            "rerun_1": {
                "status_counts": {"TIMEOUT": 3, "SOLVED": 2},
                "wall_clock_ms": {"mean": 120.0, "median": 110.0, "max": 140.0},
            },
            "rerun_2": {
                "status_counts": {"TIMEOUT": 2, "SOLVED": 3},
                "wall_clock_ms": {"mean": 110.0, "median": 100.0, "max": 130.0},
            },
            "rerun_3": {
                "status_counts": rerun3_status,
                "wall_clock_ms": {"mean": 105.0, "median": 100.0, "max": 125.0},
            },
        },
        "pairwise_comparisons": [
            {
                "from": baseline_key,
                "to": "rerun_1",
                "status_flip_count": 5,
                "status_flip_rate": 1.0,
                "digest_flip_count": 5,
                "digest_flip_rate": 1.0,
                "transitions": {"ERROR->TIMEOUT": 3, "ERROR->SOLVED": 2},
            },
            {
                "from": "rerun_1",
                "to": "rerun_2",
                "status_flip_count": 2,
                "status_flip_rate": 0.4,
                "digest_flip_count": 5,
                "digest_flip_rate": 1.0,
                "transitions": {"TIMEOUT->SOLVED": 1, "SOLVED->TIMEOUT": 1},
            },
            {
                "from": "rerun_2",
                "to": "rerun_3",
                "status_flip_count": 1,
                "status_flip_rate": 0.2,
                "digest_flip_count": 5,
                "digest_flip_rate": 1.0,
                "transitions": {"TIMEOUT->SOLVED": 1},
            },
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_build_dashboard_and_markdown(tmp_path: Path) -> None:
    mod = _load_module()
    putnam = tmp_path / "putnam_summary.json"
    minif2f = tmp_path / "minif2f_summary.json"
    _write_summary(
        putnam,
        baseline_key="baseline_probe_n5",
        baseline_status={"ERROR": 5},
        rerun3_status={"ERROR": 5},
    )
    _write_summary(
        minif2f,
        baseline_key="baseline_head5",
        baseline_status={"ERROR": 5},
        rerun3_status={"SOLVED": 3, "TIMEOUT": 2},
    )

    payload = mod.build_dashboard(putnam_summary_path=putnam, minif2f_summary_path=minif2f)
    assert payload["schema"] == "breadboard.aristotle_cross_slice_stability_dashboard.v1"
    per_run = payload["per_run_comparison"]
    assert len(per_run) == 4
    assert per_run[0]["run"] == "baseline"
    assert per_run[0]["putnam"]["status_counts"] == {"ERROR": 5}
    assert per_run[3]["minif2f"]["status_counts"] == {"SOLVED": 3, "TIMEOUT": 2}

    pairwise = payload["pairwise_drift_comparison"]
    assert len(pairwise) == 3
    assert pairwise[1]["transition"] == "rerun_1→rerun_2"
    assert pairwise[1]["putnam"]["status_flip_count"] == 2
    assert pairwise[1]["minif2f"]["digest_flip_count"] == 5

    markdown = mod._render_markdown(payload)
    assert "Cross-Slice Aristotle Stability Drift Dashboard" in markdown
    assert "| baseline |" in markdown
    assert "## Pairwise Drift Comparison" in markdown
