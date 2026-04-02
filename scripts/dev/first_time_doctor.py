#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def _venv_python() -> Path:
    unix = ROOT_DIR / ".venv" / "bin" / "python"
    windows = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    return unix if unix.exists() else windows


def _effective_profile(requested: str) -> str:
    if requested != "full":
        return requested
    if not (ROOT_DIR / "tui_skeleton" / "package.json").exists():
        return "engine"
    return "full"


def _build_checks(effective_profile: str) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = [
        DoctorCheck("repo_root_present", ROOT_DIR.exists(), str(ROOT_DIR)),
        DoctorCheck("venv_python_present", _venv_python().exists(), str(_venv_python())),
        DoctorCheck(
            "quickstart_helper_present",
            (ROOT_DIR / "scripts" / "dev" / "quickstart_first_time.py").exists(),
            "scripts/dev/quickstart_first_time.py",
        ),
        DoctorCheck(
            "bootstrap_helper_present",
            (ROOT_DIR / "scripts" / "dev" / "bootstrap_first_time.sh").exists(),
            "scripts/dev/bootstrap_first_time.sh",
        ),
        DoctorCheck("makefile_present", (ROOT_DIR / "Makefile").exists(), "Makefile"),
        DoctorCheck(
            "cli_bridge_server_present",
            (ROOT_DIR / "agentic_coder_prototype" / "api" / "cli_bridge" / "server.py").exists(),
            "agentic_coder_prototype/api/cli_bridge/server.py",
        ),
    ]

    if effective_profile in {"full", "tui"}:
        checks.extend(
            [
                DoctorCheck(
                    "tui_source_present",
                    (ROOT_DIR / "tui_skeleton" / "package.json").exists(),
                    "tui_skeleton/package.json",
                ),
                DoctorCheck(
                    "ts_sdk_source_present",
                    (ROOT_DIR / "sdk" / "ts" / "package.json").exists(),
                    "sdk/ts/package.json",
                ),
            ]
        )

    if effective_profile in {"full", "engine"}:
        checks.extend(
            [
                DoctorCheck(
                    "python_requirements_present",
                    (ROOT_DIR / "requirements.txt").exists(),
                    "requirements.txt",
                ),
                DoctorCheck(
                    "devx_validation_scripts_present",
                    (ROOT_DIR / "scripts" / "dev" / "devx_smoke.sh").exists()
                    and (ROOT_DIR / "scripts" / "dev" / "full_pass_validate.sh").exists(),
                    "scripts/dev/devx_smoke.sh + scripts/dev/full_pass_validate.sh",
                ),
            ]
        )

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a low-cost first-time doctor against the current repo checkout.")
    parser.add_argument(
        "--profile",
        choices=("full", "engine", "tui"),
        default="full",
        help="Validation profile. full is the default.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any checks fail.")
    args = parser.parse_args()

    effective_profile = _effective_profile(args.profile)
    checks = _build_checks(effective_profile)
    failed = [row for row in checks if not row.ok]
    payload = {
        "ok": not failed,
        "profile": args.profile,
        "effective_profile": effective_profile,
        "checks": [asdict(row) for row in checks],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"[first-time-doctor] profile={args.profile} effective={effective_profile}")
        for row in checks:
            print(f"[{'OK' if row.ok else 'FAIL'}] {row.name}: {row.detail}")
        print(f"[first-time-doctor] {len(checks) - len(failed)}/{len(checks)} passed")

    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
