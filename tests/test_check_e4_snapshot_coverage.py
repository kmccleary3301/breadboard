from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_check_e4_snapshot_coverage_passes_on_repo() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_e4_snapshot_coverage.py", "--json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["missing_snapshot_row_count"] == 0


def test_check_e4_snapshot_coverage_flags_base_rows_without_snapshots(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "agent_configs").mkdir(parents=True)
    (repo_root / "config").mkdir(parents=True)
    (repo_root / "agent_configs" / "sample_e4.yaml").write_text("model: test\n", encoding="utf-8")
    payload = {
        "schema_version": "e4_target_freeze_manifest_v1",
        "manifest_updated_utc": "2026-03-05T00:00:00Z",
        "e4_configs": {
            "sample_e4": {
                "config_path": "agent_configs/sample_e4.yaml",
                "harness": {"family": "sample"},
                "calibration_anchor": {
                    "class": "fixture",
                    "scenario_id": "sample",
                    "evidence_paths": ["docs/sample.json"],
                },
            }
        },
    }
    (repo_root / "config" / "e4_target_freeze_manifest.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_e4_snapshot_coverage.py"),
            "--repo-root",
            str(repo_root),
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["missing_snapshot_rows"] == ["sample_e4"]
