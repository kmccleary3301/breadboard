from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_zero_importer_advisory_reports_new_modules_against_baseline(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "used.py", "VALUE = 1\n")
    _write(repo / "pkg" / "entry.py", "from pkg import used\n")
    _write(repo / "pkg" / "new_lonely.py", "VALUE = 2\n")
    baseline = repo / "audit.json"
    baseline.write_text(
        json.dumps({"paths": [{"path": "pkg/entry.py", "verdict": "quarantine"}]}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_zero_importer_advisory.py"),
            "--repo-root",
            str(repo),
            "--scan-root",
            "pkg",
            "--baseline-json",
            str(baseline),
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["advisory"] is True
    assert "pkg/new_lonely.py" in report["new_zero_importers"]
    assert "pkg/entry.py" not in report["new_zero_importers"]
    assert "pkg/used.py" not in report["new_zero_importers"]


def test_zero_importer_advisory_can_limit_report_to_changed_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "unchanged_lonely.py", "VALUE = 1\n")
    _write(repo / "pkg" / "changed_lonely.py", "VALUE = 2\n")
    changed = repo / "changed_files.txt"
    changed.write_text("pkg/changed_lonely.py\nREADME.md\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_zero_importer_advisory.py"),
            "--repo-root",
            str(repo),
            "--scan-root",
            "pkg",
            "--changed-files-file",
            str(changed),
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["changed_python_path_count"] == 1
    assert report["new_zero_importers"] == ["pkg/changed_lonely.py"]


def test_zero_importer_advisory_writes_json_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "pkg" / "__init__.py", "")
    _write(repo / "pkg" / "lonely.py", "VALUE = 1\n")
    out = tmp_path / "report.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_zero_importer_advisory.py"),
            "--repo-root",
            str(repo),
            "--scan-root",
            "pkg",
            "--json-out",
            str(out),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["new_zero_importers"] == ["pkg/lonely.py"]
