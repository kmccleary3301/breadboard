"""pi stock responsibility owner."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List
from .pi_target import get_target_overlay_argv
from .pi_environment_guards import begin_pi_environment_observation, finish_pi_environment_observation

class RpcSettleState:
    """Follow stock events and use get_state as the no-compaction barrier.

    Pi emits agent_end before checking compaction: 0.73.1
    dist/core/agent-session.js:294-341; 0.57.1:204-251.
    With the target's --no-extensions overlay, the check emits its start before
    another RPC command can run. get_state reports isCompacting/isStreaming:
    0.73.1 dist/modes/rpc/rpc-mode.js:334-349; 0.57.1:282-297.
    Start/end names: 0.73.1 dist/core/agent-session.js:1452,1562;
    0.57.1 dist/core/agent-session.js:1401,1480.
    """

    def __init__(self, version: str) -> None:
        self.start, self.end = {
            "0.73.1": ("compaction_start", "compaction_end"),
            "0.57.1": ("auto_compaction_start", "auto_compaction_end"),
        }[version]
        self.agent_ended = False
        self.compacting = False
        self.retry_pending = False
        self.barrier_id = ""
        self.generation = 0

    def observe(self, event: Dict[str, Any]) -> bool:
        kind = event["type"]
        if kind == "agent_start":
            self.agent_ended = False
        elif kind == "agent_end":
            self.agent_ended = True
            self.retry_pending = False
            self.generation += 1
            self.barrier_id = f"settle-{self.generation}"
        elif kind == self.start:
            self.compacting = True
        elif kind == self.end:
            self.compacting = False
            self.retry_pending = event["willRetry"]
            # Repeated-overflow termination emits an end without a start:
            # 0.73.1 agent-session.js:1399-1407; 0.57.1:1349-1356.
            return self.agent_ended and not self.retry_pending
        elif kind == "response" and event.get("id") == self.barrier_id:
            if not event["success"]:
                raise RuntimeError(event["error"])
            state = event["data"]
            return (
                self.agent_ended and not self.compacting and not self.retry_pending
                and not state["isStreaming"] and not state["isCompacting"]
            )
        elif kind == "response" and not event["success"]:
            raise RuntimeError(event["error"])
        return False

def run_stock_pi_rpc(
    pi_cli: Path,
    mock_base_url: str,
    workdir: Path,
    prompt: str,
    target_id: str = "pi-r3@0.73.1",
    context_window: int = 16000,
    reserve_tokens: int = 1000,
    keep_recent_tokens: int = 2000,
    timeout: float = 60.0,
    environment_observation: Dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run stock Pi in RPC mode against the mock provider."""
    import threading

    agent_dir = workdir / ".pi_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    target_overlay_argv = get_target_overlay_argv(target_id)
    if "--no-extensions" not in target_overlay_argv:
        raise ValueError("RPC settle barrier requires the target's --no-extensions overlay")
    # Filter out --mode from overlay argv to allow --mode rpc
    filtered_overlay_argv: List[str] = []
    skip_next = False
    for arg in target_overlay_argv:
        if skip_next:
            skip_next = False
            continue
        if arg == "--mode":
            skip_next = True
            continue
        if arg.startswith("--mode="):
            continue
        filtered_overlay_argv.append(arg)

    # Setup models.json
    compat_config = {
        "supportsStore": True,
        "supportsDeveloperRole": True,
        "supportsUsageInStreaming": True,
        "maxTokensField": "max_tokens",
        "supportsStrictMode": False,
    }
    if "0.57.1" in target_id:
        compat_config["supportsStore"] = False
        compat_config["supportsDeveloperRole"] = False
        compat_config["supportsReasoningEffort"] = False
        compat_config["supportsStrictMode"] = True

    models_config = {
        "providers": {
            "mock-provider": {
                "name": "Mock Provider",
                "baseUrl": f"{mock_base_url}/v1",
                "apiKey": "mock-api-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": "model-a",
                        "name": "model-a",
                        "contextWindow": context_window,
                        "maxTokens": 2048,
                        "input": ["text"],
                        "compat": compat_config,
                    }
                ],
            }
        }
    }
    (agent_dir / "models.json").write_text(json.dumps(models_config, indent=2), encoding="utf-8")

    settings_config = {
        "images": {
            "blockImages": True,
        },
        "compaction": {
            "enabled": True,
            "reserveTokens": reserve_tokens,
            "keepRecentTokens": keep_recent_tokens,
        },
    }
    (agent_dir / "settings.json").write_text(json.dumps(settings_config, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["PI_CODING_AGENT_DIR"] = str(agent_dir)
    env["PI_OFFLINE"] = "1"
    env["MOCK_API_KEY"] = "mock-api-key"

    cmd = [
        "node",
        str(pi_cli),
        "--mode",
        "rpc",
        "--provider",
        "mock-provider",
        "--model",
        "model-a",
        *filtered_overlay_argv,
    ]

    if environment_observation is not None:
        environment_observation.update(begin_pi_environment_observation(
            target_id.split("@", 1)[1], workdir, native=False,
        ))

    proc = subprocess.Popen(
        cmd,
        cwd=str(workdir),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout_lines: List[str] = []
    reader_errors: List[Exception] = []
    stop_event = threading.Event()
    settled = threading.Event()
    settle = RpcSettleState(target_id.split("@", 1)[1])
    settlement_event: Dict[str, Any] | None = None
    assert proc.stdin is not None

    def reader() -> None:
        nonlocal settlement_event
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                stdout_lines.append(line)
                event = json.loads(line)
                if settle.observe(event):
                    settlement_event = event
                    settled.set()
                    stop_event.set()
                    return
                if event["type"] == "agent_end":
                    proc.stdin.write(json.dumps({
                        "type": "get_state", "id": settle.barrier_id,
                    }) + "\n")
                    proc.stdin.flush()
            raise RuntimeError("Stock Pi RPC stdout closed before settling")
        except Exception as exc:
            reader_errors.append(exc)
            stop_event.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        proc.stdin.write(json.dumps({"type": "prompt", "id": "p1", "message": prompt}) + "\n")
        proc.stdin.flush()
        if not stop_event.wait(timeout=timeout):
            raise subprocess.TimeoutExpired(cmd, timeout, output="".join(stdout_lines))
    finally:
        cleanup: Dict[str, Any] = {
            "pid": proc.pid, "controller": "pi_stock.run_stock_pi_rpc",
            "settled": settled.is_set(), "agent_ended": settle.agent_ended,
            "reader_error_count": len(reader_errors),
            "settlement_before_cleanup": settled.is_set() and settlement_event is not None,
            "settlement_event": settlement_event,
            "process_running_before_terminate": False,
            "terminate_requested": False, "requested_signal": None,
            "kill_requested": False, "wait_completed": False, "wait_returncode": None,
        }
        if environment_observation is not None:
            environment_observation["rpc_cleanup"] = cleanup
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        cleanup["process_running_before_terminate"] = proc.poll() is None
        if cleanup["process_running_before_terminate"]:
            cleanup["terminate_requested"] = True
            cleanup["requested_signal"] = "SIGTERM"
            try:
                proc.terminate()
            except OSError:
                if proc.poll() is None:
                    raise
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cleanup["kill_requested"] = True
            proc.kill()
            proc.wait()
        cleanup["wait_completed"] = True
        cleanup["wait_returncode"] = proc.returncode
        thread.join(timeout=3)
        if environment_observation is not None:
            finish_pi_environment_observation(environment_observation)
    assert proc.stderr is not None
    stderr = proc.stderr.read()
    if reader_errors:
        raise reader_errors[0]
    if not settled.is_set():
        raise RuntimeError(f"Stock Pi RPC did not settle: {stderr}")
    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode,
        stdout="".join(stdout_lines), stderr=stderr,
    )

def run_stock_pi_print(
    pi_cli: Path,
    mock_base_url: str,
    workdir: Path,
    prompt: str,
    target_id: str = "pi-r3@0.73.1",
    context_window: int = 16000,
    reserve_tokens: int = 1000,
    keep_recent_tokens: int = 2000,
    timeout: float = 60.0,
    environment_observation: Dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run stock Pi in print mode against the mock provider (kept as reference)."""
    agent_dir = workdir / ".pi_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    target_overlay_argv = get_target_overlay_argv(target_id)
    models_config = {
        "providers": {
            "mock-provider": {
                "name": "Mock Provider",
                "baseUrl": f"{mock_base_url}/v1",
                "apiKey": "mock-api-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": "model-a",
                        "name": "model-a",
                        "contextWindow": context_window,
                        "maxTokens": 2048,
                        "input": ["text"],
                        "compat": {
                            "supportsStore": True,
                            "supportsDeveloperRole": True,
                            "supportsUsageInStreaming": True,
                            "maxTokensField": "max_tokens",
                            "supportsStrictMode": False,
                        },
                    }
                ],
            }
        }
    }
    (agent_dir / "models.json").write_text(json.dumps(models_config, indent=2), encoding="utf-8")

    settings_config = {
        "images": {
            "blockImages": True,
        },
        "compaction": {
            "enabled": True,
            "reserveTokens": reserve_tokens,
            "keepRecentTokens": keep_recent_tokens,
        },
    }
    (agent_dir / "settings.json").write_text(json.dumps(settings_config, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["PI_CODING_AGENT_DIR"] = str(agent_dir)
    env["PI_OFFLINE"] = "1"
    env["MOCK_API_KEY"] = "mock-api-key"

    cmd = [
        "node",
        str(pi_cli),
        "--provider",
        "mock-provider",
        "--model",
        "model-a",
        *target_overlay_argv,
        "-p",
        prompt,
    ]

    if environment_observation is not None:
        environment_observation.update(begin_pi_environment_observation(
            target_id.split("@", 1)[1], workdir, native=False,
        ))
    try:
        return subprocess.run(
            cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        if environment_observation is not None:
            finish_pi_environment_observation(environment_observation)

def run_stock_pi(
    pi_cli: Path,
    mock_base_url: str,
    workdir: Path,
    prompt: str,
    target_id: str = "pi-r3@0.73.1",
    context_window: int = 16000,
    reserve_tokens: int = 1000,
    keep_recent_tokens: int = 2000,
    runner: str = "rpc",
    timeout: float = 60.0,
    environment_observation: Dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run stock Pi against mock provider via RPC mode (parity) or print mode (reference)."""
    if runner == "print":
        return run_stock_pi_print(
            pi_cli, mock_base_url, workdir, prompt,
            target_id=target_id, context_window=context_window,
            reserve_tokens=reserve_tokens, keep_recent_tokens=keep_recent_tokens,
            timeout=timeout, environment_observation=environment_observation,
        )
    return run_stock_pi_rpc(
        pi_cli, mock_base_url, workdir, prompt,
        target_id=target_id, context_window=context_window,
        reserve_tokens=reserve_tokens, keep_recent_tokens=keep_recent_tokens,
        timeout=timeout, environment_observation=environment_observation,
    )
