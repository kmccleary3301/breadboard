from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_render_profile_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "tmux_capture_render_profile.py"
    spec = importlib.util.spec_from_file_location("tmux_capture_render_profile", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_profile_is_locked_phase4_v1():
    module = _load_render_profile_module()
    assert module.DEFAULT_RENDER_PROFILE_ID == module.LOCKED_PHASE4_PROFILE_ID
    assert module.resolve_render_profile(None).id == module.LOCKED_PHASE4_PROFILE_ID


def test_resolve_legacy_profile_returns_none():
    module = _load_render_profile_module()
    assert module.resolve_render_profile("legacy") is None


def test_append_profile_cli_args_is_deterministic():
    module = _load_render_profile_module()
    profile = module.resolve_render_profile(module.DEFAULT_RENDER_PROFILE_ID)
    cmd = module.append_profile_cli_args(["python"], profile)
    assert "--renderer" in cmd
    assert "--bg" in cmd and "1f2430" in cmd
    assert "--fg" in cmd and "f1f5f9" in cmd
