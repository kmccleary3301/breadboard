"""T-4: committed sdk/ts generated output must match the in-process generator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN = REPO_ROOT / "sdk" / "ts" / "src" / "generated"


def test_generated_ts_sdk_is_current() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "dev" / "generate_ts_sdk.py"),
            "--check",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"stale generated SDK: {proc.stdout}{proc.stderr}"


def test_generated_routes_carry_hash_headers_and_dead_dtos_stay_removed() -> None:
    head = (GEN / "routes.ts").read_text(encoding="utf-8").splitlines()[:5]
    assert any("openapi-schema-sha256:" in line for line in head)
    assert any("app-source-sha256:" in line for line in head)
    assert not (GEN / "dtos.ts").exists()
