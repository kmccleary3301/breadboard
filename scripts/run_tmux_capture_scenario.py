#!/usr/bin/env python3
"""
Run a scripted tmux interaction scenario while polling pane captures.

This wraps `scripts/tmux_capture_poll.py` and optionally tracks provider dump
file deltas so each scenario run has one consolidated artifact folder.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tmux_capture_time import run_timestamp
from tmux_capture_render_profile import (
    DEFAULT_RENDER_PROFILE_ID,
    SUPPORTED_RENDER_PROFILES,
    resolve_render_profile,
)

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


@dataclass
class ScenarioConfig:
    target: str
    tmux_socket: str
    scenario: str
    actions_path: Path | None
    duration: float
    interval: float
    out_root: Path
    submit_key: str
    newline_key: str
    newline_prefix: str
    submit_delay_ms: int
    settle_ms: int
    settle_attempts: int
    poller_terminate_timeout: float
    pre_sleep: float
    post_sleep: float
    provider_dump_dir: Path | None
    provider_fail_on_http_error: bool
    provider_fail_on_model_not_found: bool
    provider_fail_on_permission_error: bool
    provider_forbid_model_aliases: tuple[str, ...]
    no_png: bool
    capture_mode: str
    tail_lines: int
    final_tail_lines: int
    fullpane_start_markers: tuple[str, ...]
    fullpane_max_lines: int
    fullpane_render_max_rows: int
    render_profile: str
    clear_before_send: bool
    wait_idle_accept_active_timeout: bool
    wait_idle_accept_stable_active_after: float
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    must_match_regex: tuple[str, ...] = ()
    semantic_timeout: float = 5.0
    semantic_fail_fast: bool = False
    require_final_idle: bool = False
    capture_label: str = ""
    session_prefix_guard: str = "breadboard_test_"
    protected_sessions: tuple[str, ...] = (
        "bb_tui_codex_dev",
        "bb_engine_codex_dev",
        "bb_atp",
    )
    max_stall_seconds: float = 0.0
    dry_run_actions: bool = False
    clear_mode: str = "ctrl_u"


def run_tmux(args: list[str]) -> None:
    subprocess.run([*tmux_base_args(), *args], check=True)


_DEFAULT_TMUX_SOCKET = ""


def tmux_base_args(socket_name: str | None = None) -> list[str]:
    socket_name = (_DEFAULT_TMUX_SOCKET if socket_name is None else socket_name) or ""
    socket_name = socket_name.strip()
    if not socket_name:
        return ["tmux"]
    return ["tmux", "-L", socket_name]


def tmux_send_text(target: str, text: str) -> None:
    if text == "":
        return
    run_tmux(["send-keys", "-t", target, "-l", text])


def tmux_send_key(target: str, key: str) -> None:
    run_tmux(["send-keys", "-t", target, key])


def tmux_capture_text(target: str) -> str:
    """
    Capture pane text for semantic assertions.

    Important: many TUIs render on tmux's alternate screen. If we only capture the
    normal screen, semantic checks can fail while the UI is visibly present.
    We capture both when possible and concatenate, so "ready markers" like
    "for shortcuts" can be found reliably across environments.
    """
    def _non_ws_count(value: str) -> int:
        return sum(1 for ch in value if not ch.isspace())

    def _looks_like_ready_ui(value: str) -> bool:
        tokens = ("for shortcuts", "Try \"", "Try '", "No conversation yet", "❯")
        return any(token in value for token in tokens)

    def _capture(args: list[str], *, allow_no_alt_screen: bool = False) -> str:
        result = subprocess.run(
            [*tmux_base_args(), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout
        stderr = (result.stderr or "").lower()
        if allow_no_alt_screen and "no alternate screen" in stderr:
            return ""
        raise subprocess.CalledProcessError(
            result.returncode,
            [*tmux_base_args(), *args],
            output=result.stdout,
            stderr=result.stderr,
        )

    normal = _capture(["capture-pane", "-pt", target])
    alt = _capture(["capture-pane", "-apt", target], allow_no_alt_screen=True)

    selected = normal
    if alt and alt.strip() and (_looks_like_ready_ui(alt) or _non_ws_count(alt) > _non_ws_count(normal)):
        selected = alt
    elif alt and alt.strip() and alt != normal:
        selected = normal + "\n\n[tmux:alternate_screen]\n" + alt

    # Include bounded recent history in semantic reads so short-lived replay output
    # is still detectable even when the visible pane collapses back to a prompt.
    history_normal = _capture(["capture-pane", "-p", "-S", "-160", "-t", target])
    history_alt = _capture(
        ["capture-pane", "-a", "-p", "-S", "-160", "-t", target],
        allow_no_alt_screen=True,
    )
    history_best = history_normal
    if history_alt and history_alt.strip() and (
        _looks_like_ready_ui(history_alt) or _non_ws_count(history_alt) > _non_ws_count(history_normal)
    ):
        history_best = history_alt
    if history_best.strip() and history_best != selected:
        return selected + "\n\n[tmux:scrollback_recent]\n" + history_best
    return selected


def split_tmux_target(target: str) -> tuple[str, str]:
    """
    Split a tmux target like 'session:window.pane' into:
    - window target: 'session:window'
    - pane target:   'session:window.pane'
    """
    raw = target.strip()
    if "." not in raw:
        raise ValueError(f"tmux target is missing pane selector (expected session:window.pane): {target!r}")
    window_target, pane_suffix = raw.rsplit(".", 1)
    if not window_target or not pane_suffix:
        raise ValueError(f"invalid tmux target: {target!r}")
    return window_target, raw


def tmux_display_pane_size(target: str) -> tuple[int, int]:
    result = subprocess.run(
        [*tmux_base_args(), "display-message", "-p", "-t", target, "#{pane_width} #{pane_height}"],
        check=True,
        capture_output=True,
        text=True,
    )
    parts = (result.stdout or "").strip().split()
    if len(parts) != 2:
        raise ValueError(f"unexpected pane size output: {result.stdout!r}")
    return int(parts[0]), int(parts[1])


def tmux_resize_target(target: str, *, cols: int, rows: int, scope: str) -> tuple[int, int]:
    cols = int(cols)
    rows = int(rows)
    if cols <= 0 or rows <= 0:
        raise ValueError(f"resize requires positive cols/rows, got cols={cols} rows={rows}")
    window_target, pane_target = split_tmux_target(target)
    normalized_scope = (scope or "window").strip().lower()
    if normalized_scope in {"window", "win"}:
        run_tmux(["resize-window", "-t", window_target, "-x", str(cols), "-y", str(rows)])
    elif normalized_scope in {"pane"}:
        run_tmux(["resize-pane", "-t", pane_target, "-x", str(cols), "-y", str(rows)])
    else:
        raise ValueError(f"resize scope must be 'window' or 'pane', got: {scope!r}")
    return tmux_display_pane_size(pane_target)


def normalize_captured_text(text: str) -> str:
    if not text:
        return ""
    normalized = ANSI_ESCAPE_RE.sub("", text)
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def default_clear_mode_for_target(target: str) -> str:
    lower = target.lower()
    if "codex" in lower:
        # Codex commonly shows a prefilled composer *suggestion*. In current builds, typing
        # replaces the suggestion directly, so "clear" key sequences are both unnecessary
        # and occasionally harmful (they can change focus/mode in ways that break automation).
        #
        # We default to no-op clearing for Codex; scenarios can opt into codex_auto explicitly
        # if they have a specific reason.
        return "none"
    return "ctrl_u"


def extract_codex_composer_text(pane_text: str) -> str | None:
    """
    Best-effort: extract the current Codex composer line text (after the '›' prompt glyph).
    This is used only for automation heuristics (never as a semantic correctness signal).
    """
    for raw_line in pane_text.splitlines():
        if "›" not in raw_line:
            continue
        _, after = raw_line.split("›", 1)
        return after.strip()
    return None


def codex_composer_looks_empty_or_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    v = value.strip()
    if v == "":
        return True
    lowered = v.lower()
    if lowered.startswith("write tests for"):
        return True
    if lowered.startswith("try \"") or lowered.startswith("try '"):
        return True
    if lowered in {"(no obvious next step)", "no obvious next step"}:
        return True
    return False


def clear_composer(target: str, mode: str) -> None:
    normalized = (mode or "").strip().lower()
    if normalized in {"", "none"}:
        return
    if normalized == "codex_auto":
        def current_value() -> str | None:
            captured = normalize_captured_text(tmux_capture_text(target))
            return extract_codex_composer_text(captured)

        tmux_send_key(target, "Escape")
        time.sleep(0.05)

        tmux_send_key(target, "C-u")
        time.sleep(0.08)
        if codex_composer_looks_empty_or_placeholder(current_value()):
            return

        # If Ctrl+U didn't take effect, the focus may not be in the composer.
        tmux_send_key(target, "Escape")
        time.sleep(0.05)
        tmux_send_key(target, "C-u")
        time.sleep(0.08)
        if codex_composer_looks_empty_or_placeholder(current_value()):
            return

        # Try common line-editing sequences (may be no-ops in some builds).
        tmux_send_key(target, "C-a")
        tmux_send_key(target, "C-k")
        time.sleep(0.08)
        if codex_composer_looks_empty_or_placeholder(current_value()):
            return

        # Last resort: interrupt any active mode and leave the composer "as-is".
        tmux_send_key(target, "C-c")
        time.sleep(0.08)
        return
    if normalized == "ctrl_u":
        tmux_send_key(target, "C-u")
        return
    if normalized in {"ctrl_a_ctrl_k", "ctrl-a-ctrl-k"}:
        tmux_send_key(target, "C-a")
        tmux_send_key(target, "C-k")
        return
    if normalized == "escape":
        tmux_send_key(target, "Escape")
        return
    raise ValueError(f"unsupported clear mode: {mode!r}")


def parse_token_list(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        token = raw.strip()
        return (token,) if token else ()
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            token = str(item).strip()
            if token:
                out.append(token)
        return tuple(out)
    raise ValueError("token list values must be string or array of strings")


def parse_csv_tokens(raw: str) -> tuple[str, ...]:
    return tuple(token.strip() for token in str(raw).split(",") if token.strip())


def get_target_session_name(target: str) -> str:
    return target.split(":", 1)[0].strip()


def ensure_target_allowed(target: str, session_prefix_guard: str, protected_sessions: tuple[str, ...]) -> None:
    session_name = get_target_session_name(target)
    if session_name in set(protected_sessions):
        raise ValueError(f"target '{target}' is protected and cannot be used for capture automation")
    if session_prefix_guard and not session_name.startswith(session_prefix_guard):
        raise ValueError(
            f"target '{target}' session '{session_name}' does not match required prefix '{session_prefix_guard}'"
        )


def ensure_target_exists(target: str) -> None:
    result = subprocess.run(
        [*tmux_base_args(), "list-panes", "-t", target],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"tmux target '{target}' does not exist or is not addressable")


def evaluate_semantic_assertions(
    text: str,
    *,
    must_contain: tuple[str, ...],
    must_not_contain: tuple[str, ...],
    must_match_regex: tuple[str, ...],
    must_contain_any: tuple[str, ...] = (),
    must_match_regex_any: tuple[str, ...] = (),
) -> list[str]:
    failures: list[str] = []
    for token in must_contain:
        if token not in text:
            failures.append(f"missing required token: {token!r}")
    if must_contain_any and not any(token in text for token in must_contain_any):
        failures.append(f"missing any-of required tokens: {list(must_contain_any)!r}")
    for token in must_not_contain:
        if token in text:
            failures.append(f"forbidden token present: {token!r}")
    for pattern in must_match_regex:
        try:
            if re.search(pattern, text, re.MULTILINE) is None:
                failures.append(f"missing regex match: {pattern!r}")
        except re.error as exc:
            failures.append(f"invalid regex {pattern!r}: {exc}")
    if must_match_regex_any:
        valid_patterns: list[str] = []
        any_match = False
        for pattern in must_match_regex_any:
            try:
                valid_patterns.append(pattern)
                if re.search(pattern, text, re.MULTILINE) is not None:
                    any_match = True
            except re.error as exc:
                failures.append(f"invalid regex {pattern!r}: {exc}")
        if valid_patterns and not any_match:
            failures.append(f"missing any-of regex matches: {valid_patterns!r}")
    return failures


def wait_for_semantic_assertions(
    target: str,
    *,
    must_contain: tuple[str, ...],
    must_not_contain: tuple[str, ...],
    must_match_regex: tuple[str, ...],
    must_contain_any: tuple[str, ...] = (),
    must_match_regex_any: tuple[str, ...] = (),
    timeout: float,
    interval: float = 0.2,
) -> tuple[bool, str, list[str]]:
    deadline = time.time() + max(0.0, timeout)
    last_text = ""
    while True:
        last_text = normalize_captured_text(tmux_capture_text(target))
        failures = evaluate_semantic_assertions(
            last_text,
            must_contain=must_contain,
            must_not_contain=must_not_contain,
            must_match_regex=must_match_regex,
            must_contain_any=must_contain_any,
            must_match_regex_any=must_match_regex_any,
        )
        if not failures:
            return True, last_text, []
        if time.time() >= deadline:
            return False, last_text, failures
        time.sleep(max(0.05, interval))


def collect_action_semantics(
    action: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        parse_token_list(action.get("must_contain")),
        parse_token_list(action.get("must_not_contain")),
        parse_token_list(action.get("must_match_regex")),
        parse_token_list(action.get("must_contain_any")),
        parse_token_list(action.get("must_match_regex_any")),
    )


def wait_for_idle_check(
    target: str,
    *,
    timeout: float,
    interval: float,
    quiet_seconds: float,
    ready_tokens: list[str],
    active_tokens: list[str],
    fail_tokens: list[str],
    accept_active_timeout: bool,
    accept_stable_active_after: float,
    max_stall_seconds: float,
) -> None:
    deadline = time.time() + timeout
    idle_since: float | None = None
    active_since: float | None = None
    last_text = ""
    last_change_at = time.time()

    while True:
        now = time.time()
        current_text = normalize_captured_text(tmux_capture_text(target))
        if current_text != last_text:
            last_text = current_text
            last_change_at = now

        fail_hit = next((token for token in fail_tokens if token in last_text), None)
        if fail_hit is not None:
            tail = "\n".join(last_text.splitlines()[-30:])
            raise RuntimeError(f"wait_for_idle failed on token '{fail_hit}'. Pane tail:\n{tail}")

        has_ready = any(token in last_text for token in ready_tokens) if ready_tokens else True
        has_active = any(token in last_text for token in active_tokens) if active_tokens else False
        if has_ready and not has_active:
            if idle_since is None:
                idle_since = now
            elif (now - idle_since) >= quiet_seconds:
                return
            active_since = None
        else:
            idle_since = None
            if has_ready and has_active:
                if active_since is None:
                    active_since = now
                if max_stall_seconds > 0 and (now - last_change_at) >= max_stall_seconds:
                    tail = "\n".join(last_text.splitlines()[-30:])
                    raise TimeoutError(
                        f"wait_for_idle stalled while active for {(now - last_change_at):.1f}s "
                        f"(max_stall_seconds={max_stall_seconds}). Pane tail:\n{tail}"
                    )
                if accept_stable_active_after > 0:
                    unchanged_for = now - last_change_at
                    active_for = now - active_since
                    if unchanged_for >= accept_stable_active_after and active_for >= accept_stable_active_after:
                        return
            else:
                active_since = None

        if now >= deadline:
            if accept_active_timeout and has_ready:
                return
            tail = "\n".join(last_text.splitlines()[-30:])
            raise TimeoutError(
                f"wait_for_idle timeout after {timeout}s "
                f"(quiet_seconds={quiet_seconds}, ready_tokens={ready_tokens}, active_tokens={active_tokens}, "
                f"accept_active_timeout={accept_active_timeout}, "
                f"accept_stable_active_after={accept_stable_active_after}). "
                f"Pane tail:\n{tail}"
            )
        time.sleep(interval)


def default_ready_tokens(target: str) -> list[str]:
    lower = target.lower()
    if "claude" in lower:
        return [
            'Try "',
            "for shortcuts",
            "bypass permissions on",
            "Reply exactly with:",
        ]
    if "codex" in lower:
        return [
            "Try \"",
            "for shortcuts",
            "Reply exactly with:",
            "Continue after compaction",
        ]
    return ['Try "', "for shortcuts"]


def default_active_tokens(target: str) -> list[str]:
    lower = target.lower()
    if "claude" in lower:
        return [
            "Sautéing",
            "Spelunking",
            "Deciphering",
            "(esc to interrupt",
            "run in background",
            "Press up to edit queued messages",
        ]
    if "codex" in lower:
        return [
            "Working",
            "thinking",
            "Running",
            "Press up to edit queued messages",
        ]
    return ["Working", "thinking", "run in background"]


def default_fail_tokens(target: str) -> list[str]:
    lower = target.lower()
    if "claude" in lower:
        return [
            "warning: ANTHROPIC_API_KEY is not set",
            "not signed in",
            "log in",
        ]
    if "codex" in lower:
        return ["MCP startup incomplete (failed: oracle)"]
    return []


def infer_target_key_defaults(target: str) -> tuple[str, str, str]:
    target_lower = target.lower()
    if "claude" in target_lower:
        # Claude submit should be carriage return; multiline requires explicit
        # escape prefix + newline key so automation matches interactive behavior.
        #
        # In practice, sending "Enter" for newline is ambiguous (it can submit or
        # insert a newline depending on focus). A literal line-feed (Ctrl+J)
        # is much more reliable for newline insertion in automation.
        return ("C-m", "C-j", "\\")
    return ("C-m", "C-j", "")


def normalize_tmux_key(raw_key: str, *, fallback: str, for_submit: bool = False) -> str:
    key = raw_key.strip()
    if not key:
        return fallback
    lowered = key.lower()
    if lowered in {"c-m", "ctrl+m", "ctrl-m"}:
        return "C-m"
    if lowered in {"enter", "return", "ret"}:
        return "C-m" if for_submit else "Enter"
    if lowered in {"c-j", "ctrl+j", "ctrl-j"}:
        return "C-j"
    if lowered in {"escape", "esc"}:
        return "Escape"
    return key


def extract_models(payload: Any) -> list[str]:
    models: list[str] = []
    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "model" and isinstance(value, str):
                    models.append(value)
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return models


def list_files(root: Path | None) -> set[str]:
    if root is None or not root.exists():
        return set()
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
    }


def find_new_run_dir(parent: Path, before: set[str]) -> Path:
    after = {p.name for p in parent.iterdir() if p.is_dir()} if parent.exists() else set()
    created = sorted(after - before)
    if created:
        return parent / created[-1]
    if not after:
        raise RuntimeError(f"poller did not create a run directory under {parent}")
    return parent / sorted(after)[-1]


def parse_args() -> ScenarioConfig:
    parser = argparse.ArgumentParser(description="Run tmux scenario + polling capture.")
    parser.add_argument("--target", required=True, help="tmux target, e.g. session:window.pane")
    parser.add_argument(
        "--tmux-socket",
        default="",
        help="tmux socket name (tmux -L <name>). Use this for isolated capture servers.",
    )
    parser.add_argument("--scenario", required=True, help="scenario id, e.g. claude/multiturn")
    parser.add_argument("--actions", default="", help="path to JSON actions file")
    parser.add_argument("--duration", type=float, default=60.0, help="poll duration seconds")
    parser.add_argument("--interval", type=float, default=0.5, help="poll interval seconds")
    parser.add_argument("--submit-key", default="C-m", help="tmux key for submit action")
    parser.add_argument("--newline-key", default="", help="tmux key for newline action (target-aware default)")
    parser.add_argument(
        "--newline-prefix",
        default="",
        help="literal prefix sent before newline key (Claude default: backslash)",
    )
    parser.add_argument("--submit-delay-ms", type=int, default=80, help="delay before submit after typed text")
    parser.add_argument("--settle-ms", type=int, default=140, help="poll capture settle delay")
    parser.add_argument("--settle-attempts", type=int, default=5, help="poll settle attempts")
    parser.add_argument(
        "--poller-terminate-timeout",
        type=float,
        default=12.0,
        help="seconds to wait for the poller to shutdown gracefully before hard kill",
    )
    parser.add_argument("--pre-sleep", type=float, default=1.0, help="sleep before action execution")
    parser.add_argument("--post-sleep", type=float, default=1.0, help="sleep after action execution")
    parser.add_argument("--provider-dump-dir", default="", help="optional provider dumps dir")
    parser.add_argument(
        "--provider-fail-on-http-error",
        action="store_true",
        help="fail scenario when provider dump response files include HTTP status >= 400",
    )
    parser.add_argument(
        "--provider-fail-on-model-not-found",
        action="store_true",
        help="fail scenario when provider dump response files include model not_found errors",
    )
    parser.add_argument(
        "--provider-fail-on-permission-error",
        action="store_true",
        help="fail scenario when provider dump response files include permission/scope errors",
    )
    parser.add_argument(
        "--provider-forbid-model-aliases",
        default="",
        help="comma-separated model aliases that must not appear in provider request payloads",
    )
    parser.add_argument("--out-root", default="", help="capture root (default: ../docs_tmp/tmux_captures/scenarios)")
    parser.add_argument("--no-png", action="store_true", help="disable PNG output")
    parser.add_argument(
        "--capture-mode",
        default="pane",
        choices=("pane", "pane-auto", "tail", "scrollback", "fullpane"),
        help=(
            "frame capture mode passed to tmux_capture_poll "
            "(pane=full visible pane top->bottom, tail=sliding tail window, "
            "fullpane=landing->bottom stitched buffer window)"
        ),
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=120,
        help="line count for tail captures (capture-mode=tail and pane-auto fallback)",
    )
    parser.add_argument(
        "--final-tail-lines",
        type=int,
        default=0,
        help="optional final tailed snapshot line count (0 disables)",
    )
    parser.add_argument(
        "--fullpane-start-markers",
        default="BreadBoard v,No conversation yet",
        help="comma-separated start markers for fullpane capture mode",
    )
    parser.add_argument(
        "--fullpane-max-lines",
        type=int,
        default=0,
        help="optional max lines for fullpane mode (0 = unlimited)",
    )
    parser.add_argument(
        "--fullpane-render-max-rows",
        type=int,
        default=0,
        help="optional max PNG rows for fullpane mode (0 = unlimited)",
    )
    parser.add_argument(
        "--render-profile",
        default=DEFAULT_RENDER_PROFILE_ID,
        choices=SUPPORTED_RENDER_PROFILES,
        help=f"ANSI->PNG render profile lock (default: {DEFAULT_RENDER_PROFILE_ID})",
    )
    parser.add_argument(
        "--must-contain",
        action="append",
        default=[],
        help="global semantic requirement; can be provided multiple times",
    )
    parser.add_argument(
        "--must-not-contain",
        action="append",
        default=[],
        help="global semantic prohibition; can be provided multiple times",
    )
    parser.add_argument(
        "--must-match-regex",
        action="append",
        default=[],
        help="global semantic regex requirement; can be provided multiple times",
    )
    parser.add_argument(
        "--semantic-timeout",
        type=float,
        default=5.0,
        help="seconds to wait for semantic assertions to pass",
    )
    parser.add_argument(
        "--semantic-fail-fast",
        action="store_true",
        help="fail immediately when semantic assertions do not pass",
    )
    parser.add_argument(
        "--require-final-idle",
        action="store_true",
        help="require a final idle condition check after all actions and semantic checks",
    )
    parser.add_argument("--capture-label", default="", help="optional label saved into run manifest/summary")
    parser.add_argument(
        "--session-prefix-guard",
        default="breadboard_test_",
        help="required tmux session prefix for target safety guard (empty disables guard)",
    )
    parser.add_argument(
        "--protected-sessions",
        default="bb_tui_codex_dev,bb_engine_codex_dev,bb_atp",
        help="comma-separated session names that are never allowed as targets",
    )
    parser.add_argument(
        "--max-stall-seconds",
        type=float,
        default=0.0,
        help="fail wait_for_idle when pane content is unchanged while active beyond this duration",
    )
    parser.add_argument(
        "--wait-idle-accept-active-timeout",
        action="store_true",
        help="for wait_for_idle actions, allow timeout pass when ready tokens are present",
    )
    parser.add_argument(
        "--wait-idle-accept-stable-active-after",
        type=float,
        default=0.0,
        help="for wait_for_idle actions, allow pass when pane is unchanged while active for this many seconds",
    )
    parser.add_argument(
        "--clear-before-send",
        action="store_true",
        help="clear prefilled composer text before each send action (mode controlled by --clear-mode)",
    )
    parser.add_argument(
        "--clear-mode",
        default="auto",
        choices=("auto", "codex_auto", "ctrl_u", "ctrl_a_ctrl_k", "none", "escape"),
        help="composer clear strategy used when clear-before-send or clear_first is enabled",
    )
    parser.add_argument(
        "--dry-run-actions",
        action="store_true",
        help="print normalized action plan and exit without sending keys",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    default_out = repo_root.parent / "docs_tmp" / "tmux_captures" / "scenarios"
    out_root = Path(args.out_root).expanduser().resolve() if args.out_root else default_out

    actions_path = Path(args.actions).expanduser().resolve() if args.actions else None
    provider_dump_dir = Path(args.provider_dump_dir).expanduser().resolve() if args.provider_dump_dir else None
    scenario_lower = args.scenario.strip("/").lower()
    default_forbidden_aliases = ("haiku-4-5",) if "claude" in scenario_lower else ()
    explicit_aliases = tuple(
        token.strip()
        for token in str(args.provider_forbid_model_aliases).split(",")
        if token.strip()
    )
    must_contain = tuple(token.strip() for token in args.must_contain if str(token).strip())
    must_not_contain = tuple(token.strip() for token in args.must_not_contain if str(token).strip())
    must_match_regex = tuple(token.strip() for token in args.must_match_regex if str(token).strip())
    protected_sessions = parse_csv_tokens(args.protected_sessions)
    fullpane_start_markers = parse_csv_tokens(args.fullpane_start_markers)
    resolved_profile = resolve_render_profile(args.render_profile)
    render_profile = resolved_profile.id if resolved_profile is not None else args.render_profile

    default_submit_key, default_newline_key, default_newline_prefix = infer_target_key_defaults(args.target)
    submit_key = normalize_tmux_key(
        str(args.submit_key or default_submit_key),
        fallback=default_submit_key,
        for_submit=True,
    )
    newline_key = normalize_tmux_key(
        str(args.newline_key or default_newline_key),
        fallback=default_newline_key,
        for_submit=False,
    )
    newline_prefix = (
        args.newline_prefix
        if args.newline_prefix != ""
        else default_newline_prefix
    )
    clear_mode = (
        default_clear_mode_for_target(args.target)
        if str(args.clear_mode).strip().lower() == "auto"
        else str(args.clear_mode).strip().lower()
    )

    return ScenarioConfig(
        target=args.target,
        tmux_socket=str(args.tmux_socket or "").strip(),
        scenario=args.scenario.strip("/"),
        actions_path=actions_path,
        duration=max(0.0, args.duration),
        interval=max(0.05, args.interval),
        out_root=out_root,
        submit_key=submit_key,
        newline_key=newline_key,
        newline_prefix=newline_prefix,
        submit_delay_ms=max(0, args.submit_delay_ms),
        settle_ms=max(0, args.settle_ms),
        settle_attempts=max(1, args.settle_attempts),
        poller_terminate_timeout=max(0.0, float(args.poller_terminate_timeout)),
        pre_sleep=max(0.0, args.pre_sleep),
        post_sleep=max(0.0, args.post_sleep),
        provider_dump_dir=provider_dump_dir,
        provider_fail_on_http_error=bool(args.provider_fail_on_http_error),
        provider_fail_on_model_not_found=bool(args.provider_fail_on_model_not_found),
        provider_fail_on_permission_error=bool(args.provider_fail_on_permission_error),
        provider_forbid_model_aliases=explicit_aliases or default_forbidden_aliases,
        no_png=bool(args.no_png),
        capture_mode=str(args.capture_mode),
        tail_lines=max(1, int(args.tail_lines)),
        final_tail_lines=max(0, int(args.final_tail_lines)),
        fullpane_start_markers=fullpane_start_markers,
        fullpane_max_lines=max(0, int(args.fullpane_max_lines)),
        fullpane_render_max_rows=max(0, int(args.fullpane_render_max_rows)),
        render_profile=str(render_profile),
        clear_before_send=bool(args.clear_before_send),
        clear_mode=clear_mode,
        wait_idle_accept_active_timeout=bool(args.wait_idle_accept_active_timeout),
        wait_idle_accept_stable_active_after=max(0.0, float(args.wait_idle_accept_stable_active_after)),
        must_contain=must_contain,
        must_not_contain=must_not_contain,
        must_match_regex=must_match_regex,
        semantic_timeout=max(0.0, float(args.semantic_timeout)),
        semantic_fail_fast=bool(args.semantic_fail_fast),
        require_final_idle=bool(args.require_final_idle),
        capture_label=str(args.capture_label),
        session_prefix_guard=str(args.session_prefix_guard).strip(),
        protected_sessions=protected_sessions,
        max_stall_seconds=max(0.0, float(args.max_stall_seconds)),
        dry_run_actions=bool(args.dry_run_actions),
    )


def load_actions(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("actions JSON must be an array")
    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each action must be an object")
        out.append(item)
    return out


def execute_actions(
    config: ScenarioConfig,
    actions: list[dict[str, Any]],
    executed: list[dict[str, Any]] | None = None,
    semantic_failures: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if executed is None:
        executed = []
    if semantic_failures is None:
        semantic_failures = []
    for index, action in enumerate(actions, start=1):
        record: dict[str, Any] = {
            "index": index,
            "action": action,
            "started_at": time.time(),
            "status": "ok",
        }
        action_type = str(action.get("type", "send")).strip().lower()
        try:
            if action_type == "sleep":
                seconds = float(action.get("seconds", action.get("wait", 0)))
                time.sleep(max(0.0, seconds))
            elif action_type in {"wait_for_quiet", "wait_quiet"}:
                timeout = max(0.1, float(action.get("timeout", 120)))
                interval = max(0.05, float(action.get("interval", 0.5)))
                quiet_seconds = max(0.1, float(action.get("quiet_seconds", 3.0)))

                deadline = time.time() + timeout
                last_text = normalize_captured_text(tmux_capture_text(config.target))
                last_change_at = time.time()

                while True:
                    now = time.time()
                    current_text = normalize_captured_text(tmux_capture_text(config.target))
                    if current_text != last_text:
                        last_text = current_text
                        last_change_at = now
                    if (now - last_change_at) >= quiet_seconds:
                        break
                    if now >= deadline:
                        tail = "\n".join(last_text.splitlines()[-30:])
                        raise TimeoutError(
                            f"wait_for_quiet timeout after {timeout}s "
                            f"(quiet_seconds={quiet_seconds}). "
                            f"Pane tail:\n{tail}"
                        )
                    time.sleep(interval)
            elif action_type in {"wait_for_idle", "wait_idle"}:
                timeout = max(0.1, float(action.get("timeout", 180)))
                interval = max(0.05, float(action.get("interval", 0.5)))
                quiet_seconds = max(0.1, float(action.get("quiet_seconds", 3.0)))
                max_stall_seconds = max(0.0, float(action.get("max_stall_seconds", config.max_stall_seconds)))
                accept_active_timeout = bool(
                    action.get("accept_active_timeout", config.wait_idle_accept_active_timeout)
                )
                accept_stable_active_after = max(
                    0.0,
                    float(action.get("accept_stable_active_after", config.wait_idle_accept_stable_active_after)),
                )
                active_tokens = [
                    token
                    for token in (
                        action.get("active_tokens")
                        if isinstance(action.get("active_tokens"), list)
                        else default_active_tokens(config.target)
                    )
                    if isinstance(token, str) and token
                ]
                ready_tokens = [
                    token
                    for token in (
                        action.get("ready_tokens")
                        if isinstance(action.get("ready_tokens"), list)
                        else default_ready_tokens(config.target)
                    )
                    if isinstance(token, str) and token
                ]
                fail_tokens = [
                    token
                    for token in (
                        action.get("fail_tokens")
                        if isinstance(action.get("fail_tokens"), list)
                        else default_fail_tokens(config.target)
                    )
                    if isinstance(token, str) and token
                ]
                wait_for_idle_check(
                    config.target,
                    timeout=timeout,
                    interval=interval,
                    quiet_seconds=quiet_seconds,
                    ready_tokens=ready_tokens,
                    active_tokens=active_tokens,
                    fail_tokens=fail_tokens,
                    accept_active_timeout=accept_active_timeout,
                    accept_stable_active_after=accept_stable_active_after,
                    max_stall_seconds=max_stall_seconds,
                )
            elif action_type in {"startup_preflight", "preflight_ready"}:
                timeout = max(0.1, float(action.get("timeout", 60)))
                interval = max(0.05, float(action.get("interval", 0.4)))
                ready_tokens = [
                    token
                    for token in (
                        action.get("ready_tokens")
                        if isinstance(action.get("ready_tokens"), list)
                        else default_ready_tokens(config.target)
                    )
                    if isinstance(token, str) and token
                ]
                fail_tokens = [
                    token
                    for token in (
                        action.get("fail_tokens")
                        if isinstance(action.get("fail_tokens"), list)
                        else default_fail_tokens(config.target)
                    )
                    if isinstance(token, str) and token
                ]
                kick_keys = [
                    key
                    for key in action.get("kick_keys", ["Escape"])
                    if isinstance(key, str) and key
                ]
                kick_every = max(0, int(action.get("kick_every_polls", 4)))
                deadline = time.time() + timeout
                poll_count = 0
                last_text = ""

                while True:
                    poll_count += 1
                    last_text = normalize_captured_text(tmux_capture_text(config.target))

                    # Claude can enter a bad "custom model" state (e.g. haiku-4-5) where requests fail.
                    # When this happens, proactively switch to the real Haiku 4.5 entry via /model.
                    if (
                        "There's an issue with the selected model" in last_text
                        and "Run /model" in last_text
                    ):
                        model_choice = str(action.get("model_choice", "4")).strip() or "4"
                        tmux_send_text(config.target, "/model")
                        tmux_send_key(config.target, config.submit_key)
                        time.sleep(interval)

                        menu_deadline = time.time() + max(8.0, interval * 6)
                        menu_text = ""
                        while time.time() < menu_deadline:
                            menu_text = normalize_captured_text(tmux_capture_text(config.target))
                            if "Select model" in menu_text and "Enter to confirm" in menu_text:
                                break
                            time.sleep(interval)
                        else:
                            tail = "\n".join(menu_text.splitlines()[-40:])
                            raise TimeoutError(
                                "startup_preflight could not open /model menu within timeout. "
                                f"Pane tail:\n{tail}"
                            )

                        tmux_send_key(config.target, model_choice)
                        tmux_send_key(config.target, config.submit_key)
                        time.sleep(interval)
                        continue

                    # Claude first-run theme picker blocks input until selection.
                    if (
                        "Choose the text style that looks best with your" in last_text
                        and "run /theme" in last_text
                    ):
                        # Newer builds may require an explicit numeric selection before submit.
                        tmux_send_key(config.target, "1")
                        tmux_send_key(config.target, "C-m")
                        time.sleep(interval)
                        continue

                    # Claude login method picker can appear on fresh homes.
                    if (
                        "Select login method:" in last_text
                        and "Claude account with subscription" in last_text
                        and "Anthropic Console account" in last_text
                    ):
                        login_choice = str(action.get("login_choice", "2")).strip() or "2"
                        tmux_send_key(config.target, login_choice)
                        tmux_send_key(config.target, "C-m")
                        time.sleep(interval)
                        continue

                    # OAuth device/code flow requires a human sign-in; fail fast with clear guidance.
                    if "Paste code here if prompted >" in last_text and "oauth/authorize" in last_text:
                        tail = "\n".join(last_text.splitlines()[-30:])
                        raise RuntimeError(
                            "startup_preflight requires manual Claude OAuth sign-in in target pane. "
                            "Complete login interactively, then rerun capture. "
                            f"Pane tail:\n{tail}"
                        )

                    fail_hit = next((token for token in fail_tokens if token in last_text), None)
                    if fail_hit is not None:
                        tail = "\n".join(last_text.splitlines()[-30:])
                        raise RuntimeError(
                            f"startup_preflight failed on token '{fail_hit}'. Pane tail:\n{tail}"
                        )

                    if any(token in last_text for token in ready_tokens):
                        break

                    if kick_every > 0 and kick_keys and (poll_count % kick_every == 0):
                        for key in kick_keys:
                            tmux_send_key(config.target, key)

                    if time.time() >= deadline:
                        tail = "\n".join(last_text.splitlines()[-30:])
                        raise TimeoutError(
                            f"startup_preflight timeout after {timeout}s "
                            f"(ready_tokens={ready_tokens}). Pane tail:\n{tail}"
                        )
                    time.sleep(interval)
            elif action_type in {"wait_until", "wait_for"}:
                timeout = max(0.1, float(action.get("timeout", 60)))
                interval = max(0.05, float(action.get("interval", 0.5)))

                contains_raw = action.get("contains", [])
                if isinstance(contains_raw, str):
                    contains = [contains_raw]
                elif isinstance(contains_raw, list):
                    contains = [str(item) for item in contains_raw]
                else:
                    raise ValueError("wait_until.contains must be a string or array of strings")
                contains = [token for token in contains if token]

                absent_raw = action.get("absent", [])
                if isinstance(absent_raw, str):
                    absent = [absent_raw]
                elif isinstance(absent_raw, list):
                    absent = [str(item) for item in absent_raw]
                else:
                    raise ValueError("wait_until.absent must be a string or array of strings")
                absent = [token for token in absent if token]

                require_raw = action.get("require_contains_any", [])
                if isinstance(require_raw, str):
                    require_contains_any = [require_raw]
                elif isinstance(require_raw, list):
                    require_contains_any = [str(item) for item in require_raw]
                else:
                    raise ValueError("wait_until.require_contains_any must be a string or array of strings")
                require_contains_any = [token for token in require_contains_any if token]

                require_timeout = max(0.1, float(action.get("require_timeout", min(timeout, 30.0))))
                deadline = time.time() + timeout
                last_text = ""

                if require_contains_any:
                    require_deadline = min(deadline, time.time() + require_timeout)
                    while True:
                        last_text = normalize_captured_text(tmux_capture_text(config.target))
                        if any(token in last_text for token in require_contains_any):
                            break
                        if time.time() >= require_deadline:
                            tail = "\n".join(last_text.splitlines()[-30:])
                            raise TimeoutError(
                                f"wait_until require_contains_any timeout after {require_timeout}s "
                                f"(require_contains_any={require_contains_any}). "
                                f"Pane tail:\n{tail}"
                            )
                        time.sleep(interval)

                while True:
                    last_text = normalize_captured_text(tmux_capture_text(config.target))
                    has_all_contains = all(token in last_text for token in contains)
                    has_no_absent = all(token not in last_text for token in absent)
                    if has_all_contains and has_no_absent:
                        break
                    if time.time() >= deadline:
                        tail = "\n".join(last_text.splitlines()[-30:])
                        raise TimeoutError(
                            f"wait_until timeout after {timeout}s "
                            f"(contains={contains}, absent={absent}). "
                            f"Pane tail:\n{tail}"
                        )
                    time.sleep(interval)
            elif action_type == "resize":
                cols = action.get("cols")
                rows = action.get("rows")
                if cols is None or rows is None:
                    raise ValueError("resize action requires 'cols' and 'rows'")
                scope = str(action.get("target", action.get("scope", "window")))
                before = tmux_display_pane_size(config.target)
                after = tmux_resize_target(config.target, cols=int(cols), rows=int(rows), scope=scope)
                record["resize"] = {
                    "scope": scope,
                    "requested": {"cols": int(cols), "rows": int(rows)},
                    "before": {"cols": before[0], "rows": before[1]},
                    "after": {"cols": after[0], "rows": after[1]},
                }
                assert_size = action.get("assert_size")
                if isinstance(assert_size, bool) and assert_size:
                    if after != (int(cols), int(rows)):
                        raise AssertionError(
                            f"resize assert_size failed: got {after[0]}x{after[1]}, expected {cols}x{rows}"
                        )
                wait_after = float(action.get("wait_after", action.get("wait", 0)))
                if wait_after > 0:
                    time.sleep(wait_after)
            elif action_type == "key":
                key = str(action.get("key", "")).strip()
                if not key:
                    raise ValueError("key action requires non-empty 'key'")
                tmux_send_key(config.target, normalize_tmux_key(key, fallback="C-m", for_submit=False))
                wait = float(action.get("wait", 0))
                if wait > 0:
                    time.sleep(wait)
            elif action_type == "submit":
                submit_key = normalize_tmux_key(
                    str(action.get("submit_key", config.submit_key)),
                    fallback=config.submit_key,
                    for_submit=True,
                )
                tmux_send_key(config.target, submit_key)
                wait = float(action.get("wait", 0))
                if wait > 0:
                    time.sleep(wait)
            elif action_type in {"submit_enter", "submit_enter_key"}:
                # Explicit submit action to avoid ambiguity with newline behavior.
                submit_key = normalize_tmux_key(
                    str(action.get("submit_key", config.submit_key)),
                    fallback=config.submit_key,
                    for_submit=True,
                )
                tmux_send_key(config.target, submit_key)
                wait = float(action.get("wait", 0))
                if wait > 0:
                    time.sleep(wait)
            elif action_type == "newline":
                newline_prefix = str(action.get("newline_prefix", config.newline_prefix))
                newline_key = normalize_tmux_key(
                    str(action.get("newline_key", config.newline_key)),
                    fallback=config.newline_key,
                    for_submit=False,
                )
                if newline_prefix:
                    tmux_send_text(config.target, newline_prefix)
                tmux_send_key(config.target, newline_key)
                wait = float(action.get("wait", 0))
                if wait > 0:
                    time.sleep(wait)
            elif action_type in {"literal_newline", "multiline_newline"}:
                # Explicit literal newline action used for multiline composition tests.
                newline_prefix = str(action.get("newline_prefix", action.get("prefix", config.newline_prefix)))
                newline_key = normalize_tmux_key(
                    str(action.get("newline_key", config.newline_key)),
                    fallback=config.newline_key,
                    for_submit=False,
                )
                if newline_prefix:
                    tmux_send_text(config.target, newline_prefix)
                tmux_send_key(config.target, newline_key)
                wait = float(action.get("wait", 0))
                if wait > 0:
                    time.sleep(wait)
            elif action_type == "comment":
                pass
            else:
                clear_first = bool(action.get("clear_first", config.clear_before_send))
                if clear_first:
                    clear_mode = str(action.get("clear_mode", config.clear_mode)).strip().lower()
                    clear_composer(config.target, clear_mode)
                text = str(action.get("text", ""))
                send_mode = str(action.get("send_mode", "")).strip().lower()
                sent_text = False
                if text:
                    if "\n" in text and not bool(action.get("allow_literal_newlines", False)):
                        raise ValueError(
                            "send action text contains literal newlines. "
                            "Use send_mode=multiline (newline key actions) or set allow_literal_newlines=true."
                        )
                    tmux_send_text(config.target, text)
                    sent_text = True
                input_mode = str(action.get("input_mode", "")).strip().lower()
                if send_mode and not input_mode:
                    if send_mode in {"submit", "enter", "submit_enter", "submit-only", "submit_only"}:
                        input_mode = "submit_enter"
                    elif send_mode in {"multiline", "newline", "shift_enter", "escaped_newline"}:
                        input_mode = "shift_enter"
                    elif send_mode in {"literal", "type"}:
                        input_mode = ""
                    else:
                        raise ValueError(f"unknown send_mode: {send_mode}")
                submit_requested = bool(action.get("submit", False) or action.get("enter", False))
                newline_requested = bool(action.get("newline", False))
                if input_mode in {"submit_enter", "submit_only", "submit-only"}:
                    submit_requested = True
                    newline_requested = False
                elif input_mode in {"literal_newline", "newline_only", "newline-only"}:
                    newline_requested = True
                    submit_requested = False
                elif input_mode in {"shift_enter", "escaped_newline"}:
                    newline_requested = True
                    submit_requested = False
                    if "newline_prefix" not in action:
                        action["newline_prefix"] = config.newline_prefix

                if submit_requested and newline_requested:
                    raise ValueError("send action cannot request both submit and newline; set send_mode explicitly.")

                if newline_requested:
                    newline_prefix = str(action.get("newline_prefix", config.newline_prefix))
                    newline_key = normalize_tmux_key(
                        str(action.get("newline_key", config.newline_key)),
                        fallback=config.newline_key,
                        for_submit=False,
                    )
                    if newline_prefix:
                        tmux_send_text(config.target, newline_prefix)
                    tmux_send_key(config.target, newline_key)
                    sent_text = True

                if submit_requested:
                    submit_key = normalize_tmux_key(
                        str(action.get("submit_key", config.submit_key)),
                        fallback=config.submit_key,
                        for_submit=True,
                    )
                    submit_delay_ms = int(action.get("submit_delay_ms", config.submit_delay_ms))
                    if sent_text and submit_delay_ms > 0:
                        time.sleep(submit_delay_ms / 1000.0)
                    tmux_send_key(config.target, submit_key)
                wait = float(action.get("wait", 0))
                if wait > 0:
                    time.sleep(wait)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            record["ended_at"] = time.time()
            executed.append(record)
            raise
        (
            action_must_contain,
            action_must_not_contain,
            action_must_match_regex,
            action_must_contain_any,
            action_must_match_regex_any,
        ) = collect_action_semantics(action)
        if (
            action_must_contain
            or action_must_not_contain
            or action_must_match_regex
            or action_must_contain_any
            or action_must_match_regex_any
        ):
            semantic_timeout = max(0.0, float(action.get("semantic_timeout", config.semantic_timeout)))
            semantic_fail_fast = bool(action.get("semantic_fail_fast", config.semantic_fail_fast))
            ok, last_text, failures = wait_for_semantic_assertions(
                config.target,
                must_contain=action_must_contain,
                must_not_contain=action_must_not_contain,
                must_match_regex=action_must_match_regex,
                must_contain_any=action_must_contain_any,
                must_match_regex_any=action_must_match_regex_any,
                timeout=semantic_timeout,
                interval=min(0.5, max(0.05, config.interval)),
            )
            if not ok:
                semantic_tail = "\n".join(last_text.splitlines()[-30:])
                semantic_entry = {
                    "index": index,
                    "scope": "action",
                    "failures": failures,
                    "timeout": semantic_timeout,
                    "must_contain": list(action_must_contain),
                    "must_not_contain": list(action_must_not_contain),
                    "must_match_regex": list(action_must_match_regex),
                    "must_contain_any": list(action_must_contain_any),
                    "must_match_regex_any": list(action_must_match_regex_any),
                    "pane_tail": semantic_tail,
                }
                semantic_failures.append(semantic_entry)
                record["semantic_error"] = semantic_entry
                if semantic_fail_fast:
                    record["status"] = "error"
                    record["error"] = "semantic assertion failed (fail-fast)"
                    record["ended_at"] = time.time()
                    executed.append(record)
                    raise AssertionError(json.dumps(semantic_entry, ensure_ascii=False))
        record["ended_at"] = time.time()
        executed.append(record)
    return executed


def validate_frame_bundle(run_dir: Path, interval_seconds: float) -> dict[str, Any]:
    frames_dir = run_dir / "frames"
    summary: dict[str, Any] = {
        "frames_dir_exists": frames_dir.exists(),
        "frame_count": 0,
        "missing_frame_indices": [],
        "missing_artifacts": [],
        "max_unchanged_streak_frames": 0,
        "max_unchanged_streak_seconds_estimate": 0.0,
    }
    if not frames_dir.exists():
        return summary

    frame_re = re.compile(r"^frame_(\d{4})\.(txt|ansi|png)$")
    by_index: dict[int, set[str]] = {}
    for path in frames_dir.iterdir():
        if not path.is_file():
            continue
        match = frame_re.match(path.name)
        if not match:
            continue
        index = int(match.group(1))
        ext = match.group(2)
        by_index.setdefault(index, set()).add(ext)
    if not by_index:
        return summary

    indices = sorted(by_index.keys())
    summary["frame_count"] = len(indices)
    all_exts = {"txt", "ansi", "png"}
    for index in indices:
        missing = sorted(all_exts - by_index[index])
        if missing:
            summary["missing_artifacts"].append({"index": index, "missing": missing})
    min_idx = indices[0]
    max_idx = indices[-1]
    present = set(indices)
    summary["missing_frame_indices"] = [idx for idx in range(min_idx, max_idx + 1) if idx not in present]

    # Delta/stall estimate is based on adjacent .txt content changes.
    max_streak = 0
    streak = 0
    previous_text: str | None = None
    for index in indices:
        txt_path = frames_dir / f"frame_{index:04d}.txt"
        if not txt_path.exists():
            continue
        current_text = txt_path.read_text(encoding="utf-8", errors="replace")
        if previous_text is not None and current_text == previous_text:
            streak += 1
        else:
            streak = 0
        if streak > max_streak:
            max_streak = streak
        previous_text = current_text
    summary["max_unchanged_streak_frames"] = max_streak
    summary["max_unchanged_streak_seconds_estimate"] = round(max_streak * max(interval_seconds, 0.05), 3)
    return summary


def exit_code_for_scenario_result(scenario_result: str) -> int:
    if scenario_result == "pass":
        return 0
    if scenario_result == "operational_pass_semantic_fail":
        return 2
    return 3


def analyze_provider_new_files(
    provider_dump_dir: Path | None,
    provider_new: list[str],
    forbidden_aliases: tuple[str, ...],
) -> dict[str, Any]:
    analysis: dict[str, Any] = {
        "http_error_files": [],
        "model_not_found_files": [],
        "forbidden_model_alias_hits": [],
        "permission_error_files": [],
        "scope_error_files": [],
    }
    if provider_dump_dir is None:
        return analysis

    status_re = re.compile(r"not_found_error", re.IGNORECASE)
    permission_re = re.compile(r"insufficient permissions|missing scopes|permission denied", re.IGNORECASE)
    scope_token_re = re.compile(r"api\.(?:responses\.write|model\.read)")
    for rel_path in provider_new:
        path = provider_dump_dir / rel_path
        if not path.exists():
            continue
        if path.name.endswith("_response.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = payload.get("status_code")
            if not isinstance(status, int):
                status = payload.get("status")
            if isinstance(status, int) and status >= 400:
                analysis["http_error_files"].append({"file": rel_path, "status": status})
            text_blob = json.dumps(payload, ensure_ascii=False)
            if status_re.search(text_blob) and "model" in text_blob.lower():
                analysis["model_not_found_files"].append(rel_path)
            if permission_re.search(text_blob):
                analysis["permission_error_files"].append(rel_path)
            scope_hits = sorted(set(scope_token_re.findall(text_blob)))
            if scope_hits:
                analysis["scope_error_files"].append({"file": rel_path, "status": status, "scopes": scope_hits})
        elif path.name.endswith("_request.json") and forbidden_aliases:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            models = extract_models(payload)
            for alias in forbidden_aliases:
                for model in models:
                    if model == alias:
                        analysis["forbidden_model_alias_hits"].append(
                            {"file": rel_path, "alias": alias, "model": model}
                        )
    return analysis


def main() -> None:
    config = parse_args()
    global _DEFAULT_TMUX_SOCKET
    _DEFAULT_TMUX_SOCKET = config.tmux_socket
    actions = load_actions(config.actions_path)
    ensure_target_allowed(config.target, config.session_prefix_guard, config.protected_sessions)
    ensure_target_exists(config.target)

    if config.dry_run_actions:
        dry_run_payload = {
            "scenario": config.scenario,
            "target": config.target,
            "actions_path": str(config.actions_path) if config.actions_path else None,
            "actions_count": len(actions),
            "submit_key": config.submit_key,
            "newline_key": config.newline_key,
            "newline_prefix": config.newline_prefix,
            "must_contain": list(config.must_contain),
            "must_not_contain": list(config.must_not_contain),
            "must_match_regex": list(config.must_match_regex),
            "semantic_timeout": config.semantic_timeout,
            "semantic_fail_fast": config.semantic_fail_fast,
            "require_final_idle": config.require_final_idle,
            "capture_mode": config.capture_mode,
            "tail_lines": config.tail_lines,
            "final_tail_lines": config.final_tail_lines,
            "fullpane_start_markers": list(config.fullpane_start_markers),
            "fullpane_max_lines": config.fullpane_max_lines,
            "fullpane_render_max_rows": config.fullpane_render_max_rows,
            "render_profile": config.render_profile,
            "wait_idle_accept_active_timeout": config.wait_idle_accept_active_timeout,
            "wait_idle_accept_stable_active_after": config.wait_idle_accept_stable_active_after,
            "max_stall_seconds": config.max_stall_seconds,
            "capture_label": config.capture_label,
        }
        print(json.dumps(dry_run_payload, indent=2))
        return

    script_dir = Path(__file__).resolve().parent
    poller_path = script_dir / "tmux_capture_poll.py"

    run_id = run_timestamp()
    scenario_parent = config.out_root / config.scenario / run_id
    scenario_parent.mkdir(parents=True, exist_ok=True)

    # The poller writes directly into <out_root>/<scenario>/<run_id>/ so we don't have to
    # guess which timestamp directory it created. This also makes scenarios fast to iterate
    # on and easier to diff.
    run_dir = config.out_root / config.scenario / run_id

    provider_before = list_files(config.provider_dump_dir)

    poller_cmd = [
        sys.executable,
        str(poller_path),
        "--target",
        config.target,
        "--tmux-socket",
        config.tmux_socket,
        "--duration",
        str(config.duration),
        "--interval",
        str(config.interval),
        "--scenario",
        config.scenario,
        "--run-id",
        run_id,
        "--out-root",
        str(config.out_root),
        "--capture-mode",
        config.capture_mode,
        "--tail-lines",
        str(config.tail_lines),
        "--final-tail-lines",
        str(config.final_tail_lines),
        "--fullpane-start-markers",
        ",".join(config.fullpane_start_markers),
        "--fullpane-max-lines",
        str(config.fullpane_max_lines),
        "--fullpane-render-max-rows",
        str(config.fullpane_render_max_rows),
        "--render-profile",
        config.render_profile,
        "--settle-ms",
        str(config.settle_ms),
        "--settle-attempts",
        str(config.settle_attempts),
        "--session-prefix-guard",
        config.session_prefix_guard,
        "--protected-sessions",
        ",".join(config.protected_sessions),
    ]
    if config.no_png:
        poller_cmd.append("--no-png")

    started_at = time.time()
    poller_log_path = scenario_parent / "poller.log"
    poller_log = poller_log_path.open("w", encoding="utf-8")
    poller = subprocess.Popen(
        poller_cmd,
        stdout=poller_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if config.pre_sleep > 0:
        time.sleep(config.pre_sleep)

    executed_actions: list[dict[str, Any]] = []
    semantic_failures: list[dict[str, Any]] = []
    execution_error: str | None = None
    provider_error: str | None = None
    provider_error_kind: str | None = None
    try:
        execute_actions(config, actions, executed_actions, semantic_failures)
    except Exception as exc:
        execution_error = str(exc)
    if config.post_sleep > 0:
        time.sleep(config.post_sleep)

    global_semantic: dict[str, Any] | None = None
    if execution_error is None and (config.must_contain or config.must_not_contain or config.must_match_regex):
        semantic_ok, semantic_text, semantic_errors = wait_for_semantic_assertions(
            config.target,
            must_contain=config.must_contain,
            must_not_contain=config.must_not_contain,
            must_match_regex=config.must_match_regex,
            timeout=config.semantic_timeout,
            interval=min(0.5, max(0.05, config.interval)),
        )
        if not semantic_ok:
            global_semantic = {
                "scope": "global",
                "timeout": config.semantic_timeout,
                "must_contain": list(config.must_contain),
                "must_not_contain": list(config.must_not_contain),
                "must_match_regex": list(config.must_match_regex),
                "failures": semantic_errors,
                "pane_tail": "\n".join(semantic_text.splitlines()[-30:]),
            }
            semantic_failures.append(global_semantic)

    final_idle_error: str | None = None
    if execution_error is None and config.require_final_idle:
        try:
            wait_for_idle_check(
                config.target,
                timeout=max(30.0, config.semantic_timeout),
                interval=max(0.05, config.interval),
                quiet_seconds=2.0,
                ready_tokens=default_ready_tokens(config.target),
                active_tokens=default_active_tokens(config.target),
                fail_tokens=default_fail_tokens(config.target),
                accept_active_timeout=config.wait_idle_accept_active_timeout,
                accept_stable_active_after=config.wait_idle_accept_stable_active_after,
                max_stall_seconds=config.max_stall_seconds,
            )
        except Exception as exc:
            final_idle_error = str(exc)

    poller_exit: int | None = poller.poll()
    poller_terminated_by_runner = False
    if poller_exit is None:
        try:
            # Treat --duration as a maximum cap only: once actions + checks finish, stop the poller.
            poller_terminated_by_runner = True
            poller.terminate()
            poller_exit = poller.wait(timeout=max(0.1, config.poller_terminate_timeout))
        except subprocess.TimeoutExpired:
            poller.kill()
            poller_exit = poller.wait(timeout=max(0.1, config.poller_terminate_timeout))
        finally:
            poller_log.close()
    else:
        poller_log.close()
    ended_at = time.time()

    # The poller creates the run directory immediately, but give it a brief grace window.
    deadline = time.time() + 3.0
    while not run_dir.exists() and time.time() < deadline:
        time.sleep(0.05)
    if not run_dir.exists():
        raise RuntimeError(f"poller did not create expected run directory: {run_dir}")
    if config.actions_path is not None and config.actions_path.exists():
        (run_dir / "actions.json").write_text(config.actions_path.read_text(encoding="utf-8"), encoding="utf-8")

    provider_after = list_files(config.provider_dump_dir)
    provider_new = sorted(provider_after - provider_before)
    provider_analysis = analyze_provider_new_files(
        config.provider_dump_dir,
        provider_new,
        config.provider_forbid_model_aliases,
    )

    if config.provider_fail_on_http_error and provider_analysis["http_error_files"]:
        provider_error = (
            "provider HTTP errors detected: "
            + json.dumps(provider_analysis["http_error_files"], ensure_ascii=False)
        )
        provider_error_kind = "http_error"
    if provider_error is None and config.provider_fail_on_model_not_found and provider_analysis["model_not_found_files"]:
        provider_error = (
            "provider model not found errors detected: "
            + json.dumps(provider_analysis["model_not_found_files"], ensure_ascii=False)
        )
        provider_error_kind = "model_not_found"
    if provider_error is None and config.provider_fail_on_permission_error and provider_analysis["permission_error_files"]:
        if provider_analysis.get("scope_error_files"):
            provider_error = (
                "provider permission/scope errors detected: "
                + json.dumps(provider_analysis["scope_error_files"], ensure_ascii=False)
            )
            provider_error_kind = "permission_scope_error"
        else:
            provider_error = (
                "provider permission errors detected: "
                + json.dumps(provider_analysis["permission_error_files"], ensure_ascii=False)
            )
            provider_error_kind = "permission_error"
    if provider_error is None and provider_analysis["forbidden_model_alias_hits"]:
        provider_error = (
            "forbidden provider model aliases detected: "
            + json.dumps(provider_analysis["forbidden_model_alias_hits"], ensure_ascii=False)
        )
        provider_error_kind = "forbidden_model_alias"

    frame_validation = validate_frame_bundle(run_dir, interval_seconds=config.interval)
    frame_stall_error: str | None = None
    if config.max_stall_seconds > 0 and frame_validation.get("max_unchanged_streak_seconds_estimate", 0.0) >= config.max_stall_seconds:
        frame_stall_error = (
            f"frame content stall exceeded max_stall_seconds={config.max_stall_seconds}: "
            f"{frame_validation['max_unchanged_streak_seconds_estimate']}s"
        )

    action_error = execution_error
    if action_error is None and semantic_failures:
        action_error = "semantic assertion failed"
    if action_error is None and final_idle_error is not None:
        action_error = f"final idle check failed: {final_idle_error}"
    if action_error is None and provider_error is not None:
        action_error = provider_error
    if action_error is None and frame_stall_error is not None:
        action_error = frame_stall_error

    scenario_result = "pass"
    if (
        (poller_exit not in (0, None) and not poller_terminated_by_runner)
        or execution_error is not None
        or provider_error is not None
        or final_idle_error is not None
        or frame_stall_error is not None
    ):
        scenario_result = "fail"
    elif semantic_failures:
        scenario_result = "operational_pass_semantic_fail"

    manifest = {
        "scenario": config.scenario,
        "run_id": run_id,
        "target": config.target,
        "tmux_socket": config.tmux_socket,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": ended_at - started_at,
        "poller_cmd": poller_cmd,
        "poller_log": str(poller_log_path),
        "poller_exit_code": poller_exit,
        "poller_terminated_by_runner": poller_terminated_by_runner,
        "poller_terminate_timeout_seconds": config.poller_terminate_timeout,
        "actions_path": str(config.actions_path) if config.actions_path else None,
        "actions_count": len(actions),
        "executed_actions": executed_actions,
        "action_error": action_error,
        "execution_error": execution_error,
        "semantic_failures": semantic_failures,
        "global_semantic": global_semantic,
        "final_idle_error": final_idle_error,
        "provider_error": provider_error,
        "provider_error_kind": provider_error_kind,
        "frame_stall_error": frame_stall_error,
        "scenario_result": scenario_result,
        "provider_dump_dir": str(config.provider_dump_dir) if config.provider_dump_dir else None,
        "provider_before_count": len(provider_before),
        "provider_after_count": len(provider_after),
        "provider_new_count": len(provider_new),
        "provider_new_files": provider_new,
        "provider_analysis": provider_analysis,
        "provider_forbid_model_aliases": list(config.provider_forbid_model_aliases),
        "capture_mode": config.capture_mode,
        "tail_lines": config.tail_lines,
        "final_tail_lines": config.final_tail_lines,
        "fullpane_start_markers": list(config.fullpane_start_markers),
        "fullpane_max_lines": config.fullpane_max_lines,
        "fullpane_render_max_rows": config.fullpane_render_max_rows,
        "render_profile": config.render_profile,
        "submit_key": config.submit_key,
        "newline_key": config.newline_key,
        "newline_prefix": config.newline_prefix,
        "wait_idle_accept_active_timeout": config.wait_idle_accept_active_timeout,
        "wait_idle_accept_stable_active_after": config.wait_idle_accept_stable_active_after,
        "must_contain": list(config.must_contain),
        "must_not_contain": list(config.must_not_contain),
        "must_match_regex": list(config.must_match_regex),
        "semantic_timeout": config.semantic_timeout,
        "semantic_fail_fast": config.semantic_fail_fast,
        "require_final_idle": config.require_final_idle,
        "capture_label": config.capture_label,
        "session_prefix_guard": config.session_prefix_guard,
        "protected_sessions": list(config.protected_sessions),
        "max_stall_seconds": config.max_stall_seconds,
        "dry_run_actions": config.dry_run_actions,
        "frame_validation": frame_validation,
    }
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    run_summary = {
        "scenario": config.scenario,
        "run_id": run_id,
        "scenario_result": scenario_result,
        "duration_seconds": ended_at - started_at,
        "tmux_socket": config.tmux_socket,
        "poller_exit_code": poller_exit,
        "poller_terminated_by_runner": poller_terminated_by_runner,
        "poller_terminate_timeout_seconds": config.poller_terminate_timeout,
        "actions_count": len(actions),
        "executed_actions_count": len(executed_actions),
        "semantic_failures_count": len(semantic_failures),
        "provider_new_files_count": len(provider_new),
        "capture_label": config.capture_label,
        "target": config.target,
        "action_error": action_error,
        "provider_error_kind": provider_error_kind,
        "provider_scope_error_files_count": len(provider_analysis.get("scope_error_files", [])),
        "provider_permission_error_files_count": len(provider_analysis.get("permission_error_files", [])),
    }
    (run_dir / "run_summary.json").write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")

    print(f"[scenario] run_dir={run_dir}")
    print(f"[scenario] poller_exit={poller_exit} result={scenario_result} action_error={action_error!r}")
    print(f"[scenario] provider_new_files={len(provider_new)}")
    raise SystemExit(exit_code_for_scenario_result(scenario_result))


if __name__ == "__main__":
    main()
