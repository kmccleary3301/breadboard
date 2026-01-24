from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentic_coder_prototype.ctrees.store import CTreeStore
from agentic_coder_prototype.ctrees.tree_view import build_ctree_tree_view


def _write_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    ctrees_root = bundle / "ctrees"
    ctrees_root.mkdir(parents=True, exist_ok=True)

    store = CTreeStore()
    store.record("message", {"role": "user", "content": "hello"}, turn=1)
    store.record("message", {"role": "assistant", "content": "hi"}, turn=1)
    store.persist(str(ctrees_root))

    view = build_ctree_tree_view(
        store,
        stage="FROZEN",
        compiler_config={},
        collapse_target=None,
        collapse_mode="all_but_last",
        include_previews=False,
    )
    tree_dir = ctrees_root / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    (tree_dir / "frozen.json").write_text(json.dumps(view, indent=2, sort_keys=True), encoding="utf-8")

    hashes = {
        "node_hash": (store.hashes() or {}).get("node_hash"),
        "tree_sha256": (view.get("hashes") or {}).get("tree_sha256"),
        "z1": (view.get("hashes") or {}).get("z1"),
        "z2": (view.get("hashes") or {}).get("z2"),
        "z3": (view.get("hashes") or {}).get("z3"),
        "selection_sha256": (view.get("selection") or {}).get("selection_sha256"),
    }
    (ctrees_root / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")

    manifest = {
        "stage": "FROZEN",
        "include_previews": False,
        "collapse_target": None,
        "collapse_mode": "all_but_last",
        "compiler_config": {},
    }
    (bundle / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return bundle


def test_ctlab_validate_smoke(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "ctlab.py"
    result = subprocess.run([sys.executable, str(script), "validate", str(bundle)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
