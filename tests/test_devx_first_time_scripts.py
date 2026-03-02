from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        list(args),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_quickstart_first_time_json_contract() -> None:
    proc = _run("python3", "scripts/dev/quickstart_first_time.py", "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert isinstance(payload.get("repo_root"), str)
    assert isinstance(payload.get("venv_python"), str)
    assert isinstance(payload.get("has_tui_source"), bool)
    assert isinstance(payload.get("breadboard_cli_usable"), bool)
    caps = payload.get("cli_capabilities")
    assert isinstance(caps, dict)
    assert isinstance(caps.get("breadboard_on_path"), bool)
    assert isinstance(caps.get("breadboard_runnable"), bool)
    assert isinstance(caps.get("doctor_first_time_supported"), bool)
    assert isinstance(caps.get("setup_profile_supported"), bool)
    actions = payload.get("recommended_actions")
    assert isinstance(actions, list) and actions
    assert any(isinstance(a, dict) and a.get("step") == "doctor" for a in actions)
    assert any(isinstance(a, dict) and a.get("step") == "onboarding_contract" for a in actions)


def test_quickstart_first_time_forced_cli_broken_includes_fix_step() -> None:
    proc = _run(
        "python3",
        "scripts/dev/quickstart_first_time.py",
        "--json",
        env_overrides={"BREADBOARD_QUICKSTART_ASSUME_CLI": "broken"},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload.get("breadboard_cli_usable") is False
    actions = payload.get("recommended_actions")
    assert isinstance(actions, list)
    if payload.get("has_tui_source"):
        assert any(isinstance(a, dict) and a.get("step") == "cli_fix" for a in actions)


def test_quickstart_first_time_include_advanced_contains_engine_full_pass_step() -> None:
    proc = _run("python3", "scripts/dev/quickstart_first_time.py", "--json", "--include-advanced")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    actions = payload.get("recommended_actions")
    assert isinstance(actions, list)
    assert any(isinstance(a, dict) and a.get("step") == "setup_fast_engine" for a in actions)
    assert any(isinstance(a, dict) and a.get("step") == "full_pass_engine" for a in actions)
    assert any(isinstance(a, dict) and a.get("step") == "devx_timing" for a in actions)
    assert any(isinstance(a, dict) and a.get("step") == "disk_report" for a in actions)


def test_first_time_doctor_json_contract() -> None:
    proc = _run("python3", "scripts/dev/first_time_doctor.py", "--json", "--profile", "full")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert payload.get("profile") == "full"
    assert isinstance(payload.get("effective_profile"), str)
    checks = payload.get("checks")
    assert isinstance(checks, list) and checks
    assert all(isinstance(item, dict) and "name" in item and "ok" in item for item in checks)


def test_cli_capabilities_json_contract() -> None:
    proc = _run("python3", "scripts/dev/cli_capabilities.py", "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert isinstance(payload.get("breadboard_on_path"), bool)
    assert isinstance(payload.get("breadboard_runnable"), bool)
    assert isinstance(payload.get("doctor_first_time_supported"), bool)
    assert isinstance(payload.get("setup_profile_supported"), bool)
    assert isinstance(payload.get("detail"), str)


def test_onboarding_contract_drift_checker_json_contract() -> None:
    proc = _run("python3", "scripts/dev/check_onboarding_contract_drift.py", "--json", "--strict")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert payload.get("ok") is True
    assert isinstance(payload.get("total_checks"), int)
    assert isinstance(payload.get("failed_checks"), int)
    checks = payload.get("checks")
    assert isinstance(checks, list) and checks
    assert all(isinstance(item, dict) and "name" in item and "ok" in item and "detail" in item for item in checks)
