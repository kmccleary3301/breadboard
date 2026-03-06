#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def _venv_python_path() -> Path:
    unix = ROOT_DIR / ".venv" / "bin" / "python"
    windows = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    return unix if unix.exists() else windows


def _breadboard_cli_usable() -> tuple[bool, str]:
    override = os.environ.get("BREADBOARD_QUICKSTART_ASSUME_CLI")
    if override:
        normalized = override.strip().lower()
        if normalized in {"0", "false", "broken"}:
            return False, "forced broken via BREADBOARD_QUICKSTART_ASSUME_CLI"
        if normalized in {"1", "true", "ok"}:
            return True, "forced ok via BREADBOARD_QUICKSTART_ASSUME_CLI"
    cli = shutil.which("breadboard")
    if not cli:
        return False, "breadboard not found on PATH"
    try:
        import subprocess

        proc = subprocess.run(
            [cli, "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        return False, detail
    return True, cli


def _cli_capabilities() -> dict:
    cli = shutil.which("breadboard")
    if not cli:
        return {
            "breadboard_on_path": False,
            "breadboard_runnable": False,
            "doctor_first_time_supported": False,
            "setup_profile_supported": False,
            "detail": "breadboard not found on PATH",
        }
    ok, detail = _breadboard_cli_usable()
    if not ok:
        return {
            "breadboard_on_path": True,
            "breadboard_runnable": False,
            "doctor_first_time_supported": False,
            "setup_profile_supported": False,
            "detail": detail,
        }
    proc_doctor = subprocess.run([cli, "doctor", "--help"], check=False, capture_output=True, text=True, timeout=8)
    proc_setup = subprocess.run([cli, "setup", "--help"], check=False, capture_output=True, text=True, timeout=8)
    doctor_text = (proc_doctor.stdout or "") + "\n" + (proc_doctor.stderr or "")
    setup_text = (proc_setup.stdout or "") + "\n" + (proc_setup.stderr or "")
    return {
        "breadboard_on_path": True,
        "breadboard_runnable": True,
        "doctor_first_time_supported": bool(proc_doctor.returncode == 0 and "--first-time" in doctor_text),
        "setup_profile_supported": bool(proc_setup.returncode == 0 and "--profile" in setup_text),
        "detail": cli,
    }


def _build_payload(include_advanced: bool) -> dict:
    has_tui = (ROOT_DIR / "tui_skeleton" / "package.json").exists()
    venv_python = str(_venv_python_path())
    cli_ok, cli_detail = _breadboard_cli_usable()

    bootstrap_cmd = "bash scripts/dev/bootstrap_first_time.sh"
    profile_cmd = "bash scripts/dev/bootstrap_first_time.sh --profile engine"
    ui_cmd = "breadboard ui --config agent_configs/misc/opencode_mock_c_fs.yaml"
    run_cmd = "breadboard run --config agent_configs/misc/opencode_mock_c_fs.yaml \"Say hi and exit.\""

    actions = [
        {"step": "bootstrap", "command": profile_cmd if not has_tui else bootstrap_cmd},
        {"step": "doctor", "command": "python scripts/dev/first_time_doctor.py --strict"},
        {"step": "onboarding_contract", "command": "make onboarding-contract"},
        {
            "step": "engine_run",
            "command": "./.venv/bin/python -m agentic_coder_prototype.api.cli_bridge.server"
            if "/" in venv_python
            else f"{venv_python} -m agentic_coder_prototype.api.cli_bridge.server",
        },
        {"step": "python_sdk_hello", "command": "python scripts/dev/python_sdk_hello.py"},
        {"step": "ts_sdk_hello", "command": "node scripts/dev/ts_sdk_hello.mjs"},
    ]

    if cli_ok:
        actions.append({"step": "cli_run", "command": run_cmd})
        if has_tui:
            actions.append({"step": "ui", "command": ui_cmd})
    elif has_tui:
        actions.append(
            {
                "step": "cli_fix",
                "command": "bash scripts/dev/repair_cli_wrapper.sh",
            }
        )

    if include_advanced:
        actions.extend(
            [
                {"step": "setup_fast_engine", "command": "make setup-fast-engine"},
                {"step": "full_doctor_profiles", "command": "python scripts/dev/first_time_doctor.py --profile engine --strict"},
                {"step": "live_sdk_lane", "command": "bash scripts/dev/sdk_hello_live_smoke.sh"},
                {"step": "unit_smoke", "command": "./scripts/smoke_switcher_fancy_use_cases.sh --no-live"},
                {"step": "full_pass_engine", "command": "make devx-full-pass-engine"},
                {"step": "devx_timing", "command": "make devx-timing"},
                {"step": "disk_report", "command": "make disk-report"},
            ]
        )

    return {
        "repo_root": str(ROOT_DIR),
        "venv_python": venv_python,
        "has_tui_source": has_tui,
        "breadboard_cli_usable": cli_ok,
        "breadboard_cli_detail": cli_detail,
        "cli_capabilities": _cli_capabilities(),
        "recommended_actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="BreadBoard no-surprises quickstart helper.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--include-advanced", action="store_true", help="Include smoke and live validation lanes.")
    args = parser.parse_args()

    payload = _build_payload(include_advanced=args.include_advanced)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("[quickstart] repo:", payload["repo_root"])
    print("[quickstart] venv python:", payload["venv_python"])
    print("[quickstart] tui source present:", payload["has_tui_source"])
    print("[quickstart] breadboard cli usable:", payload["breadboard_cli_usable"])
    print("[quickstart] cli capabilities:", json.dumps(payload["cli_capabilities"]))
    if not payload["breadboard_cli_usable"]:
        print("[quickstart] cli detail:", payload["breadboard_cli_detail"])
    print("[quickstart] recommended actions:")
    for idx, item in enumerate(payload["recommended_actions"], start=1):
        print(f"  {idx}. {item['step']}: {item['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
