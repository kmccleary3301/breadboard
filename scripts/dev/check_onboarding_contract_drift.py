#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]

REQUIRED_DOC_COMMANDS: dict[str, list[str]] = {
    "README.md": [
        "python scripts/dev/quickstart_first_time.py --include-advanced",
        "python scripts/dev/first_time_doctor.py --strict",
        "make setup-fast",
        "make setup-fast-engine",
        "make setup-refresh-python",
        "make devx-full-pass",
        "make devx-timing",
        "make disk-report",
    ],
    "docs/getting-started/INSTALL_AND_DEV_QUICKSTART.md": [
        "python scripts/dev/quickstart_first_time.py --include-advanced",
        "python scripts/dev/first_time_doctor.py --strict",
        "make setup-fast",
        "make setup-fast-engine",
        "make setup-refresh-python",
        "make devx-full-pass",
        "make devx-timing",
        "make disk-report",
    ],
    "docs/quickstarts/FIRST_RUN_5_MIN.md": [
        "python scripts/dev/quickstart_first_time.py --include-advanced",
        "python scripts/dev/first_time_doctor.py --strict",
        "make setup-fast",
        "make setup-fast-engine",
        "make setup-refresh-python",
        "make devx-full-pass",
        "make devx-timing",
        "make disk-report",
    ],
}

REQUIRED_MAKEFILE_TARGETS = (
    "quickstart:",
    "setup-fast:",
    "setup-fast-engine:",
    "setup-refresh-python:",
    "doctor:",
    "devx-smoke:",
    "devx-full-pass:",
    "devx-timing:",
    "disk-report:",
    "disk-prune:",
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _run_json(argv: list[str]) -> tuple[bool, dict[str, Any] | None, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=ROOT_DIR,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, None, str(exc)

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        return False, None, detail

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return False, None, f"invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return False, None, "payload is not an object"
    return True, payload, "ok"


def _check_quickstart_contract(checks: list[Check]) -> None:
    ok, payload, detail = _run_json(["python3", "scripts/dev/quickstart_first_time.py", "--json"])
    if not ok or payload is None:
        checks.append(Check("quickstart_json_parse", False, detail))
        return
    checks.append(Check("quickstart_json_parse", True, "quickstart JSON parsed"))

    required_keys: dict[str, type] = {
        "repo_root": str,
        "venv_python": str,
        "has_tui_source": bool,
        "breadboard_cli_usable": bool,
        "breadboard_cli_detail": str,
        "cli_capabilities": dict,
        "recommended_actions": list,
    }
    missing = [k for k, t in required_keys.items() if not isinstance(payload.get(k), t)]
    checks.append(
        Check(
            "quickstart_json_shape",
            not missing,
            "all required keys present with expected types" if not missing else f"bad/missing keys: {missing}",
        )
    )

    caps = payload.get("cli_capabilities", {})
    cap_missing = [
        key
        for key, kind in {
            "breadboard_on_path": bool,
            "breadboard_runnable": bool,
            "doctor_first_time_supported": bool,
            "setup_profile_supported": bool,
            "detail": str,
        }.items()
        if not isinstance(caps.get(key), kind)
    ]
    checks.append(
        Check(
            "quickstart_cli_capabilities_shape",
            not cap_missing,
            "cli_capabilities shape is valid" if not cap_missing else f"bad/missing cli capability keys: {cap_missing}",
        )
    )

    actions = payload.get("recommended_actions", [])
    steps = {row.get("step") for row in actions if isinstance(row, dict)}
    required_steps = {"bootstrap", "doctor", "onboarding_contract", "engine_run", "python_sdk_hello"}
    missing_steps = sorted(required_steps - steps)
    checks.append(
        Check(
            "quickstart_required_steps",
            not missing_steps,
            "required quickstart steps present" if not missing_steps else f"missing steps: {missing_steps}",
        )
    )

    ok_adv, payload_adv, detail_adv = _run_json(
        ["python3", "scripts/dev/quickstart_first_time.py", "--json", "--include-advanced"]
    )
    if not ok_adv or payload_adv is None:
        checks.append(Check("quickstart_advanced_json_parse", False, detail_adv))
        return
    checks.append(Check("quickstart_advanced_json_parse", True, "quickstart advanced JSON parsed"))

    actions_adv = payload_adv.get("recommended_actions", [])
    steps_adv = {row.get("step") for row in actions_adv if isinstance(row, dict)}
    required_advanced_steps = {"setup_fast_engine", "full_pass_engine", "devx_timing", "disk_report"}
    missing_adv_steps = sorted(required_advanced_steps - steps_adv)
    checks.append(
        Check(
            "quickstart_advanced_required_steps",
            not missing_adv_steps,
            "required advanced quickstart steps present"
            if not missing_adv_steps
            else f"missing advanced steps: {missing_adv_steps}",
        )
    )


def _check_doctor_contract(checks: list[Check]) -> None:
    ok, payload, detail = _run_json(
        ["python3", "scripts/dev/first_time_doctor.py", "--json", "--profile", "full"]
    )
    if not ok or payload is None:
        checks.append(Check("first_time_doctor_json_parse", False, detail))
        return
    checks.append(Check("first_time_doctor_json_parse", True, "first-time doctor JSON parsed"))

    base_shape_ok = isinstance(payload.get("ok"), bool) and isinstance(payload.get("profile"), str)
    checks.append(
        Check(
            "first_time_doctor_base_shape",
            base_shape_ok,
            "doctor top-level shape valid" if base_shape_ok else "doctor payload missing ok/profile",
        )
    )

    checks_list = payload.get("checks")
    if not isinstance(checks_list, list) or not checks_list:
        checks.append(Check("first_time_doctor_checks_array", False, "checks array missing or empty"))
        return
    checks.append(Check("first_time_doctor_checks_array", True, "checks array present"))

    bad_rows = []
    for idx, row in enumerate(checks_list):
        if not isinstance(row, dict):
            bad_rows.append(f"{idx}:not-object")
            continue
        if not isinstance(row.get("name"), str) or not isinstance(row.get("ok"), bool) or not isinstance(
            row.get("detail"), str
        ):
            bad_rows.append(str(idx))
    checks.append(
        Check(
            "first_time_doctor_check_items_shape",
            not bad_rows,
            "all doctor check rows have name/ok/detail" if not bad_rows else f"bad check rows: {bad_rows}",
        )
    )


def _check_cli_capabilities_contract(checks: list[Check]) -> None:
    ok, payload, detail = _run_json(["python3", "scripts/dev/cli_capabilities.py", "--json"])
    if not ok or payload is None:
        checks.append(Check("cli_capabilities_json_parse", False, detail))
        return
    checks.append(Check("cli_capabilities_json_parse", True, "cli capabilities JSON parsed"))

    missing = [
        key
        for key, kind in {
            "breadboard_on_path": bool,
            "breadboard_runnable": bool,
            "doctor_first_time_supported": bool,
            "setup_profile_supported": bool,
            "detail": str,
        }.items()
        if not isinstance(payload.get(key), kind)
    ]
    checks.append(
        Check(
            "cli_capabilities_shape",
            not missing,
            "cli capabilities contract valid" if not missing else f"bad/missing keys: {missing}",
        )
    )


def _check_docs_contract(checks: list[Check]) -> None:
    for relative_path, tokens in REQUIRED_DOC_COMMANDS.items():
        text = (ROOT_DIR / relative_path).read_text(encoding="utf-8")
        for token in tokens:
            ok = token in text
            checks.append(
                Check(
                    f"docs_token::{relative_path}::{token}",
                    ok,
                    "present" if ok else f"missing token: {token}",
                )
            )


def _check_makefile_contract(checks: list[Check]) -> None:
    text = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")
    for target in REQUIRED_MAKEFILE_TARGETS:
        ok = target in text
        checks.append(
            Check(
                f"makefile_target::{target}",
                ok,
                "present" if ok else f"missing make target: {target}",
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check onboarding command/docs contract drift.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any contract checks fail.")
    args = parser.parse_args()

    checks: list[Check] = []
    _check_quickstart_contract(checks)
    _check_doctor_contract(checks)
    _check_cli_capabilities_contract(checks)
    _check_docs_contract(checks)
    _check_makefile_contract(checks)

    failed = [row for row in checks if not row.ok]
    payload = {
        "ok": not failed,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": [asdict(row) for row in checks],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for row in checks:
            print(f"[{'OK' if row.ok else 'FAIL'}] {row.name}: {row.detail}")
        print(f"[onboarding-contract] {payload['total_checks'] - payload['failed_checks']}/{payload['total_checks']} passed")

    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
