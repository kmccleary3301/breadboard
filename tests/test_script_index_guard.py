"""S-2: SCRIPT_INDEX.yaml must stay in lockstep with tracked scripts/ files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "scripts" / "SCRIPT_INDEX.yaml"


def test_script_index_current() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "dev" / "build_script_index.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"script index drift: {proc.stdout}{proc.stderr}"
    # S-2: live-script count is reported, not capped.
    assert re.search(r"OK \(\d+ live scripts of \d+ tracked\)", proc.stdout), proc.stdout


def test_live_code_cannot_import_archive() -> None:
    """S-3 guard: no live module imports internals.archive."""
    out = subprocess.run(
        ["git", "grep", "-l", r"internals\.archive", "--", 
         "breadboard_engine", "breadboard", "breadboard_sdk", "scripts", "tests"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    offenders = [p for p in out.stdout.splitlines() if p != "tests/test_script_index_guard.py"]
    assert not offenders, f"live code imports internals.archive: {offenders}"
