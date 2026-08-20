"""R-0E1: codemod driver applies only manifest-approved rewrites, refuses the rest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))

from rename_codemod import RefusedRewrite, RenameCodemod, sha256_bytes  # noqa: E402


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "pkg").mkdir()
    live = tmp_path / "pkg" / "live.py"
    live.write_text("import old_engine.core\nX = 'old_engine'\n", encoding="utf-8")
    frozen = tmp_path / "pkg" / "frozen.json"
    frozen.write_text('{"ref": "old_engine.core"}\n', encoding="utf-8")
    manifest = {
        "files": [
            {
                "path": "pkg/live.py",
                "sha256": sha256_bytes(live.read_bytes()),
                "occurrences": [
                    {"line": 1, "count": 1, "kind": "python-source", "disposition": "live-rewrite"},
                    {"line": 2, "count": 1, "kind": "python-source", "disposition": "live-rewrite"},
                ],
            },
            {
                "path": "pkg/frozen.json",
                "sha256": sha256_bytes(frozen.read_bytes()),
                "occurrences": [
                    {"line": 1, "count": 1, "kind": "json-config", "disposition": "preserve-byte"},
                ],
            },
        ]
    }
    return tmp_path, manifest


def make(manifest, root, **kw):
    return RenameCodemod(manifest, old="old_engine", new="new_engine", root=root, **kw)


def test_refuses_preserved_file(tree):
    root, manifest = tree
    codemod = make(manifest, root)
    with pytest.raises(RefusedRewrite):
        codemod.rewrite_file("pkg/frozen.json", apply=True)
    assert (root / "pkg" / "frozen.json").read_text() == '{"ref": "old_engine.core"}\n'


def test_refuses_unlisted_file(tree):
    root, manifest = tree
    (root / "pkg" / "rogue.py").write_text("import old_engine\n", encoding="utf-8")
    with pytest.raises(RefusedRewrite):
        make(manifest, root).rewrite_file("pkg/rogue.py", apply=True)


def test_check_mode_writes_nothing(tree):
    root, manifest = tree
    before = (root / "pkg" / "live.py").read_bytes()
    result = make(manifest, root).rewrite_file("pkg/live.py", apply=False)
    assert result["status"] == "would-rewrite"
    assert (root / "pkg" / "live.py").read_bytes() == before


def test_apply_rewrites_and_validates_python(tree):
    root, manifest = tree
    result = make(manifest, root).rewrite_file("pkg/live.py", apply=True)
    assert result["status"] == "rewritten"
    text = (root / "pkg" / "live.py").read_text()
    assert "new_engine.core" in text and "old_engine" not in text


def test_stale_bytes_block_rewrite(tree):
    root, manifest = tree
    (root / "pkg" / "live.py").write_text("import old_engine  # drifted\n", encoding="utf-8")
    result = make(manifest, root).rewrite_file("pkg/live.py", apply=True)
    assert result["status"] == "stale"
    assert "drifted" in (root / "pkg" / "live.py").read_text()


def test_syntax_error_blocks_write(tree):
    root, manifest = tree
    # A rewrite that would break parsing: craft a file where replacement yields
    # invalid syntax (identifier glued to a longer name is still valid, so use
    # a string-quote collision instead).
    bad = root / "pkg" / "live.py"
    bad.write_text("x = 'old_engine\n", encoding="utf-8")  # already unparsable
    manifest["files"][0]["sha256"] = sha256_bytes(bad.read_bytes())
    result = make(manifest, root).rewrite_file("pkg/live.py", apply=True)
    assert result["status"] == "syntax-error"
    assert "old_engine" in bad.read_text()


def test_mixed_disposition_manifest_rejected(tree):
    root, manifest = tree
    manifest["files"][0]["occurrences"][1]["disposition"] = "preserve-byte"
    with pytest.raises(SystemExit):
        make(manifest, root)


def test_identity_rename_rejected(tree):
    root, manifest = tree
    with pytest.raises(ValueError):
        RenameCodemod(manifest, old="old_engine", new="old_engine", root=root)


def test_real_manifest_loads_and_partitions():
    manifest_path = (
        REPO_ROOT.parent / "docs_tmp/bb_direction_assessment/evidence/R0A/rename_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    codemod = RenameCodemod(
        manifest, old="agentic_coder_prototype", new="breadboard_engine", root=REPO_ROOT
    )
    assert len(codemod.approved) + len(codemod.denied) == len(manifest["files"])
    approved_occ = sum(
        occ["count"] for e in codemod.approved.values() for occ in e["occurrences"]
    )
    assert approved_occ == manifest["totals_by_disposition"]["live-rewrite"]
    assert manifest["unclassified_count"] == 0
    with pytest.raises(RefusedRewrite):
        codemod.rewrite_file(next(iter(codemod.denied)), apply=True)
