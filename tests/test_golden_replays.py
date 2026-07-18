from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence
import pytest
from agentic_coder_prototype.parity import (
    EquivalenceLevel,
    build_expected_run_ir,
    build_run_ir_from_run_dir,
    compare_run_ir,
)
from agentic_coder_prototype.parity_manifest import ParityScenario, load_parity_scenarios
ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = ROOT / "scripts/replay_opencode_session.py"
LOGGING_ROOT = ROOT / "logging"
WEBFETCH_FIXTURE_SCRIPT = ROOT / "scripts" / "opencode_webfetch_fixture_server.py"
def _start_webfetch_fixture_server() -> tuple[subprocess.Popen[str], int]:
    proc = subprocess.Popen(
        [
            sys.executable,
            str(WEBFETCH_FIXTURE_SCRIPT),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    port_line = proc.stdout.readline().strip()
    try:
        port = int(port_line)
    except Exception:
        stderr = ""
        try:
            stderr = (proc.stderr.read() if proc.stderr else "")[:2000]
        except Exception:
            stderr = ""
        proc.terminate()
        proc.wait(timeout=2)
        raise RuntimeError(f"Failed to start webfetch fixture server: {port_line}\n{stderr}")
    return proc, port
def _stop_process(proc: subprocess.Popen[str]) -> None:
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            return
        try:
            proc.wait(timeout=2)
        except Exception:
            return
_SCENARIOS: Sequence[ParityScenario] = tuple(
    scenario
    for scenario in load_parity_scenarios()
    if scenario.enabled and scenario.mode == "replay"
)
@pytest.mark.slow
@pytest.mark.parametrize("scenario", _SCENARIOS, ids=[item.name for item in _SCENARIOS])
def test_golden_opencode_replays(tmp_path: Path, scenario: ParityScenario) -> None:
    workspace = tmp_path / scenario.name
    result_path = workspace / "session_result.json"
    seed = scenario.workspace_seed or scenario.golden_workspace
    if seed and seed.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed, workspace, dirs_exist_ok=True)
        seeded_state = workspace / ".breadboard"
        if seeded_state.exists():
            shutil.rmtree(seeded_state, ignore_errors=True)
    fixture_proc: subprocess.Popen[str] | None = None
    session_path = scenario.session
    if scenario.name == "opencode_webfetch_sentinel_replay":
        fixture_proc, port = _start_webfetch_fixture_server()
        patched = tmp_path / "opencode_webfetch_session_with_port.json"
        raw = scenario.session.read_text(encoding="utf-8")
        patched.write_text(raw.replace("{PORT}", str(port)), encoding="utf-8")
        session_path = patched
    args = [
        sys.executable,
        str(REPLAY_SCRIPT),
        "--config",
        str(scenario.config),
        "--session",
        str(session_path),
        "--workspace",
        str(workspace),
        "--golden-workspace",
        str(scenario.golden_workspace),
        "--result-json",
        str(result_path),
        "--parity-level",
        scenario.equivalence.value,
    ]
    if scenario.max_steps:
        args += ["--limit", str(scenario.max_steps)]
    if scenario.todo_expected:
        args += ["--todo-expected", str(scenario.todo_expected)]
    if scenario.guardrails_expected:
        args += ["--guardrail-expected", str(scenario.guardrails_expected)]
    if scenario.golden_meta:
        args += ["--parity-summary", str(scenario.golden_meta)]
    env = os.environ.copy()
    env.setdefault("RAY_SCE_SKIP_LSP", "1")
    env.setdefault("MOCK_API_KEY", "kc_parity_mock_key")
    env.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                f"Replay failed for {scenario.name} (exit {result.returncode}).\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
    finally:
        if fixture_proc is not None:
            _stop_process(fixture_proc)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    run_result = payload.get("result")
    assert isinstance(run_result, dict), f"Replay result missing result object: {result_path}"
    run_dir_value = run_result.get("run_dir") or run_result.get("logging_dir")
    assert run_dir_value, f"Replay result missing run directory: {result_path}"
    run_dir = Path(run_dir_value)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    print(f"[golden-replay] using run dir: {run_dir}")
    actual_ir = build_run_ir_from_run_dir(run_dir)
    expected_ir = build_expected_run_ir(
        scenario.golden_workspace,
        guardrail_path=scenario.guardrails_expected,
        todo_journal_path=scenario.todo_expected,
        summary_path=scenario.golden_meta,
    )
    target_level: EquivalenceLevel = scenario.equivalence
    ladder = [
        EquivalenceLevel.SEMANTIC,
        EquivalenceLevel.STRUCTURAL,
        EquivalenceLevel.NORMALIZED_TRACE,
        EquivalenceLevel.BITWISE_TRACE,
    ]
    level_reports = []
    failure_details = None
    for level in ladder:
        if ladder.index(level) > ladder.index(target_level):
            break
        mismatches = compare_run_ir(actual_ir, expected_ir, level)
        if not mismatches:
            level_reports.append(f"{level.value}: PASS")
            continue
        level_reports.append(f"{level.value}: FAIL ({len(mismatches)} issues)")
        failure_details = (level, mismatches)
        break

    print("[parity] " + " | ".join(level_reports))
    if failure_details:
        level, mismatches = failure_details
        pytest.fail(
            f"Parity mismatch at {level.value} level:\n" + "\n".join(mismatches) + f"\nRun dir: {run_dir}"
        )
