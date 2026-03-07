from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_check_atp_reliability_lanes_pass(tmp_path: Path) -> None:
    repo = _repo_root()
    contract = _write(tmp_path / "contract.json", {"ok": True})
    retrieval = _write(
        tmp_path / "retrieval.json",
        {"decomposition_event_validation_ok": True, "decomposition_trace_validation_ok": True, "replay_ok": True},
    )
    specialist = _write(tmp_path / "specialist.json", {"trace_validation": {"ok": True}})
    kpi = _write(
        tmp_path / "kpi.json",
        {
            "atp": {
                "retrieval_subset_solve_rate": 0.8,
                "specialist_fallback_coverage_rate": 0.6,
                "retrieval_falsification_max_delta": -0.9,
            }
        },
    )
    out_path = tmp_path / "lane_report.json"
    cmd = [
        sys.executable,
        str(repo / "scripts" / "check_atp_reliability_lanes_v1.py"),
        "--contract-validation",
        str(contract),
        "--retrieval-loop-report",
        str(retrieval),
        "--specialist-fallback-report",
        str(specialist),
        "--kpi-snapshot",
        str(kpi),
        "--json-out",
        str(out_path),
    ]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["lanes"]["ALWAYS_PASSES"]["failed_checks"] == 0


def test_check_atp_reliability_lanes_fail(tmp_path: Path) -> None:
    repo = _repo_root()
    contract = _write(tmp_path / "contract.json", {"ok": False})
    retrieval = _write(
        tmp_path / "retrieval.json",
        {"decomposition_event_validation_ok": False, "decomposition_trace_validation_ok": False, "replay_ok": False},
    )
    specialist = _write(tmp_path / "specialist.json", {"trace_validation": {"ok": False}})
    kpi = _write(
        tmp_path / "kpi.json",
        {
            "atp": {
                "retrieval_subset_solve_rate": 0.2,
                "specialist_fallback_coverage_rate": 0.0,
                "retrieval_falsification_max_delta": -0.01,
            }
        },
    )
    out_path = tmp_path / "lane_report.json"
    cmd = [
        sys.executable,
        str(repo / "scripts" / "check_atp_reliability_lanes_v1.py"),
        "--contract-validation",
        str(contract),
        "--retrieval-loop-report",
        str(retrieval),
        "--specialist-fallback-report",
        str(specialist),
        "--kpi-snapshot",
        str(kpi),
        "--json-out",
        str(out_path),
    ]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["lanes"]["ALWAYS_PASSES"]["failed_checks"] >= 1
    assert payload["lanes"]["USUALLY_PASSES"]["failed_checks"] >= 1
