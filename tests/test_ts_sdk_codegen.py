"""T-4: committed sdk/ts generated output must match the in-process generator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_generated_ts_sdk_is_current() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "dev" / "generate_ts_sdk.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"stale generated SDK: {proc.stdout}{proc.stderr}"


def test_generated_files_carry_hash_headers() -> None:
    gen = REPO_ROOT / "sdk" / "ts" / "src" / "generated"
    for name in ("dtos.ts", "routes.ts"):
        head = (gen / name).read_text(encoding="utf-8").splitlines()[:4]
        assert any("openapi-schema-sha256:" in line for line in head), name
        assert any("app-source-sha256:" in line for line in head), name
