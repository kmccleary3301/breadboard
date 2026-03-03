from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_e4_target_freeze_manifest_validator_passes() -> None:
    cmd = [
        sys.executable,
        "scripts/check_e4_target_freeze_manifest.py",
        "--json",
    ]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["declared_e4_config_count"] == payload["expected_e4_config_count"]


def test_e4_target_freeze_manifest_validator_freshness_flag_smoke() -> None:
    cmd = [
        sys.executable,
        "scripts/check_e4_target_freeze_manifest.py",
        "--json",
        "--max-evidence-age-days",
        "100000",
    ]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
