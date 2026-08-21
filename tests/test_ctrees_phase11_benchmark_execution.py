from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.ctrees import phase11_benchmark_execution
from agentic_coder_prototype.ctrees import candidate_a_live_contract, practical_baseline_contract
from agentic_coder_prototype.ctrees.phase11_benchmark_execution import Phase11BenchmarkExecutionRequest
from agentic_coder_prototype.ctrees.candidate_a_live_contract import execute_phase11_candidate_a_live_cell
from agentic_coder_prototype.ctrees.practical_baseline_contract import execute_phase11_practical_baseline_cell


_CASES = [
    ("practical", "practical_baseline_flagship", "phase11_practical_baseline_execution_v1"),
    ("candidate", "candidate_a_flagship", "phase11_candidate_a_live_execution_v1"),
]


def _execute(case: str, *, cell_id: str, out_root: Path, dry_run: bool) -> dict[str, Any]:
    if case == "practical":
        return execute_phase11_practical_baseline_cell(
            cell_id=cell_id,
            out_root=out_root,
            dry_run=dry_run,
        )
    return execute_phase11_candidate_a_live_cell(
        cell_id=cell_id,
        tasks_payload={"schema_version": "test_tasks_v1", "tasks": []},
        out_root=out_root,
        dry_run=dry_run,
    )


@pytest.mark.parametrize(
    ("contract_module", "execute_wrapper", "cell_id"),
    [
        (
            practical_baseline_contract,
            execute_phase11_practical_baseline_cell,
            "practical_baseline_flagship",
        ),
        (
            candidate_a_live_contract,
            execute_phase11_candidate_a_live_cell,
            "candidate_a_flagship",
        ),
    ],
)
def test_phase11_wrappers_delegate_through_typed_execution_request(
    contract_module: Any,
    execute_wrapper: Any,
    cell_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: Phase11BenchmarkExecutionRequest) -> dict[str, Any]:
        captured["request"] = request
        return {"status": "captured"}

    monkeypatch.setattr(contract_module, "execute_phase11_benchmark_cell", _capture)
    kwargs: dict[str, Any] = {
        "cell_id": cell_id,
        "out_root": tmp_path,
        "dry_run": True,
    }
    if contract_module is candidate_a_live_contract:
        kwargs["tasks_payload"] = {"schema_version": "test_tasks_v1", "tasks": []}

    assert execute_wrapper(**kwargs) == {"status": "captured"}
    request = captured["request"]
    assert isinstance(request, Phase11BenchmarkExecutionRequest)
    assert request.cell["cell_id"] == cell_id
    assert request.out_root == tmp_path
    assert request.dry_run is True
    assert request.runner_script.name == "phase11_benchmark_runner_stub.py"


@pytest.mark.parametrize(("case", "cell_id", "schema_version"), _CASES)
def test_phase11_wrappers_preserve_missing_environment_block(
    case: str,
    cell_id: str,
    schema_version: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _unexpected_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("missing provider environment must not launch the runner")

    monkeypatch.setattr(phase11_benchmark_execution.subprocess, "run", _unexpected_run)
    payload = _execute(case, cell_id=cell_id, out_root=tmp_path, dry_run=False)

    assert payload == {
        "schema_version": schema_version,
        "cell_id": cell_id,
        "model_tier": "flagship",
        "status": "provider_blocked_missing_env",
        "missing_env": ["OPENAI_API_KEY"],
        "tasks_path": str(tmp_path / f"{cell_id}_tasks.json"),
        "out_path": str(tmp_path / f"{cell_id}_results.json"),
        "workspace_root": str(
            Path(phase11_benchmark_execution.__file__).resolve().parents[2]
            / "tmp"
            / ("phase11_practical_baseline_runs" if case == "practical" else "phase11_candidate_a_live_runs")
            / cell_id
        ),
        "command": payload["command"],
    }
    assert payload["command"][0] == "python"
    assert payload["command"][1].endswith("scripts/archive/phase11_benchmark_runner_stub.py")
    assert "--dry-run" not in payload["command"]


@pytest.mark.parametrize(("case", "cell_id", "schema_version"), _CASES)
def test_phase11_wrappers_preserve_runner_failure_and_malformed_result(
    case: str,
    cell_id: str,
    schema_version: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PYTHONPATH", "existing-pythonpath")
    captured: dict[str, Any] = {}

    def _run(
        cmd: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured.update(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "capture_output": capture_output,
                "text": text,
            }
        )
        out_path = Path(cmd[cmd.index("--out") + 1])
        out_path.write_text("{malformed", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 7, stdout="runner stdout", stderr="runner stderr")

    monkeypatch.setattr(phase11_benchmark_execution.subprocess, "run", _run)
    payload = _execute(case, cell_id=cell_id, out_root=tmp_path, dry_run=False)
    repo_root = Path(phase11_benchmark_execution.__file__).resolve().parents[2]

    assert payload["schema_version"] == schema_version
    assert payload["status"] == "runner_failed"
    assert payload["returncode"] == 7
    assert payload["stdout"] == "runner stdout"
    assert payload["stderr"] == "runner stderr"
    assert payload["result"] is None
    assert payload["missing_env"] == []
    assert captured["cmd"] == payload["command"]
    assert captured["cwd"] == str(repo_root)
    assert captured["env"]["PYTHONPATH"] == f"{repo_root}:existing-pythonpath"
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert payload["command"][0] == "python"
    assert payload["command"][payload["command"].index("--tasks") + 1] == payload["tasks_path"]
    assert payload["command"][payload["command"].index("--out") + 1] == payload["out_path"]
    assert payload["command"][payload["command"].index("--workspace-root") + 1] == payload["workspace_root"]
    assert "--dry-run" not in payload["command"]
    if case == "practical":
        assert payload["task_pack"] == "phase11_downstream_pilot_v1"
    else:
        assert "task_pack" not in payload
