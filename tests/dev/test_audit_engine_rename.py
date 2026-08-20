"""R-0A3 guard: the rename audit must fail on unclassified occurrences."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT = REPO_ROOT / "scripts" / "dev" / "audit_engine_rename.py"
NEEDLE = "agentic_coder" + "_prototype"


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    target = root / "unmatched_zone" / "module.py"
    target.parent.mkdir()
    target.write_text(f"import {NEEDLE}.compilation\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def _run(root: Path, out: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(AUDIT), "--root", str(root), "--out", str(out), *flags],
        capture_output=True,
        text=True,
    )


def test_unclassified_occurrence_fails_audit(tmp_path):
    root = _fixture_repo(tmp_path)
    out = tmp_path / "manifest.json"
    result = _run(root, out, "--no-catch-all")
    assert result.returncode == 2, result.stdout + result.stderr
    manifest = json.loads(out.read_text())
    assert manifest["unclassified_count"] == 1
    assert manifest["unclassified"][0]["path"] == "unmatched_zone/module.py"


def test_catch_all_classifies_and_passes(tmp_path):
    root = _fixture_repo(tmp_path)
    out = tmp_path / "manifest.json"
    result = _run(root, out)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads(out.read_text())
    assert manifest["unclassified_count"] == 0
    assert manifest["totals_by_disposition"]["live-rewrite"] == 1
    (entry,) = manifest["files"]
    assert entry["sha256"] and entry["occurrences"][0]["disposition"] == "live-rewrite"
