#!/usr/bin/env python3
"""
Bootstrap provider-specific logged tmux sessions into an automation-ready state.

Goals:
- Create isolated, reproducible tmux stacks under the shared /docs_tmp/ tree
- Fail fast when a human sign-in step is required (OAuth/device code flows)
- Provide a single CLI entrypoint for "start + verify readiness"

This is intentionally not a full scenario runner; it only gets you to the
"prompt is ready and not blocked by onboarding/auth UI" baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import shlex
from dataclasses import dataclass
from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[1]
SCE_ROOT = REPO_ROOT.parent


def read_dotenv_value(env_path: Path, key: str) -> str:
    """
    Best-effort read of KEY=... from a dotenv file.

    Security note:
    - This function must never print the returned value.
    """
    try:
        raw = env_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if not stripped.startswith(f"{key}="):
            continue
        _, value = stripped.split("=", 1)
        value = value.strip()
        # Strip single/double quotes if present.
        if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in ("'", '"')):
            value = value[1:-1]
        return value.strip()
    return ""


def env_file_has_key(env_path: Path, key: str) -> bool:
    """
    Best-effort check for KEY=... in a dotenv file without loading values into logs.
    """
    try:
        raw = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except Exception:
        return False
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if not stripped.startswith(f"{key}="):
            continue
        _, value = stripped.split("=", 1)
        # Empty assignment counts as "not configured".
        return bool(value.strip().strip("\"'"))
    return False


def preapprove_claude_custom_api_key(home_dir: Path, api_key: str) -> bool:
    """
    Claude Code will ignore ANTHROPIC_API_KEY until the user has approved it once
    via onboarding UI. That approval is persisted to ~/.claude.json as:

      customApiKeyResponses.approved = ["<last 20 chars of key>", ...]

    This function pre-seeds that approval in an isolated CLAUDE_CODE_HOME, enabling
    unattended bootstrap runs.
    """
    key = (api_key or "").strip()
    if not key:
        return False
    suffix = key[-20:] if len(key) > 20 else key

    state_path = home_dir / ".claude.json"
    data: dict = {}
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

    responses = data.get("customApiKeyResponses")
    if not isinstance(responses, dict):
        responses = {}
    approved = responses.get("approved")
    if not isinstance(approved, list):
        approved = []
    if suffix not in approved:
        approved.append(suffix)
    responses["approved"] = approved
    responses.setdefault("rejected", [])
    data["customApiKeyResponses"] = responses

    state_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def run_tmux(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def tmux_capture_text(target: str) -> str:
    proc = run_tmux(["capture-pane", "-p", "-t", target])
    if proc.returncode != 0:
        raise RuntimeError(f"tmux capture-pane failed for {target}: {proc.stderr.strip()}")
    return proc.stdout


def tmux_send_keys(target: str, keys: list[str], *, literal: bool = False) -> None:
    cmd = ["send-keys", "-t", target]
    if literal:
        cmd.append("-l")
    cmd.extend(keys)
    proc = run_tmux(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"tmux send-keys failed for {target}: {proc.stderr.strip()}")


def tmux_has_session(session: str) -> bool:
    proc = run_tmux(["has-session", "-t", session])
    return proc.returncode == 0


def now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class BootstrapResult:
    provider: str
    targets: dict[str, str]
    run_root: str
    ok: bool
    errors: list[str]
    warnings: list[str]


def preflight(target: str, provider: str, *, strict: bool) -> BootstrapResult:
    cmd = [
        str(REPO_ROOT / "scripts" / "tmux_capture_preflight.py"),
        "--target",
        target,
        "--provider",
        provider,
    ]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    errors: list[str] = []
    warnings: list[str] = []
    ok = proc.returncode == 0
    try:
        payload = json.loads(proc.stdout or "{}")
        errors = list(payload.get("errors") or [])
        warnings = list(payload.get("warnings") or [])
        ok = bool(payload.get("ok")) and proc.returncode == 0
    except Exception:
        if proc.stdout.strip():
            warnings.append("preflight output was not json; see stdout/stderr")
        if proc.stderr.strip():
            warnings.append(proc.stderr.strip())
    return BootstrapResult(provider=provider, targets={"main": target}, run_root="", ok=ok, errors=errors, warnings=warnings)


def wait_for_ready_prompt(target: str, provider: str, timeout_s: float, noninteractive: bool) -> tuple[bool, str]:
    """
    Best-effort wait that also tries to dismiss the Claude theme/login pickers.
    """
    deadline = time.time() + timeout_s
    missing_session_hits = 0
    while time.time() < deadline:
        try:
            text = tmux_capture_text(target)
            missing_session_hits = 0
        except RuntimeError as exc:
            # tmux sessions can exit immediately if the underlying CLI fails fast
            # (missing binary, early auth failure, etc.). In automation we want a
            # structured failure (JSON) rather than an uncaught exception.
            msg = str(exc)
            if "can't find session" in msg:
                missing_session_hits += 1
                if missing_session_hits >= 3:
                    return False, f"tmux session disappeared while waiting for prompt: {target}"
                time.sleep(0.25)
                continue
            if noninteractive:
                return False, f"tmux capture failed while waiting for prompt: {msg}"
            time.sleep(0.5)
            continue

        if provider == "claude":
            if "Use Claude Code's terminal setup?" in text and "Shift+Enter for newlines" in text:
                # Skip terminal setup to avoid exiting into a separate setup flow.
                tmux_send_keys(target, ["2"])
                tmux_send_keys(target, ["C-m"])
                time.sleep(0.25)
                continue
            if "WARNING: Claude Code running in Bypass Permissions mode" in text and "Yes, I accept" in text:
                # We always launch with --dangerously-skip-permissions in automation runs.
                tmux_send_keys(target, ["2"])
                tmux_send_keys(target, ["C-m"])
                time.sleep(0.25)
                continue
            if "Choose the text style that looks best with your terminal" in text and "run /theme" in text:
                # Select option 1 (dark) and submit.
                tmux_send_keys(target, ["1"])
                tmux_send_keys(target, ["C-m"])
                time.sleep(0.25)
                continue
            if "Select login method:" in text and "Anthropic Console account" in text:
                # Prefer console billing (2) unless the user overrides manually.
                tmux_send_keys(target, ["2"])
                tmux_send_keys(target, ["C-m"])
                time.sleep(0.25)
                continue
            if "Paste code here if prompted >" in text and ("oauth/authorize" in text or "Opening browser to sign in" in text):
                if noninteractive:
                    return False, "Claude requires OAuth sign-in (device code). Complete login once, then rerun."

        if provider == "codex":
            # For Codex, we don't attempt to click through anything; we just wait for prompt.
            pass

        # Prompt heuristic.
        if provider == "claude":
            # The steady-state Claude Code UI shows a prompt line ("❯") and a footer line
            # containing "bypass permissions on" when launched with --dangerously-skip-permissions.
            if "bypass permissions on" in text and "❯" in text:
                return True, "ready"
            if "for shortcuts" in text:
                return True, "ready"
        if provider == "codex" and ("for shortcuts" in text and ("context left" in text or "OpenAI Codex" in text)):
            return True, "ready"
        time.sleep(0.35)
    return False, f"timeout waiting for {provider} readiness"


def bootstrap_claude(args: argparse.Namespace) -> BootstrapResult:
    ts = now_stamp()
    session = args.session or f"breadboard_test_claude_bootstrap_{ts}"
    if not session.startswith("breadboard_test_"):
        session = f"breadboard_test_{session}"

    if tmux_has_session(session):
        return BootstrapResult(provider="claude", targets={"main": f"{session}:0.0"}, run_root="", ok=False, errors=[f"tmux session exists: {session}"], warnings=[])

    # Run root under shared docs_tmp (NOT inside breadboard_repo).
    run_root = str((SCE_ROOT / "docs_tmp" / "claude_runs" / session).resolve())
    Path(run_root).mkdir(parents=True, exist_ok=True)
    home_dir = Path(run_root) / "home"

    # If we have an already-authenticated logged home, copy it to avoid first-run OAuth.
    seed_home: Path | None = None
    if args.seed_home_from:
        seed_home = Path(args.seed_home_from).expanduser().resolve()
    elif args.seed_home_from_session:
        seed_home = (SCE_ROOT / "docs_tmp" / "claude_runs" / args.seed_home_from_session / "home").resolve()

    if seed_home and seed_home.exists():
        home_dir.mkdir(parents=True, exist_ok=True)
        # Copy only known state roots to keep this cheap and deterministic.
        for name in (".claude", ".claude.json"):
            src = seed_home / name
            dst = home_dir / name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif src.is_file():
                shutil.copy2(src, dst)

    # If an API key exists, preapprove it inside the logged HOME so Claude Code will
    # use it without forcing an OAuth/device-code login flow.
    # Do not print the key.
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_api_key:
        anthropic_api_key = read_dotenv_value(REPO_ROOT / ".env", "ANTHROPIC_API_KEY")
    if anthropic_api_key:
        home_dir.mkdir(parents=True, exist_ok=True)
        preapprove_claude_custom_api_key(home_dir, anthropic_api_key)

    # Use run_claude_code_logged.sh, but redirect its logged home/workspace into our run_root.
    # Important: tmux does not inherit per-process env for new sessions; we must export inside
    # the launch command itself (or use tmux -e repeatedly).
    claude_log_dir = str(Path(run_root) / "provider_dumps")
    claude_home_dir = str(home_dir)
    claude_workspace = str(Path(args.workspace).resolve())

    # If we have an API key configured, prefer a clean logged HOME so Claude Code does not
    # import browser/OAuth auth state from the host. This is critical for unattended runs.
    has_anthropic_key = env_file_has_key(REPO_ROOT / ".env", "ANTHROPIC_API_KEY") or bool(
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    host_claude_state_dir = os.environ.get("HOST_CLAUDE_STATE_DIR", "").strip()
    host_claude_json = os.environ.get("HOST_CLAUDE_JSON", "").strip()
    if has_anthropic_key and not seed_home:
        # run_claude_code_logged.sh respects these env vars to decide whether to copy host state.
        if not host_claude_state_dir:
            host_claude_state_dir = str(home_dir / ".no_host_seed")
        if not host_claude_json:
            host_claude_json = str(home_dir / ".no_host_seed.json")

    # Prefer the pinned id unless explicitly overridden by CLI args.
    force_pinned = "1"
    pinned_model = os.environ.get("CLAUDE_CODE_PINNED_MODEL", "").strip() or "claude-haiku-4-5-20251001"
    if args.model:
        # The wrapper will pass --model; don't force its own pinned model.
        force_pinned = "0"
        pinned_model = args.model

    export_pairs: list[tuple[str, str]] = [
        ("CLAUDE_CODE_LOG_DIR", claude_log_dir),
        ("CLAUDE_CODE_HOME", claude_home_dir),
        ("CLAUDE_CODE_WORKSPACE", claude_workspace),
        ("CLAUDE_CODE_FORCE_PINNED_MODEL", force_pinned),
        ("CLAUDE_CODE_PINNED_MODEL", pinned_model),
    ]
    if host_claude_state_dir:
        export_pairs.append(("HOST_CLAUDE_STATE_DIR", host_claude_state_dir))
    if host_claude_json:
        export_pairs.append(("HOST_CLAUDE_JSON", host_claude_json))

    export_cmd = " ".join(f"export {k}={shlex.quote(v)};" for k, v in export_pairs)

    # Use a shell string so env vars apply inside the tmux pane consistently.
    model_flag = f"--model {args.model}" if args.model else ""
    # Source .env to pick up ANTHROPIC_API_KEY and other local dev vars when present.
    launch = (
        f"cd {str(REPO_ROOT)!r} && "
        f"{export_cmd} "
        "set -a; [ -f .env ] && source .env >/dev/null 2>&1 || true; set +a; "
        f"./scripts/run_claude_code_logged.sh --dangerously-skip-permissions {model_flag}"
    ).strip()
    proc = subprocess.run(
        # Fixed geometry is critical for stable captures and goldens.
        ["tmux", "new-session", "-d", "-s", session, "-x", "160", "-y", "45", "bash", "-lc", launch],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return BootstrapResult(provider="claude", targets={"main": f"{session}:0.0"}, run_root=run_root, ok=False, errors=[proc.stderr.strip() or "tmux new-session failed"], warnings=[])

    target = f"{session}:0.0"
    ok, reason = wait_for_ready_prompt(target, "claude", timeout_s=float(args.timeout), noninteractive=bool(args.noninteractive))
    if not ok:
        return BootstrapResult(provider="claude", targets={"main": target}, run_root=run_root, ok=False, errors=[reason], warnings=[])

    pf = preflight(target, "claude", strict=bool(args.strict))
    return BootstrapResult(provider="claude", targets={"main": target}, run_root=run_root, ok=pf.ok, errors=pf.errors, warnings=pf.warnings)


def bootstrap_codex(args: argparse.Namespace) -> BootstrapResult:
    ts = now_stamp()
    prefix = args.session or f"codex_bootstrap_{ts}"
    if not prefix.startswith("breadboard_test_"):
        prefix = f"breadboard_test_{prefix}"

    # start_codex_chatgpt_tmux_stack.sh creates two sessions: <prefix>_proxy and <prefix>_codex
    proxy_session = f"{prefix}_proxy"
    codex_session = f"{prefix}_codex"
    if tmux_has_session(proxy_session) or tmux_has_session(codex_session):
        return BootstrapResult(
            provider="codex",
            targets={"codex": f"{codex_session}:0.0", "proxy": f"{proxy_session}:0.0"},
            run_root="",
            ok=False,
            errors=[f"tmux session exists: {proxy_session} or {codex_session}"],
            warnings=[],
        )

    # Use bash -lc with python-side quoting; printf-style %q is not available here.
    launch = (
        f"cd {str(REPO_ROOT)!r} && "
        f"./scripts/start_codex_chatgpt_tmux_stack.sh --prefix {prefix!r} --workspace {str(Path(args.workspace).resolve())!r} --cols 160 --rows 45"
    )
    cmd = ["bash", "-lc", launch]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return BootstrapResult(provider="codex", targets={}, run_root="", ok=False, errors=[proc.stderr.strip() or proc.stdout.strip() or "stack start failed"], warnings=[])

    codex_target = f"{codex_session}:0.0"
    ok, reason = wait_for_ready_prompt(codex_target, "codex", timeout_s=float(args.timeout), noninteractive=bool(args.noninteractive))
    if not ok:
        return BootstrapResult(provider="codex", targets={"codex": codex_target, "proxy": f"{proxy_session}:0.0"}, run_root="", ok=False, errors=[reason], warnings=[])

    pf = preflight(codex_target, "codex", strict=bool(args.strict))
    return BootstrapResult(provider="codex", targets={"codex": codex_target, "proxy": f"{proxy_session}:0.0"}, run_root="", ok=pf.ok, errors=pf.errors, warnings=pf.warnings)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap logged provider tmux sessions for automation.")
    parser.add_argument("--provider", required=True, choices=("claude", "codex"), help="which provider to bootstrap")
    parser.add_argument("--session", default="", help="tmux session name (claude) or prefix (codex). Must start with breadboard_test_.")
    parser.add_argument("--workspace", default=str(REPO_ROOT), help="workspace directory for provider")
    parser.add_argument("--model", default="", help="optional model override")
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for readiness")
    parser.add_argument("--noninteractive", action="store_true", help="fail fast when manual OAuth/login is required")
    parser.add_argument("--strict", action="store_true", help="treat preflight warnings as errors")
    parser.add_argument(
        "--seed-home-from",
        default="",
        help="for claude: path to an existing logged home dir to copy from (should contain .claude/ and/or .claude.json)",
    )
    parser.add_argument(
        "--seed-home-from-session",
        default="",
        help="for claude: seed from /docs_tmp/claude_runs/<session>/home",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.provider == "claude":
        result = bootstrap_claude(args)
    else:
        result = bootstrap_codex(args)
    print(json.dumps(result.__dict__, indent=2))
    raise SystemExit(0 if result.ok else 2)


if __name__ == "__main__":
    main()
