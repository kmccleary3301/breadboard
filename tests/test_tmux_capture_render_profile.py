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


def test_default_profile_is_latest_locked_phase4_v5():
    module = _load_render_profile_module()
    assert module.DEFAULT_RENDER_PROFILE_ID == module.LATEST_LOCKED_PHASE4_PROFILE_ID
    assert module.resolve_render_profile(None).id == module.LATEST_LOCKED_PHASE4_PROFILE_ID


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
    assert "--font-path" in cmd
    assert "--baseline-offset" in cmd and "17" in cmd


def test_locked_profile_fields_are_frozen():
    module = _load_render_profile_module()
    profile = module.LATEST_LOCKED_PHASE4_PROFILE
    assert profile.id == "phase4_locked_v5"
    assert profile.renderer == "pillow"
    assert profile.bg == "1f2430"
    assert profile.fg == "f1f5f9"
    assert profile.font_size == 14
    assert profile.scale == 1.0
    assert profile.cell_width == 9
    assert profile.cell_height == 22
    assert profile.line_gap == 0
    assert profile.pad_x == 0
    assert profile.pad_y == 1
    assert profile.baseline_offset == 17
    assert profile.underline_offset == 2
    assert profile.font_path.endswith("Consolas-Regular.ttf")


def test_supported_profiles_order_is_frozen():
    module = _load_render_profile_module()
    assert module.SUPPORTED_RENDER_PROFILES == (
        "phase4_locked_v5",
        "phase4_locked_v4",
        "phase4_locked_v3",
        "phase4_locked_v2",
        "phase4_locked_v1",
        "legacy",
    )


def test_locked_profile_lock_verification_passes_for_v5():
    module = _load_render_profile_module()
    profile = module.resolve_render_profile("phase4_locked_v5")
    assert profile is not None
    assert module.validate_render_profile_lock(profile) == []


def test_locked_profile_lock_verification_passes_for_v4():
    module = _load_render_profile_module()
    profile = module.resolve_render_profile("phase4_locked_v4")
    assert profile is not None
    assert module.validate_render_profile_lock(profile) == []


def test_locked_profile_lock_verification_passes_for_v3():
    module = _load_render_profile_module()
    profile = module.resolve_render_profile("phase4_locked_v3")
    assert profile is not None
    assert module.validate_render_profile_lock(profile) == []


def test_locked_profile_lock_verification_passes_for_v2():
    module = _load_render_profile_module()
    profile = module.resolve_render_profile("phase4_locked_v2")
    assert profile is not None
    assert module.validate_render_profile_lock(profile) == []


def test_resolve_profile_font_paths_prefers_meipass_assets(tmp_path: Path, monkeypatch):
    module = _load_render_profile_module()
    profile = module.resolve_render_profile("phase4_locked_v5")
    assert profile is not None

    assets_root = tmp_path / "renderer_assets" / "fonts"
    assets_root.mkdir(parents=True, exist_ok=True)
    for name in (
        "Consolas-Regular.ttf",
        "Consolas-Bold.ttf",
        "Consolas-Italic.ttf",
        "Consolas Bold Italic.ttf",
    ):
        (assets_root / name).write_bytes(b"test-font")

    monkeypatch.setattr(module.sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = module.resolve_profile_font_paths(profile)

    assert resolved["font_path"] == (assets_root / "Consolas-Regular.ttf").resolve()
    assert resolved["font_bold_path"] == (assets_root / "Consolas-Bold.ttf").resolve()
    assert resolved["font_italic_path"] == (assets_root / "Consolas-Italic.ttf").resolve()
    assert resolved["font_bold_italic_path"] == (assets_root / "Consolas Bold Italic.ttf").resolve()
