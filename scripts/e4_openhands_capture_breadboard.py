#!/usr/bin/env python3
"""Capture one OpenHands 1.47.0 BreadBoard public replay case."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import secrets
import shutil
import socket
import subprocess
import sys
import time
from typing import Any

HERE = Path(__file__).resolve().parent
CASES = HERE / "openhands_capture_cases.json"
RECEIVER = HERE / "openhands_capture_receiver.py"
COMPOSE = HERE / "openhands_sif_compose.py"
SIF_SHA = "923d35097522343fba398665943a0087951424b5980be7c615e83e4f849d059a"
OPERATORS = Path("/opt/breadboard-operators")
PATCH_DIAGNOSTIC: dict[str, object] = {}


def load_compose() -> Any:
    return runpy.run_path(str(COMPOSE), run_name="openhands_capture_compose")["compose"]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def run_checked(argv: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=timeout)


def replace_workspace(value: Any, root: re.Pattern[str], changed: list[bool]) -> Any:
    if isinstance(value, str):
        replaced = root.sub("<WORKSPACE>", value)
        if replaced != value:
            changed[0] = True
        return replaced
    if isinstance(value, dict):
        return {key: replace_workspace(item, root, changed) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_workspace(item, root, changed) for item in value]
    return value


def normalize_history(history: list[Any], normalizations: list[str]) -> list[Any]:
    timestamp_changed = False
    traceback_changed = False
    for message in history:
        if not isinstance(message, dict):
            continue
        extra = message.get("extra")
        if not isinstance(extra, dict):
            continue
        timestamp = extra.get("timestamp")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            extra["timestamp"] = "<TIMESTAMP>"
            timestamp_changed = True
        traceback_value = extra.get("traceback")
        if isinstance(traceback_value, str) and traceback_value.startswith("Traceback (most recent call last):"):
            extra["traceback"] = "<TRACEBACK>"
            traceback_changed = True
    if timestamp_changed:
        normalizations.append("timestamp:<TIMESTAMP>")
    if traceback_changed:
        normalizations.append("traceback:<TRACEBACK>")
    return history


def load_case(path: Path, case_id: str) -> dict[str, Any]:
    payload = json.loads(path.read_bytes())
    case = dict(payload["cases"][case_id])
    case.pop("schema_version", None)
    if case.get("case_id") != case_id:
        raise ValueError("scenario case id mismatch")
    return case


def start_receiver(scenario: Path, output: Path, credential: Path) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", str(RECEIVER), "--scenario", str(scenario), "--credential-file", str(credential), "--output", str(output)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while not (output / "ready.json").exists():
        if process.poll() is not None:
            details = process.stdout.read().decode("utf-8", "replace") if process.stdout else ""
            raise RuntimeError(f"receiver failed before ready: {details}")
        if time.monotonic() >= deadline:
            process.kill()
            raise TimeoutError("receiver readiness deadline")
        time.sleep(0.02)
    return process, json.loads((output / "ready.json").read_bytes())


def stop_receiver(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def process_identity(pid: int) -> dict[str, Any] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "parent": int(fields[1]), "start_time": fields[19]}
    except (FileNotFoundError, ValueError):
        return None


SHARED_FAULT_EFFECT = b"effect-retained\n"


def cancel_active_tool(command: list[str], workspace_root: Path, stdout: Any, stderr: Any, env: dict[str, str]) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    """SIGINT the headless run once the tool's effect is observable, while it still sleeps.

    The case command writes the effect and then sleeps 25 s (under Mini's 30 s timeout), so
    the interrupt lands after the effect and before any observation can be committed.
    Returns the process and what the operator observed at the moment it signalled.
    """
    process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True, env=env)
    started = time.monotonic()
    deadline = started + 90
    pattern = "workspace-*/repository/shared_fault_effect.txt"
    while True:
        effects = [path for path in workspace_root.glob(pattern) if path.is_file()]
        effect = effects[0].read_bytes() if effects else None
        if effect == SHARED_FAULT_EFFECT:
            break
        if process.poll() is not None:
            raise RuntimeError("headless exited before the tool effect was observed")
        if time.monotonic() >= deadline:
            raise TimeoutError("tool effect was not observed before the cancellation deadline")
        time.sleep(0.05)
    running = process.poll() is None
    process.send_signal(signal.SIGINT)
    observed = {"effect_sha256_before_signal": digest_bytes(effect), "headless_running_at_signal": running, "signal": "SIGINT", "signal_after_seconds": round(time.monotonic() - started, 3)}
    process.wait(timeout=90)
    return process, observed


def compose_spec(case: dict[str, Any], args: argparse.Namespace, root: Path, ready: dict[str, Any], credential: Path, native_sha: str, base_commit: str) -> dict[str, Any]:
    native_root = Path("/opt/breadboard-native-tools")
    native_manifest = Path("/opt/breadboard-native-manifest.json")
    metadata = native_root.stat()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        control_port = reservation.getsockname()[1]
    provider = {
        "model": ready["wire_model"], "authority_model_id": ready["identity"].get("model_id", "bb-scripted-openhands-capture"),
        "credential_handle": "launch-endpoint", "context_window": 131072, "max_output_tokens": 2048,
        "timeout_seconds": 45.0, "sampling": {"temperature": 0},
        "capabilities": {"supports_non_streaming": True, "supports_streaming": False, "supports_tools": True, "supports_max_completion_tokens": True},
        "compatibility": {}, "request_policy": {"mode": "non_streaming", "include_usage": False, "max_token_field": "max_completion_tokens", "strict_tools": None, "enable_thinking": None},
    }
    return {
        "schema_version": "bb.e4.openhands-sif-composition-input.v1",
        "output_root": str(root / "episode"), "provider": provider,
        "public_manifest_sha256": args.public_manifest_sha256,
        "policy_model_identity": ready["identity"],
        "policy_capabilities": {"responses_protocol": "responses-v1", "modalities": ["text"], "tool_calling": True, "parallel_tool_calls": False, "token_ids": False, "token_logprobs": False, "routing_metadata": False, "cancellation": True, "max_context_tokens": 131072, "max_output_tokens": 2048, "policy_slot_count": 1, "request_features": ["max_completion_tokens", "non_streaming", "temperature"]},
        "native_tool_adapter": {"adapter_id": "openhands-sdk.local.v1.47.0", "tool_ids": ["file_editor", "finish", "task_tracker", "terminal", "think"], "runtime_root": {"authority_id": "openhands-147-native-root", "path": str(native_root), "device": metadata.st_dev, "inode": metadata.st_ino, "owner_uid": metadata.st_uid, "mode": f"{metadata.st_mode & 0o7777:04o}"}, "manifest_ref": {"path": str(native_manifest), "sha256": native_sha, "size_bytes": native_manifest.stat().st_size, "media_type": "application/vnd.breadboard.native-tool-source+json;version=1"}, "executable_relative_path": "python/bin/python3.12", "entrypoint_relative_path": "openhands_worker.py"},
        "episode_id": "openhands-capture-" + args.case_id + "-" + root.name,
        "credential_file": str(credential), "provider_base_url": ready["base_url"], "task_image_digest": "sha256:" + SIF_SHA,
        "integrity_checker": str(OPERATORS / "legacy_pi_snapshot_integrity.py"), "repository_root": str(args.repo), "base_commit": base_commit,
        "prompt": case["task"], "task_id": "openhands-capture-" + args.case_id, "control_port": control_port,
    }


def read_transcript(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def committed_history(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for event in ledger.get("events", []):
        if "history_digest" in event:
            history.extend(event.get("messages", []))
    return history


def patch_effects(patch: Path, repo: Path, base_commit: str, paths: list[str], scratch: Path) -> dict[str, str | None]:
    PATCH_DIAGNOSTIC.clear()
    header_lines = [line.decode("utf-8", "replace") for line in patch.read_bytes().splitlines() if line.startswith(b"diff --git ")] if patch.is_file() else []
    captured_headers: list[str] = []
    header_bytes = 0
    for header in header_lines[:200]:
        if header_bytes + len(header.encode("utf-8")) > 16 * 1024:
            break
        captured_headers.append(header)
        header_bytes += len(header.encode("utf-8"))
    PATCH_DIAGNOSTIC["patch_headers"] = captured_headers
    PATCH_DIAGNOSTIC["patch_header_count"] = len(header_lines)
    if paths and patch.is_file() and patch.stat().st_size:
        run_checked(["git", "-c", "core.hooksPath=/dev/null", "clone", "--shared", "--no-checkout", str(repo), str(scratch)], timeout=30)
        run_checked(["git", "-C", str(scratch), "checkout", "--detach", base_commit], timeout=30)
        apply_command = ["git", "-C", str(scratch), "apply", "--binary", str(patch)]
        applied = subprocess.run(apply_command, check=False, capture_output=True, timeout=30)
        PATCH_DIAGNOSTIC["apply"] = {
            "command": apply_command,
            "returncode": applied.returncode,
            "stderr": applied.stderr.decode("utf-8", "replace")[:16 * 1024],
        }
        if applied.returncode:
            raise subprocess.CalledProcessError(applied.returncode, apply_command, stderr=applied.stderr)
    effects: dict[str, str | None] = {}
    for relative in paths:
        candidate = scratch / relative
        effects[relative] = sha(candidate) if candidate.is_file() else None
    return effects


def external_raw_output_bytes(case: dict[str, Any]) -> int:
    """Count the first tool command's stdout+stderr bytes outside BreadBoard."""
    command = json.loads(case["steps"][0]["tool_calls"][0]["arguments"])["command"]
    with tempfile.TemporaryDirectory() as scratch:
        completed = subprocess.run(["/bin/bash", "-c", command], cwd=scratch, capture_output=True, timeout=60, check=False)
    return len(completed.stdout) + len(completed.stderr)


def observed_controls(case: dict[str, Any], process: Any, result: dict[str, Any], history: list[Any], cancel_observation: dict[str, Any] | None, http_attempts: int, sealed_effects: dict[str, Any]) -> dict[str, Any]:
    """Operator-recorded BreadBoard-only control facts; oracles may read only these.

    Independent observations: process exit, receiver-counted HTTP attempts, the
    operator-applied sealed patch effects, external byte counts and the cancel
    trigger. BreadBoard's published result.json and committed ledger are read as
    its consumer-visible outputs.
    """
    terminal = result.get("terminal") or {}
    cleanup = result.get("cleanup") or {}
    inventory = result.get("cleanup_inventory") or {}
    controls: dict[str, Any] = {
        "headless_returncode": None if process is None else process.returncode,
        "terminal": {key: terminal.get(key) for key in ("status", "run_failure", "primary_failure")},
        "cleanup": {"disposition": cleanup.get("disposition"), "receipt_state": (cleanup.get("receipt") or {}).get("state")},
        "leaked_resources": sum(len(value) for value in inventory.values() if isinstance(value, list)) + int(inventory.get("broker_descriptor_count") or 0),
        "patch_available": (result.get("patch") or {}).get("available"),
        "history_roles": [message.get("role") for message in history if isinstance(message, dict)],
        "http_attempts": http_attempts,
        "sealed_effects": sealed_effects,
    }
    if case["case_id"] == "raw_cap_over_limit":
        controls["external_raw_output_bytes"] = external_raw_output_bytes(case)
    if case["case_id"] == "shared_control_fault":
        controls["cancel_trigger"] = cancel_observation
    return controls


def capture(args: argparse.Namespace) -> dict[str, Any]:
    case = load_case(args.scenario_file, args.case_id)
    if args.repo is None:
        args.repo = Path("/testbed")
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    base_commit = args.base_commit or case.get("base_commit") or run_checked(["git", "-C", str(args.repo), "rev-parse", "HEAD"], timeout=10).stdout.strip()
    scenario = dict(case)
    scenario_path = output / "scenario.json"
    write_json(scenario_path, scenario)
    credential = output / "credential"
    credential.write_text("mini-capture-credential-" + secrets.token_urlsafe(32), encoding="utf-8")
    credential.chmod(0o400)
    receiver_output = output / "receiver"
    receiver, ready = start_receiver(scenario_path, receiver_output, credential)
    root = output / "bb"
    root.mkdir(mode=0o700)
    root = output / "bb"
    native_sha = args.native_manifest_sha256
    if native_sha and not native_sha.startswith("sha256:"):
        native_sha = "sha256:" + native_sha
    if not native_sha:
        raise ValueError("--native-manifest-sha256 is required in the installed replay")
    bb_home = output / "breadboard-home"
    bb_home.mkdir(mode=0o700, exist_ok=True)
    os.environ["HOME"] = str(bb_home)
    os.environ["XDG_CONFIG_HOME"] = str(bb_home / "config")
    os.environ["XDG_CACHE_HOME"] = str(bb_home / "cache")
    started = time.monotonic()
    launch: dict[str, Any] = {}
    process: subprocess.CompletedProcess[str] | subprocess.Popen[bytes] | None = None
    run_error: BaseException | None = None
    run_traceback: str | None = None
    cancel_observation: dict[str, Any] | None = None
    try:
        input_path = output / "composition-input.json"
        write_json(input_path, compose_spec(case, args, root, ready, credential, native_sha, base_commit))
        compose = load_compose()
        launch = compose(input_path)
        launch["process_env"] = {"LITELLM_LOCAL_MODEL_COST_MAP": "True"}
        write_json(output / "launch.json", launch)
        command = ["/opt/breadboard-public/venv/bin/python", "-I", "-B", "-m", "breadboard.rl.harness", "run", "--request", launch["request"], "--composition-ref", launch["composition_ref"], "--provider-credential-file", "launch-endpoint=" + str(credential), "--provider-route-file", "launch-endpoint=" + launch["provider_route"], "--repository-base-commit", launch["repository_snapshot_digest"] + "=" + base_commit]
        for handle, path in launch["secret_files"].items():
            command.extend(["--secret-file", handle + "=" + path])
        write_json(output / "command.json", command)
        headless_env = os.environ.copy()
        headless_env.update(launch["process_env"])
        stdout_path, stderr_path = output / "headless.stdout", output / "headless.stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            if args.case_id == "shared_control_fault":
                process, cancel_observation = cancel_active_tool(command, root / "episode" / "workspace", stdout, stderr, headless_env)
            else:
                process = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=120, text=False, env=headless_env)
        write_json(output / "headless-process-exit.json", {"returncode": process.returncode})
    except BaseException as exc:
        run_error = exc
        run_traceback = traceback.format_exc()
    finally:
        stop_receiver(receiver)
        credential.unlink(missing_ok=True)
    transcript = read_transcript(receiver_output / "http-transcript.jsonl")
    ledger_path = Path(launch.get("outputs", "")) / "events.jsonl" if launch else Path()
    ledger: dict[str, Any] = json.loads(ledger_path.read_bytes()) if ledger_path.is_file() else {"events": []}
    result_path = Path(launch.get("outputs", "")) / "result.json" if launch else Path()
    result = json.loads(result_path.read_bytes()) if result_path.is_file() else {}
    history = committed_history(ledger)
    status = None
    if history and isinstance(history[-1], dict) and history[-1].get("role") == "exit":
        status = history[-1].get("extra", {}).get("exit_status")
    if status is None and result.get("terminal"):
        failure = result["terminal"].get("primary_failure")
        status = failure if isinstance(failure, str) else (failure or {}).get("category") or result["terminal"].get("status")
    episode_status: list[dict[str, object]] = []
    effects = patch_effects(Path(launch.get("outputs", "")) / "patch.diff" if launch else Path(), args.repo, base_commit, case.get("probe_paths", []), output / "effect-workspace")
    shutil.rmtree(output / "effect-workspace", ignore_errors=True)

    def ledger_events(value: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if value.get("kind") in {"ActionEvent", "ObservationEvent", "AgentErrorEvent", "ConversationErrorEvent"}:
                found.append(value)
            for child in value.values():
                found.extend(ledger_events(child))
        elif isinstance(value, list):
            for child in value:
                found.extend(ledger_events(child))
        return found

    events = ledger_events(ledger)
    workspace_roots = (str(root / "episode" / "workspace"), str(args.repo), "/testbed")

    def canonicalize(value: Any) -> Any:
        if isinstance(value, str):
            for prefix in workspace_roots:
                if value == prefix:
                    return "<WORKSPACE>"
                if value.startswith(prefix + "/"):
                    return "<WORKSPACE>" + value[len(prefix):]
            return value
        if isinstance(value, dict):
            return {key: canonicalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonicalize(item) for item in value]
        return value

    requests = []
    for index, item in enumerate(transcript):
        request = item.get("request", item)
        body = request.get("body") if isinstance(request, dict) else None
        response = item.get("response") if isinstance(item, dict) else None
        if isinstance(body, dict):
            requests.append({"index": item.get("index", index), "body": canonicalize(body), "response": canonicalize(response)})
    tool_calls = []
    observations = []
    for event in events:
        if event.get("kind") == "ActionEvent":
            action = event.get("action")
            tool_calls.append({"tool_name": event.get("tool_name"), "arguments": canonicalize(action), "security_risk": event.get("security_risk")})
        elif event.get("kind") == "ObservationEvent":
            observation = event.get("observation")
            observations.append({"event_kind": "ObservationEvent", "tool_name": event.get("tool_name"), "is_error": bool(isinstance(observation, dict) and observation.get("is_error", False)), "result": canonicalize(observation)})
        elif event.get("kind") in {"AgentErrorEvent", "ConversationErrorEvent"}:
            observations.append({"event_kind": event["kind"], "tool_name": event.get("tool_name"), "is_error": True, "error_text": event.get("error") or event.get("detail") or event.get("code"), "classification": event.get("classification")})
    final_stop = None
    if requests:
        choices = (requests[-1].get("response") or {}).get("choices", [])
        if choices:
            final_stop = choices[-1].get("finish_reason")
    termination_kind = "finished" if status in {"FINISHED", "SUBMITTED", "ASSISTANT_COMPLETE", "succeeded"} else "error" if status in {"ERROR", "failed"} else str(status or "unknown")
    trace = {"schema_version": "bb.e4.openhands-sdk-trace.v1", "case_id": args.case_id, "requests": requests, "tool_calls": tool_calls, "observations": observations, "file_effects": effects, "termination": {"kind": termination_kind, "native_stop_reason": final_stop}, "request_count": len(requests), "normalizations": ["workspace:<WORKSPACE>"]}
    shutil.rmtree(root, ignore_errors=True)
    write_json(output / "trace.json", trace)
    write_json(output / "run-receipt.json", {"schema_version": "bb.e4.openhands-capture-breadboard-receipt.v1", "case_id": args.case_id, "base_commit": base_commit, "elapsed_seconds": round(time.monotonic() - started, 3), "receiver": ready, "exception": repr(run_error) if run_error else None, "traceback": run_traceback, "trace_sha256": sha(output / "trace.json")})
    return trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path("/testbed"))
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--scenario-file", type=Path, default=CASES)
    parser.add_argument("--native-manifest-sha256", required=True)
    parser.add_argument("--public-manifest-sha256", required=True)
    args = parser.parse_args()
    trace = capture(args)
    print(json.dumps({"case_id": args.case_id, "trace": str(args.output / "trace.json"), "requests": len(trace["requests"]), "status": trace["termination"]["kind"]}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
