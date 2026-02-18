from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_run_scenario_default_render_profile_is_locked():
    runner = _load_module("run_tmux_capture_scenario", "scripts/run_tmux_capture_scenario.py")
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "run_tmux_capture_scenario.py",
            "--target",
            "breadboard_test_lockcheck:0.0",
            "--scenario",
            "phase4_replay/lockcheck",
        ]
        config = runner.parse_args()
    finally:
        sys.argv = old_argv

    assert config.render_profile == "phase4_locked_v1"
    assert config.fullpane_start_markers == ("BreadBoard v", "No conversation yet")
    assert config.settle_ms == 140
    assert config.settle_attempts == 5
    assert config.session_prefix_guard == "breadboard_test_"
    assert config.protected_sessions == ("bb_tui_codex_dev", "bb_engine_codex_dev", "bb_atp")


def test_phase4_start_script_defaults_are_locked():
    script = (_repo_root() / "scripts" / "start_tmux_phase4_replay_target.sh").read_text(encoding="utf-8")
    assert 'scrollback_mode="scrollback"' in script
    assert 'landing_always="1"' in script


def test_phase4_entrypoint_defaults_are_locked():
    script = (_repo_root() / "scripts" / "phase4_replay_target_entrypoint.sh").read_text(encoding="utf-8")
    assert 'scrollback_mode="scrollback"' in script
    assert 'landing_always="1"' in script


def test_showcase_regression_defaults_are_locked():
    module = _load_module(
        "validate_phase4_showcase_regression",
        "scripts/validate_phase4_showcase_regression.py",
    )
    assert module.DEFAULT_SCENARIO_ID == "phase4_replay/everything_showcase_v1_fullpane_v1"
    assert module.DEFAULT_MIN_FRAMES == 12
    assert "Markdown Showcase" in module.DEFAULT_REQUIRED_ANCHORS
    assert "Inline link BreadBoard" in module.DEFAULT_REQUIRED_ANCHORS
