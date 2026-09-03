"""S-2: SCRIPT_INDEX.yaml must stay in lockstep with tracked scripts/ files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import scripts.dev.build_script_index as script_index

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX = REPO_ROOT / "scripts" / "SCRIPT_INDEX.yaml"


def test_script_index_current() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "dev" / "build_script_index.py"),
            "--check",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"script index drift: {proc.stdout}{proc.stderr}"
    # S-2: live-script count is reported, not capped.
    assert re.search(r"OK \(\d+ live scripts of \d+ tracked\)", proc.stdout), (
        proc.stdout
    )

def test_reference_pattern_ignores_generic_basename_data() -> None:
    """A data path such as ``__init__.py`` is not a script callsite."""
    pattern = script_index._reference_pattern("scripts/__init__.py")
    nested_pattern = script_index._reference_pattern("scripts/e4_parity/adapters/__init__.py")

    assert pattern.search("sdk_root / '__init__.py'") is None
    assert pattern.search("scripts/__init__.py") is not None
    assert pattern.search("scripts.__init__") is not None
    assert pattern.search("from scripts import e4_parity") is not None
    assert nested_pattern.search("from scripts.e4_parity.adapters import identity") is not None


def test_script_index_guard_rejects_noncanonical_rows_and_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    canonical = (
        "# generated\n"
        "schema: bb.script_index.v1\n"
        "live_script_count: 1\n"
        "entries:\n"
        "  - path: scripts/example.py\n"
        "    class: live-owned\n"
    )
    index = tmp_path / "SCRIPT_INDEX.yaml"
    index.write_text(canonical.replace("live-owned", "campaign"), encoding="utf-8")
    monkeypatch.setattr(script_index, "INDEX_PATH", index)
    monkeypatch.setattr(script_index, "build_index", lambda: canonical)
    assert script_index.main(["--check"]) == 1

    index.write_text(canonical + "  - path: scripts/example.py\n", encoding="utf-8")
    assert script_index.main(["--check"]) == 1


def test_live_code_cannot_import_archive() -> None:
    """S-3 guard: no live module imports internals.archive."""
    out = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            r"internals\.archive",
            "--",
            "breadboard_engine",
            "breadboard",
            "breadboard_sdk",
            "scripts",
            "tests",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    offenders = [
        p for p in out.stdout.splitlines() if p != "tests/test_script_index_guard.py"
    ]
    assert not offenders, f"live code imports internals.archive: {offenders}"
