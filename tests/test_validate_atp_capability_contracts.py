from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    repo = _repo_root()
    cmd = [sys.executable, str(repo / "scripts" / "validate_atp_capability_contracts.py"), *args]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True)


def test_validate_atp_capability_contracts_passes_default_fixture(tmp_path: Path) -> None:
    out_path = tmp_path / "report.json"
    result = _run("--json-out", str(out_path))
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["error_count"] == 0


def test_validate_atp_capability_contracts_fails_for_bad_fixture(tmp_path: Path) -> None:
    repo = _repo_root()
    fixture = json.loads(
        (repo / "tests" / "fixtures" / "atp_capabilities" / "retrieval_solver_replay_fixture_v1.json").read_text(
            encoding="utf-8"
        )
    )
    del fixture["retrieval_request"]["schema_version"]
    fixture_path = tmp_path / "fixture.bad.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_path = tmp_path / "report.bad.json"

    result = _run("--fixture", str(fixture_path), "--json-out", str(out_path))
    assert result.returncode != 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["error_count"] >= 1
    assert any("retrieval_request" in error for error in payload["errors"])


def test_validate_atp_capability_contracts_fails_for_bad_decomposition_trace(tmp_path: Path) -> None:
    repo = _repo_root()
    fixture = json.loads(
        (repo / "tests" / "fixtures" / "atp_capabilities" / "retrieval_solver_replay_fixture_v1.json").read_text(
            encoding="utf-8"
        )
    )
    del fixture["decomposition_trace"]["nodes"][1]["node_role"]
    fixture_path = tmp_path / "fixture.bad_decomp.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_path = tmp_path / "report.bad_decomp.json"
    result = _run("--fixture", str(fixture_path), "--json-out", str(out_path))
    assert result.returncode != 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert any("decomposition_trace" in error for error in payload["errors"])
