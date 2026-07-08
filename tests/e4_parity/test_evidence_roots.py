from __future__ import annotations
import json

from pathlib import Path

import pytest

from scripts.e4_parity import evidence_roots


def test_evidence_root_resolves_relative_from_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    resolved = evidence_roots.evidence_root("../docs_tmp", workspace_root=workspace)

    assert resolved == tmp_path / "docs_tmp"


def test_resolve_evidence_path_rejects_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    with pytest.raises(ValueError, match="escapes root"):
        evidence_roots.resolve_evidence_path("..", "outside.json", root="../docs_tmp", workspace_root=workspace)


def test_docs_tmp_literal_lint_reports_unallowed_files(tmp_path: Path) -> None:
    checked = tmp_path / "checked.py"
    checked.write_text('PATH = "docs_tmp/phase_17"\n', encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text('PATH = resolve_evidence_path("phase_17")\n', encoding="utf-8")

    findings = evidence_roots.find_docs_tmp_literals([checked, clean], workspace_root=tmp_path, allowed_files=set())

    assert [finding.path for finding in findings] == ["checked.py"]
    assert findings[0].line == 1


def test_docs_tmp_literal_lint_honors_allowed_files(tmp_path: Path) -> None:
    checked = tmp_path / "checked.py"
    checked.write_text('PATH = "docs_tmp/phase_17"\n', encoding="utf-8")

    findings = evidence_roots.find_docs_tmp_literals([checked], workspace_root=tmp_path, allowed_files={"checked.py"})

    assert findings == []


def test_literal_baseline_keys_existing_docs_tmp_lines(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"literals": [{"path": "checked.py", "line": 1, "text": 'PATH = "docs_tmp/phase_17"'}]}),
        encoding="utf-8",
    )

    assert evidence_roots.load_literal_baseline(baseline) == {("checked.py", 'PATH = "docs_tmp/phase_17"')}
