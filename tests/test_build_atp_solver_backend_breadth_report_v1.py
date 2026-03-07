from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_build_atp_solver_backend_breadth_report_v1(tmp_path: Path) -> None:
    repo = _repo_root()
    matrix_out = tmp_path / "matrix_report.json"
    matrix_artifacts = tmp_path / "matrix_artifacts"
    matrix_cmd = [
        sys.executable,
        str(repo / "scripts" / "run_atp_specialist_solver_fallback_matrix_v1.py"),
        "--schema-dir",
        "docs/contracts/atp/schemas",
        "--seed",
        "20260223",
        "--artifacts-dir",
        str(matrix_artifacts),
        "--out",
        str(matrix_out),
    ]
    matrix_result = subprocess.run(matrix_cmd, cwd=repo, capture_output=True, text=True)
    assert matrix_result.returncode == 0, matrix_result.stdout + "\n" + matrix_result.stderr

    report_out = tmp_path / "backend_breadth.json"
    md_out = tmp_path / "backend_breadth.md"
    report_cmd = [
        sys.executable,
        str(repo / "scripts" / "build_atp_solver_backend_breadth_report_v1.py"),
        "--matrix-report",
        str(matrix_out),
        "--json-out",
        str(report_out),
        "--md-out",
        str(md_out),
    ]
    report_result = subprocess.run(report_cmd, cwd=repo, capture_output=True, text=True)
    assert report_result.returncode == 0, report_result.stdout + "\n" + report_result.stderr

    payload = json.loads(report_out.read_text(encoding="utf-8"))
    assert payload["schema"] == "breadboard.atp.solver_backend_breadth_report.v1"
    assert payload["trace_validation_ok"] is True
    assert payload["ok"] is True
    assert payload["required_backend_coverage_rate"] == 1.0
    assert payload["missing_backends"] == []
    assert "geometry_stub" in payload["observed_backends"]
    assert "algebra_stub" in payload["observed_backends"]
    assert "number_theory_stub" in payload["observed_backends"]
    assert "smt_stub" in payload["observed_backends"]
    assert "general_stub" in payload["observed_backends"]
    assert md_out.exists()
