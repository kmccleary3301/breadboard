from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "curate_phase4_fullpane_visual_pack.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("curate_phase4_fullpane_visual_pack", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pick_workspace_root_prefers_repo_when_docs_and_config_exist(tmp_path: Path):
    module = _load_module()
    repo_root = tmp_path / "repo"
    (repo_root / "docs_tmp").mkdir(parents=True)
    (repo_root / "config").mkdir(parents=True)
    chosen = module._pick_workspace_root(repo_root)
    assert chosen == repo_root


def test_pick_workspace_root_falls_back_to_parent_layout(tmp_path: Path):
    module = _load_module()
    workspace = tmp_path / "workspace"
    repo_root = workspace / "breadboard_repo"
    repo_root.mkdir(parents=True)
    (workspace / "docs_tmp").mkdir(parents=True)
    (workspace / "config").mkdir(parents=True)
    chosen = module._pick_workspace_root(repo_root)
    assert chosen == workspace


def test_resolve_workspace_path_uses_existing_candidate(tmp_path: Path):
    module = _load_module()
    workspace = tmp_path / "workspace"
    repo_root = workspace / "breadboard_repo"
    repo_root.mkdir(parents=True)
    target = workspace / "docs_tmp" / "tui_design" / "everything_showcase_v2.png"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")

    resolved = module._resolve_workspace_path(
        Path("docs_tmp/tui_design/everything_showcase_v2.png"),
        workspace,
        repo_root,
    )
    assert resolved == target.resolve()
