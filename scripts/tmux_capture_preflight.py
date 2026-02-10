#!/usr/bin/env python3
"""
Preflight checks for tmux capture scenario execution.

This script prevents "mystery failures" during automated tmux capture runs by checking:
- Environment basics (required commands available)
- Target safety (session prefix guard + protected session blocklist)
- Provider readiness (no onboarding/auth prompts visible; prompt is available)

Keep this dependency-free (stdlib only) so it's CI-friendly.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


def check_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def run_tmux(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(["tmux", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def capture_pane_text(target: str) -> str:
    code, out, err = run_tmux(["capture-pane", "-p", "-t", target])
    if code != 0:
        raise RuntimeError(f"tmux capture-pane failed for {target}: {err.strip()}")
    return out


def infer_provider(target: str, provider_arg: str) -> str:
    raw = provider_arg.strip().lower()
    if raw:
        return raw
    lowered = target.lower()
    if "claude" in lowered:
        return "claude"
    if "codex" in lowered:
        return "codex"
    return "unknown"


def any_contains(text: str, tokens: Iterable[str]) -> str | None:
    for token in tokens:
        if token and token in text:
            return token
    return None


def looks_like_prompt(provider: str, text: str) -> bool:
    # Keep this heuristic intentionally loose; scenario runner enforces semantics.
    if provider == "claude":
        return ("❯" in text) or ("for shortcuts" in text)
    if provider == "codex":
        return ("for shortcuts" in text) or ("context left" in text) or ("OpenAI Codex" in text)
    return ("for shortcuts" in text) or (">" in text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate environment and tmux target readiness.")
    parser.add_argument("--target", required=True, help="tmux target (session:window.pane)")
    parser.add_argument(
        "--provider",
        default="",
        help="provider hint: claude|codex (default: inferred from target name)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    parser.add_argument("--session-prefix-guard", default="breadboard_test_", help="required target session prefix")
    parser.add_argument(
        "--protected-sessions",
        default="bb_tui_codex_dev,bb_engine_codex_dev,bb_atp",
        help="comma-separated protected sessions",
    )
    args = parser.parse_args()

    target = args.target.strip()
    session = target.split(":", 1)[0].strip()
    protected = {token.strip() for token in args.protected_sessions.split(",") if token.strip()}
    provider = infer_provider(target, args.provider)
    output: dict[str, object] = {
        "target": target,
        "session": session,
        "provider": provider,
        "ok": True,
        "errors": [],
        "warnings": [],
    }

    for cmd in ("tmux", "python"):
        if not check_cmd(cmd):
            output["ok"] = False
            output["errors"].append(f"required command not found: {cmd}")

    if args.session_prefix_guard and not session.startswith(args.session_prefix_guard):
        output["ok"] = False
        output["errors"].append(
            f"session '{session}' does not match required prefix '{args.session_prefix_guard}'"
        )
    if session in protected:
        output["ok"] = False
        output["errors"].append(f"session '{session}' is protected")

    code, _stdout, stderr = run_tmux(["list-panes", "-t", target, "-F", "#{pane_id}"])
    if code != 0:
        output["ok"] = False
        output["errors"].append(f"tmux target not available: {target} ({stderr.strip()})")

    target_lower = target.lower()
    if "claude" in target_lower and not check_cmd("claude"):
        output["warnings"].append("`claude` command not found in PATH")
    if "codex" in target_lower and not check_cmd("codex"):
        output["warnings"].append("`codex` command not found in PATH")
    if "codex" in target_lower:
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.exists():
            output["warnings"].append("~/.codex/auth.json not found; ChatGPT auth mode may fail")

    # Provider readiness checks (best-effort; scenario runner still enforces semantics).
    if output["ok"]:
        try:
            pane_text = capture_pane_text(target)
        except Exception as exc:
            output["ok"] = False
            output["errors"].append(str(exc))
            pane_text = ""

        claude_blockers = [
            "Choose the text style that looks best with your terminal",
            "Select login method:",
            "Opening browser to sign in",
            "Paste code here if prompted >",
            "Browser didn't open?",
            "Credit balance too low",
            "Auth conflict: Using ANTHROPIC API KEY",
            "warning: ANTHROPIC_API_KEY is not set",
        ]
        codex_blockers = [
            "MCP startup incomplete",
            "MCP client for `oracle` failed to start",
            "missing scope",
            "insufficient permissions",
            "error: API mode selected but OPENAI_API_KEY",
            "error: API key lacks required scopes",
        ]

        if provider == "claude":
            hit = any_contains(pane_text, claude_blockers)
            if hit:
                output["ok"] = False
                output["errors"].append(f"claude not automation-ready (blocked by: {hit})")
        elif provider == "codex":
            hit = any_contains(pane_text, codex_blockers)
            if hit:
                output["ok"] = False
                output["errors"].append(f"codex not automation-ready (blocked by: {hit})")

        if provider in {"claude", "codex"} and not looks_like_prompt(provider, pane_text):
            output["warnings"].append("prompt not detected in pane capture; target may not be ready for input yet")

    if args.strict and output["warnings"]:
        output["ok"] = False
        output["errors"].append(f"strict mode: warnings present ({len(output['warnings'])})")

    print(json.dumps(output, indent=2))
    raise SystemExit(0 if output["ok"] else 2)


if __name__ == "__main__":
    main()
