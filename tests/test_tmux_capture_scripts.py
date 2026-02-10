from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


capture_poll = _load_module(REPO_ROOT / "scripts" / "tmux_capture_poll.py", "tmux_capture_poll_test_mod")
capture_scenario = _load_module(
    REPO_ROOT / "scripts" / "run_tmux_capture_scenario.py",
    "run_tmux_capture_scenario_test_mod",
)
capture_validate = _load_module(
    REPO_ROOT / "scripts" / "validate_tmux_capture_run.py",
    "validate_tmux_capture_run_test_mod",
)
capture_cleanup = _load_module(
    REPO_ROOT / "scripts" / "cleanup_breadboard_test_tmux_sessions.py",
    "cleanup_breadboard_test_tmux_sessions_test_mod",
)


def test_capture_poll_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tmux_capture_poll.py", "--target", "breadboard_test_poll:0.0"])
    config = capture_poll.parse_args()
    assert config.capture_mode == "pane"
    assert config.interval == 0.5
    assert config.duration == 60.0
    assert config.render_png is True
    assert config.scenario == "capture"
    assert config.run_id == ""


def test_capture_poll_scrollback_alias_overrides_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tmux_capture_poll.py",
            "--target",
            "breadboard_test_poll:0.0",
            "--capture-mode",
            "pane",
            "--scrollback",
        ],
    )
    config = capture_poll.parse_args()
    assert config.capture_mode == "scrollback"


def test_capture_raw_uses_visible_pane_without_scrollback(monkeypatch):
    seen: list[list[str]] = []

    def fake_run_tmux(cmd: list[str]) -> str:
        seen.append(cmd)
        if "-ep" in cmd:
            return "ansi"
        return "txt"

    monkeypatch.setattr(capture_poll, "run_tmux", fake_run_tmux)
    ansi, txt = capture_poll.capture_raw("breadboard_test_poll:0.0", "pane")
    assert ansi == "ansi"
    assert txt == "txt"
    assert seen == [
        ["tmux", "capture-pane", "-ep", "-t", "breadboard_test_poll:0.0"],
        ["tmux", "capture-pane", "-p", "-t", "breadboard_test_poll:0.0"],
    ]


def test_infer_target_key_defaults_for_claude():
    submit_key, newline_key, newline_prefix = capture_scenario.infer_target_key_defaults(
        "breadboard_test_claude:0.0"
    )
    assert submit_key == "C-m"
    assert newline_key == "Enter"
    assert newline_prefix == "\\"


def test_normalize_tmux_key_maps_enter_aliases():
    assert capture_scenario.normalize_tmux_key("Enter", fallback="C-m", for_submit=True) == "C-m"
    assert capture_scenario.normalize_tmux_key("return", fallback="C-m", for_submit=True) == "C-m"
    assert capture_scenario.normalize_tmux_key("Enter", fallback="C-j", for_submit=False) == "Enter"
    assert capture_scenario.normalize_tmux_key("ctrl+j", fallback="Enter", for_submit=False) == "C-j"
    assert capture_scenario.normalize_tmux_key("", fallback="C-m", for_submit=True) == "C-m"


def test_scenario_parse_args_target_aware_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_tmux_capture_scenario.py",
            "--target",
            "breadboard_test_claude:0.0",
            "--scenario",
            "claude/startup",
            "--out-root",
            str(tmp_path),
        ],
    )
    config = capture_scenario.parse_args()
    assert config.submit_key == "C-m"
    assert config.newline_key == "Enter"
    assert config.newline_prefix == "\\"
    assert config.wait_idle_accept_active_timeout is False
    assert config.wait_idle_accept_stable_active_after == 0.0
    assert config.session_prefix_guard == "breadboard_test_"
    assert config.protected_sessions == ("bb_tui_codex_dev", "bb_engine_codex_dev", "bb_atp")


def test_scenario_parse_args_semantic_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_tmux_capture_scenario.py",
            "--target",
            "breadboard_test_codex:0.0",
            "--scenario",
            "codex/demo",
            "--out-root",
            str(tmp_path),
            "--must-contain",
            "compact-ready",
            "--must-not-contain",
            "Conversation interrupted",
            "--must-match-regex",
            "^OK$",
            "--semantic-timeout",
            "8",
            "--semantic-fail-fast",
            "--require-final-idle",
            "--capture-label",
            "smoke",
            "--max-stall-seconds",
            "12",
            "--dry-run-actions",
        ],
    )
    config = capture_scenario.parse_args()
    assert config.must_contain == ("compact-ready",)
    assert config.must_not_contain == ("Conversation interrupted",)
    assert config.must_match_regex == ("^OK$",)
    assert config.semantic_timeout == 8.0
    assert config.semantic_fail_fast is True
    assert config.require_final_idle is True
    assert config.capture_label == "smoke"
    assert config.max_stall_seconds == 12.0
    assert config.dry_run_actions is True


def test_execute_actions_send_newline_uses_prefix_and_key(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup",
        actions_path=None,
        duration=1.0,
        interval=0.5,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )

    sent_text: list[str] = []
    sent_keys: list[str] = []

    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda _target, text: sent_text.append(text))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [
            {"type": "send", "text": "line 1", "newline": True},
            {"type": "submit"},
        ],
    )
    assert sent_text == ["line 1", "\\"]
    assert sent_keys == ["Enter", "C-m"]


def test_execute_actions_send_input_mode_submit_enter_avoids_newline(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup",
        actions_path=None,
        duration=1.0,
        interval=0.5,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )
    sent_text: list[str] = []
    sent_keys: list[str] = []
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda _target, text: sent_text.append(text))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [{"type": "send", "text": "one line", "input_mode": "submit_enter"}],
    )
    assert sent_text == ["one line"]
    assert sent_keys == ["C-m"]


def test_execute_actions_send_mode_submit_alias(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup",
        actions_path=None,
        duration=1.0,
        interval=0.5,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )
    sent_text: list[str] = []
    sent_keys: list[str] = []
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda _target, text: sent_text.append(text))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [{"type": "send", "text": "one line", "send_mode": "submit"}],
    )
    assert sent_text == ["one line"]
    assert sent_keys == ["C-m"]


def test_execute_actions_rejects_literal_newlines_without_flag(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup",
        actions_path=None,
        duration=1.0,
        interval=0.5,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda _target, text: None)
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: None)

    with pytest.raises(ValueError, match="contains literal newlines"):
        capture_scenario.execute_actions(
            config,
            [{"type": "send", "text": "line1\nline2", "send_mode": "submit"}],
        )


def test_execute_actions_literal_newline_action(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup",
        actions_path=None,
        duration=1.0,
        interval=0.5,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )
    sent_text: list[str] = []
    sent_keys: list[str] = []
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda _target, text: sent_text.append(text))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [{"type": "literal_newline"}],
    )
    assert sent_text == ["\\"]
    assert sent_keys == ["Enter"]


def test_wait_for_idle_accept_active_timeout(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_codex:0.0",
        scenario="codex/timeout_tolerant",
        actions_path=None,
        duration=1.0,
        interval=0.5,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="C-j",
        newline_prefix="",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=True,
        wait_idle_accept_stable_active_after=0.0,
    )

    # Always-ready + always-active pane should timeout unless tolerant mode is enabled.
    monkeypatch.setattr(
        capture_scenario,
        "tmux_capture_text",
        lambda _target: 'Try "refactor"\nWorking (4m 00s)',
    )

    capture_scenario.execute_actions(
        config,
        [
            {
                "type": "wait_for_idle",
                "timeout": 0.15,
                "interval": 0.05,
                "quiet_seconds": 0.1,
            }
        ],
    )


def test_wait_for_idle_stall_guard_raises(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_codex:0.0",
        scenario="codex/stall_guard",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="C-j",
        newline_prefix="",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
        max_stall_seconds=0.1,
    )
    monkeypatch.setattr(
        capture_scenario,
        "tmux_capture_text",
        lambda _target: 'Try "refactor"\nWorking (4m 00s)',
    )

    with pytest.raises(TimeoutError):
        capture_scenario.execute_actions(
            config,
            [
                {
                    "type": "wait_for_idle",
                    "timeout": 0.4,
                    "interval": 0.05,
                    "quiet_seconds": 0.1,
                    "max_stall_seconds": 0.1,
                }
            ],
        )


def test_startup_preflight_handles_claude_theme_picker(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup_preflight",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )

    panes = iter(
        [
            "Choose the text style that looks best with your terminal\nTo change this later, run /theme",
            'Try "refactor <filepath>"\n? for shortcuts',
        ]
    )
    sent_keys: list[str] = []
    monkeypatch.setattr(capture_scenario, "tmux_capture_text", lambda _target: next(panes))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [{"type": "startup_preflight", "timeout": 2, "interval": 0.01}],
    )
    assert sent_keys[:2] == ["1", "C-m"]


def test_startup_preflight_handles_claude_login_picker(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup_preflight",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )
    panes = iter(
        [
            "Select login method:\n1. Claude account with subscription\n2. Anthropic Console account",
            'Try "refactor <filepath>"\n? for shortcuts',
        ]
    )
    sent_keys: list[str] = []
    monkeypatch.setattr(capture_scenario, "tmux_capture_text", lambda _target: next(panes))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [{"type": "startup_preflight", "timeout": 2, "interval": 0.01, "login_choice": "2"}],
    )
    assert sent_keys[:2] == ["2", "C-m"]


def test_startup_preflight_recovers_from_claude_model_issue(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup_preflight",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )

    panes = [
        "There's an issue with the selected model (haiku-4-5). It may not exist. Run /model to pick a different model.",
        "Select model\nEnter to confirm\n4. Haiku",
        'Try "refactor <filepath>"\n? for shortcuts',
    ]

    def fake_capture(_target: str) -> str:
        if panes:
            return panes.pop(0)
        return 'Try "refactor <filepath>"\n? for shortcuts'

    sent_text: list[str] = []
    sent_keys: list[str] = []
    monkeypatch.setattr(capture_scenario, "tmux_capture_text", fake_capture)
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda _target, text: sent_text.append(text))
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda _target, key: sent_keys.append(key))

    capture_scenario.execute_actions(
        config,
        [{"type": "startup_preflight", "timeout": 2, "interval": 0.01}],
    )

    assert sent_text == ["/model"]
    assert sent_keys[:3] == ["C-m", "4", "C-m"]


def test_startup_preflight_fails_fast_on_claude_oauth_prompt(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_claude:0.0",
        scenario="claude/startup_preflight",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="Enter",
        newline_prefix="\\",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
    )
    monkeypatch.setattr(
        capture_scenario,
        "tmux_capture_text",
        lambda _target: "https://platform.claude.com/oauth/authorize?x=1\nPaste code here if prompted >",
    )
    with pytest.raises(RuntimeError, match="manual Claude OAuth sign-in"):
        capture_scenario.execute_actions(
            config,
            [{"type": "startup_preflight", "timeout": 2, "interval": 0.01}],
        )


def test_semantic_action_assertion_failure_collects(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_codex:0.0",
        scenario="codex/semantic",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="C-j",
        newline_prefix="",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
        semantic_timeout=0.05,
    )
    semantic_failures: list[dict[str, object]] = []
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture_scenario, "tmux_capture_text", lambda _target: "no-match-here")

    capture_scenario.execute_actions(
        config,
        [{"type": "send", "text": "hello", "must_contain": ["compact-ready"]}],
        semantic_failures=semantic_failures,
    )
    assert len(semantic_failures) == 1
    assert semantic_failures[0]["scope"] == "action"


def test_semantic_action_assertion_must_contain_any(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_codex:0.0",
        scenario="codex/semantic_any",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="C-j",
        newline_prefix="",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
        semantic_timeout=0.05,
    )
    semantic_failures: list[dict[str, object]] = []
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture_scenario, "tmux_capture_text", lambda _target: "table only | row")

    capture_scenario.execute_actions(
        config,
        [{"type": "send", "text": "hello", "must_contain_any": ["#", "```", "|"]}],
        semantic_failures=semantic_failures,
    )
    assert semantic_failures == []


def test_semantic_action_assertion_fail_fast_raises(monkeypatch, tmp_path):
    config = capture_scenario.ScenarioConfig(
        target="breadboard_test_codex:0.0",
        scenario="codex/semantic",
        actions_path=None,
        duration=1.0,
        interval=0.05,
        out_root=tmp_path,
        submit_key="C-m",
        newline_key="C-j",
        newline_prefix="",
        submit_delay_ms=0,
        settle_ms=0,
        settle_attempts=1,
        pre_sleep=0.0,
        post_sleep=0.0,
        provider_dump_dir=None,
        provider_fail_on_http_error=False,
        provider_fail_on_model_not_found=False,
        provider_fail_on_permission_error=False,
        provider_forbid_model_aliases=(),
        no_png=True,
        clear_before_send=False,
        wait_idle_accept_active_timeout=False,
        wait_idle_accept_stable_active_after=0.0,
        semantic_timeout=0.05,
        semantic_fail_fast=True,
    )
    monkeypatch.setattr(capture_scenario, "tmux_send_text", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture_scenario, "tmux_send_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture_scenario, "tmux_capture_text", lambda _target: "no-match-here")

    with pytest.raises(AssertionError):
        capture_scenario.execute_actions(
            config,
            [{"type": "send", "text": "hello", "must_contain": ["compact-ready"]}],
        )


def test_protected_target_guard_rejects_reserved_sessions():
    with pytest.raises(ValueError):
        capture_scenario.ensure_target_allowed(
            "bb_tui_codex_dev:0.0",
            "breadboard_test_",
            ("bb_tui_codex_dev", "bb_engine_codex_dev", "bb_atp"),
        )


def test_validate_frame_bundle_detects_missing_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    frames = run_dir / "frames"
    frames.mkdir(parents=True)
    (frames / "frame_0001.txt").write_text("a", encoding="utf-8")
    (frames / "frame_0001.ansi").write_text("a", encoding="utf-8")
    (frames / "frame_0002.txt").write_text("b", encoding="utf-8")
    result = capture_scenario.validate_frame_bundle(run_dir, interval_seconds=0.5)
    assert result["frame_count"] == 2
    assert result["missing_artifacts"]


def test_exit_code_contract():
    assert capture_scenario.exit_code_for_scenario_result("pass") == 0
    assert capture_scenario.exit_code_for_scenario_result("operational_pass_semantic_fail") == 2
    assert capture_scenario.exit_code_for_scenario_result("fail") == 3
    assert capture_scenario.exit_code_for_scenario_result("unknown") == 3


def test_analyze_provider_new_files_classifies_scope_errors(tmp_path):
    provider_dir = tmp_path / "provider"
    provider_dir.mkdir(parents=True, exist_ok=True)
    rel = "abc_response.json"
    (provider_dir / rel).write_text(
        json.dumps(
            {
                "status_code": 403,
                "error": {
                    "message": "missing scopes: api.responses.write and api.model.read",
                    "type": "insufficient_permissions",
                },
            }
        ),
        encoding="utf-8",
    )
    analysis = capture_scenario.analyze_provider_new_files(
        provider_dir,
        [rel],
        (),
    )
    assert analysis["http_error_files"] == [{"file": rel, "status": 403}]
    assert analysis["permission_error_files"] == [rel]
    assert analysis["scope_error_files"] == [
        {"file": rel, "status": 403, "scopes": ["api.model.read", "api.responses.write"]}
    ]


def test_analyze_provider_new_files_detects_forbidden_alias_in_request(tmp_path):
    provider_dir = tmp_path / "provider"
    provider_dir.mkdir(parents=True, exist_ok=True)
    rel = "abc_request.json"
    (provider_dir / rel).write_text(
        json.dumps({"model": "gpt-5-codex"}),
        encoding="utf-8",
    )
    analysis = capture_scenario.analyze_provider_new_files(
        provider_dir,
        [rel],
        ("gpt-5-codex",),
    )
    assert analysis["forbidden_model_alias_hits"] == [
        {"file": rel, "alias": "gpt-5-codex", "model": "gpt-5-codex"}
    ]


def test_summarize_failure_script(tmp_path):
    manifest = tmp_path / "scenario_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "scenario": "demo",
                "run_id": "r1",
                "target": "breadboard_test_demo:0.0",
                "scenario_result": "fail",
                "poller_exit_code": 0,
                "action_error": "semantic assertion failed",
                "semantic_failures": [{"scope": "global", "failures": ["missing required token: 'ok'"]}],
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "summarize_tmux_scenario_failure.py"),
            "--manifest",
            str(manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["scenario_result"] == "fail"
    assert payload["semantic_failures_count"] == 1


def test_cleanup_script_respects_prefix_and_protected(monkeypatch, capsys):
    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, returncode: int, stdout: str = ""):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(cmd, capture_output=False, text=False, check=False):
        calls.append(list(cmd))
        if cmd[:3] == ["tmux", "list-sessions", "-F"]:
            # session_created is epoch seconds.
            now = 2_000_000.0
            old = int(now - 400 * 60)
            fresh = int(now - 10 * 60)
            output = "\n".join(
                [
                    f"breadboard_test_old\t{old}",
                    f"breadboard_test_new\t{fresh}",
                    f"bb_tui_codex_dev\t{old}",
                    f"other_session\t{old}",
                ]
            )
            return FakeProc(0, output)
        if cmd[:2] == ["tmux", "kill-session"]:
            return FakeProc(0, "")
        return FakeProc(0, "")

    monkeypatch.setattr(capture_cleanup.subprocess, "run", fake_run)
    monkeypatch.setattr(capture_cleanup.time, "time", lambda: 2_000_000.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_breadboard_test_tmux_sessions.py",
            "--older-than-minutes",
            "180",
        ],
    )
    capture_cleanup.main()
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["session"] == "breadboard_test_old"
    kill_calls = [cmd for cmd in calls if cmd[:2] == ["tmux", "kill-session"]]
    assert len(kill_calls) == 1
    assert kill_calls[0][-1] == "breadboard_test_old"
