"""Compare pinned stock Pi RPC requests with BreadBoard conductor requests.

Package roots are required in PI_CODING_AGENT_NODE_MODULES for 0.73.1 and
PI057_CODING_AGENT_NODE_MODULES for 0.57.1. Overlay argv, advertisement transforms,
and admitted deviations come from the selected target, never a fallback list.
RPC keeps the stock session alive through agent_end compaction. The target
declares this lifecycle difference as compaction_session_lifecycle.
The lane uses the target's pinned model window and compaction settings.
Long-session tool results exceed the keep budget to exercise a split-turn cut.
Every execution limit's source is recorded in lane_report.json. Limits not
declared by the target remain explicitly identified as test-harness admission.

Re-run from the repository root with both package-root variables exported:

    PYTHONPATH=. ~/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m pytest tests/compaction_lanes/ -q -p no:cacheprovider -W ignore
    PYTHONPATH=. ~/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.long_session_lane --harness pi@0.73.1 --target-id pi-r3@0.73.1 --runner rpc --scenario long_session --out /tmp/pi073-long-session
    PYTHONPATH=. ~/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.long_session_lane --harness pi@0.57.1 --target-id pi-r4@0.57.1 --runner rpc --scenario long_session --out /tmp/pi057-long-session
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.compaction_lanes.mock_provider import MockProvider


def resolve_pi_cli(version: str = "0.73.1") -> Path:
    """Resolve only the explicitly configured package for this version."""
    env_var = {
        "0.73.1": "PI_CODING_AGENT_NODE_MODULES",
        "0.57.1": "PI057_CODING_AGENT_NODE_MODULES",
    }[version]
    base = os.environ.get(env_var)
    if not base:
        raise ValueError(f"{env_var} must be set for Pi {version}")
    package = Path(base) / "@mariozechner" / "pi-coding-agent"
    metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
    if metadata["version"] != version:
        raise ValueError(f"{env_var} contains Pi {metadata['version']}, expected {version}")
    cli = package / "dist" / "cli.js"
    if not cli.is_file():
        raise FileNotFoundError(cli)
    return cli


def resolve_target_harness_path(target_id: str) -> Path:
    """Resolve harness.yaml path from target_id strictly without fallbacks."""
    if "@" in target_id:
        target_name, ver = target_id.split("@", 1)
        if target_name.startswith("pi-r"):
            rev = target_name.split("-", 1)[1]
            cand = REPO_ROOT / "config" / "e4_targets" / "pi" / f"{ver}-{rev}" / "harness.yaml"
            if cand.is_file():
                return cand
        cand = REPO_ROOT / "config" / "e4_targets" / target_name / ver / "harness.yaml"
        if cand.is_file():
            return cand
    cand = REPO_ROOT / "config" / "e4_targets" / target_id / "harness.yaml"
    if cand.is_file():
        return cand
    raise FileNotFoundError(f"Target harness.yaml not found for target_id {target_id!r}")


def get_target_deviations(target_id: str) -> List[str]:
    """Extract policy.deviations ids from target harness.yaml."""
    harness_path = resolve_target_harness_path(target_id)
    import yaml  # type: ignore
    doc = yaml.safe_load(harness_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"Invalid harness.yaml at {harness_path}")
    deviations = doc["policy"]["deviations"]
    if not isinstance(deviations, list) or any(
        not isinstance(dev, dict) or not isinstance(dev.get("id"), str)
        for dev in deviations
    ):
        raise ValueError(f"Missing or invalid policy.deviations in {harness_path}")
    return [dev["id"] for dev in deviations]


def load_target_document(target_id: str, filename: str) -> Dict[str, Any]:
    path = resolve_target_harness_path(target_id).with_name(filename)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"Invalid target document at {path}")
    return doc


def get_target_overlay_argv(target_id: str) -> List[str]:
    doc = load_target_document(target_id, "target.json")
    argv = doc["overlay"]["argv"]
    if not isinstance(argv, list) or not argv or any(
        not isinstance(arg, str) or not arg for arg in argv
    ):
        raise ValueError(f"Invalid overlay.argv for {target_id}")
    return argv


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


def build_long_session_scenario(context_window: int = 16000) -> List[Dict[str, Any]]:
    """Build scripted responses that exercise:
    - several tool turns before first compaction
    - overflow 400 -> first compaction
    - retried request with compacted history -> subsequent tool call
    - overflow 400 -> second compaction (carrying previous summary)
    - retried request -> repeated overflow terminating at Pi 1-attempt bound.
    """
    summary_1 = (
        "## Goal\nAnalyze files\n\n## Constraints & Preferences\n- Standard execution\n\n"
        "## Progress\n### Done\n- Steps 1-3 complete\n\n## Key Decisions\n- (none)\n\n## Next Steps\n1. Continue analysis"
    )
    summary_2 = (
        "## Goal\nAnalyze files\n\n## Constraints & Preferences\n- Standard execution\n\n"
        "## Progress\n### Done\n- Steps 1-4 complete\n\n## Key Decisions\n- (none)\n\n## Next Steps\n1. Finish analysis"
    )
    return [
        # Five 18k tool outputs exceed the pinned 20k-token keep budget while
        # each observation stays below the existing 20k-byte test envelope.
        {
            "type": "response",
            "tool_calls": [
                {"name": "bash", "call_id": "call_1a", "arguments": json.dumps({"command": "printf 'a%.0s' {1..18000}; printf '\\n'"})},
                {"name": "bash", "call_id": "call_1b", "arguments": json.dumps({"command": "printf 'b%.0s' {1..18000}; printf '\\n'"})},
            ],
            "usage": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
        },
        {
            "type": "response",
            "tool_calls": [
                {"name": "bash", "call_id": "call_2a", "arguments": json.dumps({"command": "printf 'c%.0s' {1..18000}; printf '\\n'"})},
                {"name": "bash", "call_id": "call_2b", "arguments": json.dumps({"command": "printf 'd%.0s' {1..18000}; printf '\\n'"})},
            ],
            "usage": {"input_tokens": 1200, "output_tokens": 50, "total_tokens": 1250},
        },
        {
            "type": "tool_call", "name": "bash", "call_id": "call_3",
            "arguments": json.dumps({"command": "printf 'e%.0s' {1..18000}; printf '\\n'"}),
            "usage": {"input_tokens": 1400, "output_tokens": 50, "total_tokens": 1450},
        },
        # Req 3: Tool result 3 -> 400 overflow (triggers compaction 1)
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"Context length exceeded ({context_window})",
            "status_code": 400,
        },
        # Req 4: Compaction 1 summary request
        {
            "type": "text",
            "text": summary_1,
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
        # Req 5: Retried request with compacted history -> Tool call 4
        {
            "type": "tool_call",
            "name": "bash",
            "call_id": "call_4",
            "arguments": json.dumps({"command": "echo step 4"}),
            "usage": {"input_tokens": 2500, "output_tokens": 50, "total_tokens": 2550},
        },
        # Req 6: Tool result 4 -> 400 overflow (triggers compaction 2)
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"Context length exceeded ({context_window})",
            "status_code": 400,
        },
        # Req 7: Compaction 2 summary request (carrying summary 1)
        {
            "type": "text",
            "text": summary_2,
            "usage": {"input_tokens": 2200, "output_tokens": 100, "total_tokens": 2300},
        },
        # Req 8: Retried request with compacted history -> 400 overflow (repeated failure terminal)
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"Context length exceeded ({context_window}) second failure",
            "status_code": 400,
        },
    ]


def build_repeated_failure_scenario(context_window: int = 16000) -> List[Dict[str, Any]]:
    """Build scripted responses that force REPEATED FAILURE:

    1. Initial agent request -> overflows (context_length_exceeded).
    2. Summary request -> succeeds.
    3. Retried agent request -> overflows again (second overflow is terminal).
    """
    summary_text = "## Goal\nTest repeated overflow failure.\n\n## Progress\nCompacted once."
    return [
        # Req 1: Initial request overflows
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"This model maximum context length is {context_window} tokens.",
            "status_code": 400,
        },
        # Req 2: Compaction summary
        {
            "type": "text",
            "text": summary_text,
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
        # Req 3: Retried request overflows again -> Terminal failure
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": f"This model maximum context length is {context_window} tokens. Second failure.",
            "status_code": 400,
        },
    ]

def build_final_over_threshold_scenario(
    context_window: int = 16000,
    reserve_tokens: int = 1000,
) -> List[Dict[str, Any]]:
    """Build scripted responses where final response reports usage over threshold but under window:
    - Req 0: Assistant text response with totalTokens > context_window - reserve_tokens
    - Req 1: If harness compacts at agent_end, provider receives summary request
    """
    threshold = context_window - reserve_tokens
    tokens = threshold + 100
    return [
        {
            "type": "text",
            "text": "Task finished. Here is the complete final report.",
            "usage": {"input_tokens": tokens - 500, "output_tokens": 500, "total_tokens": tokens},
        },
        {
            "type": "text",
            "text": "## Goal\nComplete task\n\n## Progress\n### Done\n- Completed all tasks\n\n## Next Steps\n1. Conclude",
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
    ]


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
    assert proc.stdin is not None

    def reader() -> None:
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                stdout_lines.append(line)
                event = json.loads(line)
                if settle.observe(event):
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
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.terminate()
        except OSError:
            if proc.poll() is None:
                raise
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        thread.join(timeout=3)
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

    return subprocess.run(
        cmd,
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


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
) -> subprocess.CompletedProcess[str]:
    """Run stock Pi against mock provider via RPC mode (parity) or print mode (reference)."""
    if runner == "print":
        return run_stock_pi_print(
            pi_cli, mock_base_url, workdir, prompt,
            target_id=target_id, context_window=context_window,
            reserve_tokens=reserve_tokens, keep_recent_tokens=keep_recent_tokens,
            timeout=timeout,
        )
    return run_stock_pi_rpc(
        pi_cli, mock_base_url, workdir, prompt,
        target_id=target_id, context_window=context_window,
        reserve_tokens=reserve_tokens, keep_recent_tokens=keep_recent_tokens,
        timeout=timeout,
    )
async def run_breadboard_pi_episode(
    mock_base_url: str,
    workdir: Path,
    prompt: str,
    target_id: str = "pi-r3@0.73.1",
    context_window: int = 16000,
    max_output_tokens: int = 2048,
    terminal_overflow: bool = False,
) -> Tuple[Any, List[Dict[str, Any]], Dict[str, Any]]:
    """Build the existing conductor test harness with the selected Pi consumer."""
    from breadboard_engine.compilation.provider_response import (
        PI_RESPONSE_CONSUMER_ID, PI_0_57_1_RESPONSE_CONSUMER_ID,
    )
    from breadboard_engine.e4_targets import load_e4_target
    from tests.rl.harness import test_pi_0_73_1_compaction as h
    from breadboard_engine.provider.contracts import NativeProviderRequestFailure
    from breadboard_engine.compaction.overflow import is_context_overflow
    import yaml
    from tests.rl.harness.test_pi_0_57_1_native_stream_conductor import _Pi057WorkerPort

    version = target_id.split("@", 1)[1]
    legacy = version == "0.57.1"
    consumer = PI_0_57_1_RESPONSE_CONSUMER_ID if legacy else PI_RESPONSE_CONSUMER_ID
    model_id = "model-a"
    profile = h.OpenAICompletionsProviderProfile(
        model=model_id, scoped_credential="episode-secret", base_url=mock_base_url,
        context_window=context_window, max_output_tokens=max_output_tokens,
        caller_headers={},
        request_policy={
            "mode": "streaming", "include_usage": True,
            "strict_tools": False if legacy else None, "enable_thinking": None,
        },
        capabilities={"supports_store": not legacy, "supports_strict_tools": legacy},
    )
    cas = h.FilesystemCAS(workdir / "target-cas")
    try:
        compiled = h.compile_e4_harness(
            load_e4_target(target_id), {},
            {
                "version": 2,
                "profile": {"name": "pi-compaction-lane"},
                "workspace": {"root": "workspace"},
                "provider_tools": {
                    "use_native": True,
                    "api_variant": "chat_completions" if legacy else "responses",
                },
                "providers": {
                    "default_model": model_id,
                    "models": [{
                        "id": model_id, "adapter": "openai",
                        "context_length": context_window,
                        "route_handle_id": "route-a", "credential_handle_id": "credential-a",
                        "params": {},
                        "response_policy": {
                            "schema_version": "bb.provider_native_response_policy.v1",
                            "consumer_id": consumer,
                            "provider_profile_digest": h.profile_identity_digest(profile),
                            "max_response_bytes": 1_048_576, "max_stream_fragments": 10_000,
                        },
                    }],
                },
            },
            cas=cas, options=h._options(),
            request_schema_version="bb.rl.headless-run-request.v2",
        )
        manifest = compiled.manifest
        projection = h.E4TargetPolicyProjection.from_compiled(manifest)
        semantics = manifest.semantic.to_canonical_obj()
    finally:
        cas.close()
    features = ["max_tokens", "n", "stream_options", "streaming"]
    features += ["strict_tools"] if legacy else ["store"]
    observation = h._observation(
        provider_id="openai", model_id=model_id,
        capabilities=h._policy_capabilities(request_features=sorted(features)),
    )
    tool_order = load_target_document(target_id, "native-config.json")["tools"]["ordered"]
    tools = tuple(h._tool_grant(name) for name in sorted(tool_order))
    harness_path = resolve_target_harness_path(target_id)
    controls = yaml.safe_load(harness_path.read_text())["policy"]["execution"]["bounded_controls"]
    plan = h._plan(
        observation=observation, semantics=semantics, tools=tools,
        policy_slot_ids=(f"model:{model_id}",),
        limit_updates={
            "max_turns": controls["max_model_calls"],
            "action_timeout_ms": controls["single_tool_wall_seconds"] * 1000,
            "observation_bytes": controls["raw_evidence_bytes_per_outcome"],
        },
        implementation_digest=h.CONDUCTOR_IMPLEMENTATION_DIGEST,
    )
    base = plan.base_compiled.model_dump(mode="python")
    base.update(
        manifest_digest="sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        compiler_input_digest=manifest.inputs.compiler_input_digest,
    )
    payload = plan.model_dump(mode="python")
    payload["base_compiled"] = h.c.CompiledArtifactIdentity.model_validate(base)
    plan = h.c.EffectiveExecutionPlan.model_validate(payload)
    grants = tuple(h.RunnerToolBinding(t.tool_id, t.implementation_digest, t.capability_ids) for t in tools)
    worker = (
        _Pi057WorkerPort(workdir, workdir / ".scratch", grants)
        if legacy else h._NativeWorkerPort(workdir, grants)
    )
    client = h.EpisodeOpenAICompletionsPolicyClient(
        episode_id="episode-pi-compaction", effective_plan_digest=plan.canonical_digest(),
        observation=observation, profile=profile, target_projection=projection, timeout_seconds=45,
    )
    request = h.RunnerOpenRequest(episode_id="episode-pi-compaction", effective_plan=plan)
    sink = h._Events()
    session = await h.ConductorAdapter(
        h.CONDUCTOR_RUNTIME_ABI,
        containment_authenticator=h.CONDUCTOR_TEST_AUTHENTICATOR,
        admitted_lease_ledger=h.CONDUCTOR_TEST_LEDGER,
    ).open(
        request, policy=h.PolicyRuntimeBinding(request, client), workspace=worker,
        cancellation=h._Cancellation(), events=sink,
    )
    try:
        result = await session.run(h.ConductorRunRequest(task_input={"prompt": prompt}, context={}))
    except h.RunnerDependencyError as exc:
        cause: BaseException | None = exc
        while cause is not None and not isinstance(cause, NativeProviderRequestFailure):
            cause = cause.__cause__
        if not terminal_overflow or cause is None or not is_context_overflow(cause):
            raise
        result = exc
    finally:
        await session.close()
        await worker.close()
        await client.close()
    limits = plan.effective_capabilities.limits.model_dump(mode="json")
    sources = {
        name: "tests/rl/harness/test_runner_policy_runtime.py:_plan limit_payload"
        for name in limits
    }
    for name, key in [
        ("max_turns", "max_model_calls"),
        ("action_timeout_ms", "single_tool_wall_seconds"),
        ("observation_bytes", "raw_evidence_bytes_per_outcome"),
    ]:
        sources[name] = f"{harness_path.relative_to(REPO_ROOT)}:policy.execution.bounded_controls.{key}"
    # A raised Pi073 dependency failure has no returned replay trace; preserve
    # that absence rather than synthesizing one from captured HTTP requests.
    trace_count = (
        None if isinstance(result, Exception)
        else len(result.response["replay_trace"]["requests"])
    )
    return result, sink.events, {
        "limits": limits, "limit_sources": sources,
        "bb_replay_trace_request_count": trace_count,
    }
def is_summary_request(
    request_body: Mapping[str, Any],
    profile_id: str = "breadboard.pi-coding-agent.v0.73.1",
) -> bool:
    """Check if request body is a compaction summary request by checking profile system prompt equality."""
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES

    profile = NATIVE_STREAM_PROFILES.get(profile_id)
    if not profile or not profile.compaction_summary_system_prompt:
        return False
    msgs = request_body.get("messages", [])
    if not msgs or not isinstance(msgs, list):
        return False
    first = msgs[0]
    if isinstance(first, dict) and first.get("role") == "system":
        return first.get("content") == profile.compaction_summary_system_prompt
    return False


def normalize_request_body(
    body: Dict[str, Any],
    allowed_deviations: Sequence[str],
    *,
    is_stock: bool = False,
    target_id: str = "pi-r3@0.73.1",
    episode_tools: Any = None,
    episode_max_tokens: Optional[int] = None,
    reserve_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Normalize a request body removing strictly the recorded deviation fields.

    Admitted deviations:
    - compaction_summary_episode_tools: On summary requests, remove `tools` field.
    - compaction_summary_max_tokens: On summary requests, remove `max_tokens` / `max_completion_tokens`.

    Declared transform (native-config advertisement):
    - When is_stock is True:
      * advertisement.tools.read: verify native_sha256, replace with declared description.
      * advertisement.prompt.remove_exact: assert needle was in content, remove it.
    - Canonical environmental normalizations (workspace path, current date, package dir).
    """
    import re
    normalized = deepcopy(body)
    is_sum = is_summary_request(normalized)
    if is_sum:
        if "compaction_summary_episode_tools" in allowed_deviations:
            if is_stock:
                if "tools" in normalized:
                    raise ValueError("Stock Pi summary unexpectedly carries tools")
            elif episode_tools is None or json.dumps(normalized.get("tools"), sort_keys=True) != json.dumps(episode_tools, sort_keys=True):
                raise ValueError("BB summary tools differ from episode tools")
            normalized.pop("tools", None)
        if "compaction_summary_max_tokens" in allowed_deviations:
            if is_stock:
                reserve = reserve_tokens if reserve_tokens is not None else load_target_document(
                    target_id, "native-config.json"
                )["agent"]["compaction_settings"]["reserveTokens"]
                content = normalized["messages"][-1]["content"]
                text = content if isinstance(content, str) else content[-1]["text"]
                # Stock history/prefix budgets: 0.73.1 compaction.js:433,593;
                # 0.57.1 compaction.js:424,590.
                ratio = 0.5 if "\n\nThis is the PREFIX of a turn" in text else 0.8
                expected_cap = int(ratio * reserve)
            else:
                if episode_max_tokens is None:
                    raise ValueError("Episode cap is required for summary validation")
                expected_cap = episode_max_tokens
            if normalized.get("max_tokens") != expected_cap or "max_completion_tokens" in normalized:
                raise ValueError("Summary token cap differs from its admitted budget")
            normalized.pop("max_tokens", None)

    adv = load_target_document(target_id, "native-config.json")["advertisement"]
    if not isinstance(adv, dict):
        raise ValueError(f"Invalid native advertisement for {target_id}")

    read_adv = adv.get("tools", {}).get("read", {})
    remove_exact = adv.get("prompt", {}).get("remove_exact", [])

    if is_stock and read_adv:
        for t in normalized.get("tools", []):
            fn = t.get("function", {})
            if fn.get("name") == "read":
                desc = fn.get("description", "")
                actual_sha = "sha256:" + hashlib.sha256(desc.encode("utf-8")).hexdigest()
                assert actual_sha == read_adv.get("native_sha256"), (
                    f"Stock read description SHA mismatch: {actual_sha} vs {read_adv.get('native_sha256')}"
                )
                fn["description"] = read_adv["description"]

    for m in normalized.get("messages", []):
        if m.get("role") == "system":
            content = m.get("content", "")
            if is_stock and not is_sum and remove_exact:
                for needle in remove_exact:
                    assert needle in content, f"Declared transform needle {needle!r} not in stock system prompt"
                    content = content.replace(needle, "")

            # Canonical environmental normalizations
            content = re.sub(r"Current date: \d{4}-\d{2}-\d{2}", "Current date: <CURRENT_DATE>", content)
            content = re.sub(r"Current date and time: [^\n]+", "Current date and time: <CURRENT_DATE_TIME>", content)
            content = re.sub(r"Current working directory: [^\n]+", "Current working directory: <WORKSPACE>", content)
            content = re.sub(r"/.*?/@mariozechner/pi-coding-agent", "<PI_PACKAGE_DIR>", content)
            m["content"] = content

    return normalized


def compare_recorded_requests(
    stock_requests: List[Dict[str, Any]],
    bb_requests: List[Dict[str, Any]],
    allowed_deviations: Sequence[str],
    *,
    target_id: str = "pi-r3@0.73.1",
    episode_tools: Any = None,
    episode_max_tokens: Optional[int] = None,
    reserve_tokens: Optional[int] = None,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Byte-compare stock and BreadBoard requests after applying deviation normalizer."""
    diffs: List[Dict[str, Any]] = []
    main = next((row["json"] for row in bb_requests if not is_summary_request(row["json"])), None)
    if main is not None:
        episode_tools = main["tools"]
        episode_max_tokens = main["max_tokens"]

    if len(stock_requests) != len(bb_requests):
        diffs.append({
            "type": "count_mismatch",
            "stock_count": len(stock_requests),
            "bb_count": len(bb_requests),
        })

    max_idx = max(len(stock_requests), len(bb_requests))
    for i in range(max_idx):
        if i >= len(stock_requests):
            diffs.append({"index": i, "type": "extra_breadboard_request", "bb_request": bb_requests[i]})
            continue
        if i >= len(bb_requests):
            diffs.append({"index": i, "type": "missing_breadboard_request", "stock_request": stock_requests[i]})
            continue

        try:
            stock_norm = normalize_request_body(
                stock_requests[i]["json"], allowed_deviations, is_stock=True,
                target_id=target_id, reserve_tokens=reserve_tokens,
            )
            bb_norm = normalize_request_body(
                bb_requests[i]["json"], allowed_deviations, is_stock=False,
                target_id=target_id, episode_tools=episode_tools,
                episode_max_tokens=episode_max_tokens,
            )
        except ValueError as exc:
            diffs.append({"index": i, "type": "deviation_value_diff", "message": str(exc)})
            continue

        stock_bytes = json.dumps(stock_norm, sort_keys=True).encode("utf-8")
        bb_bytes = json.dumps(bb_norm, sort_keys=True).encode("utf-8")

        if stock_bytes != bb_bytes:
            diffs.append({
                "index": i,
                "type": "body_diff",
                "stock_normalized": stock_norm,
                "bb_normalized": bb_norm,
            })

    return len(diffs) == 0, diffs


def run_pi_0_73_1_lane(
    out_dir: Path,
    target_id: str = "pi-r3@0.73.1",
    context_window: Optional[int] = None,
    scenario: str = "overflow_recovery",
    runner: str = "rpc",
) -> Dict[str, Any]:
    """Execute either pinned Pi version's long-session lane end-to-end."""
    out_dir.mkdir(parents=True, exist_ok=True)
    version = target_id.split("@", 1)[1]
    pi_cli = resolve_pi_cli(version=version)
    native_config = load_target_document(target_id, "native-config.json")
    pinned_window = native_config["model"]["context_window"]
    compaction_settings = native_config["agent"]["compaction_settings"]
    if context_window is not None and context_window != pinned_window:
        raise ValueError("The lane requires the target's pinned context window")
    context_window = pinned_window
    reserve_tokens = compaction_settings["reserveTokens"]
    keep_recent_tokens = compaction_settings["keepRecentTokens"]

    deviations = get_target_deviations(target_id)
    if scenario == "long_session":
        scenario_script = build_long_session_scenario(context_window=context_window)
        task_prompt = "Perform analysis of workspace files"
    elif scenario == "final_over_threshold":
        scenario_script = build_final_over_threshold_scenario(context_window=context_window, reserve_tokens=reserve_tokens)
        task_prompt = "Execute task"
    elif scenario == "repeated_failure":
        scenario_script = build_repeated_failure_scenario(context_window=context_window)
        task_prompt = "Hello from user. " + ("Here is important context info. " * 3000)
    else:  # "overflow_recovery"
        task_prompt = "Perform multi-step investigation of workspace files"
        scenario_script = [
            # Req 0: Initial prompt -> Tool call 1
            {
                "type": "tool_call",
                "name": "bash",
                "call_id": "call_1",
                "arguments": json.dumps({"command": "echo 'investigation step 1 complete'"}),
                "usage": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
            },
            # Req 1: Tool result 1 -> Tool call 2
            {
                "type": "tool_call",
                "name": "bash",
                "call_id": "call_2",
                "arguments": json.dumps({"command": "echo 'investigation step 2 complete'"}),
                "usage": {"input_tokens": 1200, "output_tokens": 50, "total_tokens": 1250},
            },
            # Req 2: Tool result 2 -> 400 overflow (triggers compaction mid-loop)
            {
                "type": "error",
                "error": "context_length_exceeded",
                "message": f"This model maximum context length is {context_window} tokens.",
                "status_code": 400,
            },
            # Req 3: Compaction summary request
            {
                "type": "text",
                "text": (
                    "## Goal\nInvestigate workspace files\n\n## Constraints & Preferences\n- (none)\n\n"
                    "## Progress\n### Done\n- Investigation steps 1 and 2 complete\n\n### In Progress\n- Final synthesis\n\n"
                    "## Key Decisions\n- (none)\n\n## Next Steps\n1. Complete analysis"
                ),
                "usage": {"input_tokens": 1500, "output_tokens": 100, "total_tokens": 1600},
            },
            # Req 4: Retried request with compacted history -> Assistant completes turn
            {
                "type": "text",
                "text": "Investigation finished successfully. All steps completed.",
                "usage": {"input_tokens": 1800, "output_tokens": 30, "total_tokens": 1830},
            },
        ]
    # 1. Run Stock Pi against mock provider
    stock_record_file = out_dir / "stock_requests.jsonl"
    stock_mock = MockProvider(script=scenario_script, record_path=stock_record_file)
    stock_mock.start()

    with tempfile.TemporaryDirectory(prefix="stock_pi_lane_") as stock_tmp:
        stock_workdir = Path(stock_tmp)
        try:
            stock_proc = run_stock_pi(
                pi_cli,
                stock_mock.base_url,
                stock_workdir,
                task_prompt,
                target_id=target_id,
                context_window=context_window,
                reserve_tokens=reserve_tokens,
                keep_recent_tokens=keep_recent_tokens,
                runner=runner,
            )
        finally:
            stock_mock.stop()

    stock_requests = [
        json.loads(line)
        for line in stock_record_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # 2. Replay through BreadBoard conductor
    bb_record_file = out_dir / "breadboard_requests.jsonl"
    bb_mock = MockProvider(script=scenario_script, record_path=bb_record_file)
    bb_mock.start()

    with tempfile.TemporaryDirectory(prefix="bb_pi_lane_") as bb_tmp:
        bb_workdir = Path(bb_tmp)
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bb_result, bb_events, limit_report = loop.run_until_complete(
                run_breadboard_pi_episode(
                    bb_mock.base_url,
                    bb_workdir,
                    task_prompt,
                    target_id=target_id,
                    context_window=context_window,
                    terminal_overflow=scenario in ("long_session", "repeated_failure"),
                )
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)
            bb_mock.stop()

    bb_requests = [
        json.loads(line)
        for line in bb_record_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace_count = limit_report["bb_replay_trace_request_count"]
    if trace_count is not None:
        assert trace_count == len(bb_requests), "Replay trace must record every physical HTTP request exactly once"

    # 3. Compare request bodies
    passed, diffs = compare_recorded_requests(stock_requests, bb_requests, deviations, target_id=target_id)

    harness_name = f"pi@{version}"
    summary = {
        "harness": harness_name,
        "target_id": target_id,
        "runner": runner,
        "scenario": scenario,
        "context_window": context_window,
        "compaction_settings": compaction_settings,
        "native_config_source": str(resolve_target_harness_path(target_id).with_name("native-config.json").relative_to(REPO_ROOT)),
        "stock_request_count": len(stock_requests),
        **limit_report,
        "bb_terminal_error": str(bb_result) if isinstance(bb_result, Exception) else None,
        "bb_request_count": len(bb_requests),
        "stock_compaction_count": sum(is_summary_request(req["json"]) for req in stock_requests),
        "bb_compaction_count": sum(is_summary_request(req["json"]) for req in bb_requests),
        "deviations_admitted": deviations,
        "diff_count": len(diffs),
        "diffs": diffs,
        "passed": passed,
    }

    (out_dir / "lane_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Long-session compaction byte-for-byte lane.")
    parser.add_argument("--harness", required=True, help="Target harness (e.g. pi@0.73.1, pi@0.57.1)")
    parser.add_argument("--target-id", default=None, help="BreadBoard target id override (default: auto)")
    parser.add_argument("--runner", default="rpc", choices=["rpc", "print"], help="Stock runner mode: rpc (parity) or print (reference)")
    parser.add_argument("--scenario", default="overflow_recovery", choices=["overflow_recovery", "long_session", "repeated_failure", "final_over_threshold"], help="Scenario to run")
    parser.add_argument("--context-window", type=int, default=None, help="Must equal the selected target's pinned model context window")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for recordings and diffs")
    args = parser.parse_args()

    target_id = args.target_id
    if not target_id:
        if "0.73.1" in args.harness:
            target_id = "pi-r3@0.73.1"
        elif "0.57.1" in args.harness:
            target_id = "pi-r4@0.57.1"
        else:
            target_id = args.harness

    report = run_pi_0_73_1_lane(
        args.out,
        target_id=target_id,
        context_window=args.context_window,
        scenario=args.scenario,
        runner=args.runner,
    )
    print(json.dumps(report, indent=2))
    if not report.get("passed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
