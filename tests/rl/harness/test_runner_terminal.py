from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import subprocess
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any

import pytest

from breadboard_engine.compilation.contracts import canonical_sha256
from breadboard.rl.phase5.bootstrap import BREADBOARD_BASELINE
import breadboard.rl.harness.runners.base as runner_base
from breadboard.rl.harness.runners import terminal as terminal_module
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.runners.base import (
    JsonSnapshotError,
    PolicyRequestEvent,
    PolicyResponseEvent,
    RunnerCancellation,
    RunnerCancellationObservedEvent,
    RunnerCancellationRequestedEvent,
    RunnerCancelled,
    RunnerCloseResult,
    RunnerDependencyError,
    RunnerErrorEvent,
    RunnerEvent,
    RunnerEventSinkError,
    RunnerOpenRequest,
    RunnerPlanError,
    RunnerProtocolError,
    RunnerRequestError,
    RunnerResult,
    RunnerStateError,
    RunnerTermination,
    RunnerTerminationEvent,
    RunnerToolBinding,
    RunnerTurn,
    ToolCallEvent,
    ToolObservationEvent,
    thaw_json,
)
from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
    TERMINAL_TOOL_DEFINITIONS,
    TerminalLoopLimits,
    TerminalResponsesAdapter,
    TerminalRunRequest,
    TerminalToolDefinition,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "rl"
    / "harness"
    / "terminal_adapter_parity_v1.json"
)
PROVENANCE_PATH = FIXTURE_PATH.with_name("terminal_adapter_parity_v1.provenance.json")
REPO_ROOT = Path(__file__).resolve().parents[3]
PINNED_PROVENANCE_SHA256 = "d845a272a61576cb3285a70d213968d5f2d1a125a820c1919c823505e66bf1e8"
PINNED_FIXTURE_SHA256 = "f55f214cde5a25f5a2cc7399a5c86d73d9116edbe4e1a08cf333fb807babbfdb"
RUNTIME_ABI = TERMINAL_RUNTIME_ABI
IMPLEMENTATION_DIGEST = TERMINAL_IMPLEMENTATION_DIGEST


def test_terminal_implementation_digest_measures_exact_module_artifact() -> None:
    module_path = Path(terminal_module.__file__)
    assert module_path.is_absolute()
    assert TERMINAL_IMPLEMENTATION_DIGEST == (
        "sha256:" + hashlib.sha256(module_path.read_bytes()).hexdigest()
    )


def test_terminal_constructor_owns_identity_and_rejects_post_bootstrap_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError):
        TerminalResponsesAdapter(RUNTIME_ABI, "sha256:" + "0" * 64)  # type: ignore[call-arg]
    original = terminal_module._TERMINAL_MODULE_IDENTITY
    changed = type(original)(
        "sha256:" + "0" * 64,
        original.device,
        original.inode,
        original.size_bytes,
        original.mtime_ns,
    )
    monkeypatch.setattr(terminal_module, "measure_module_artifact", lambda _path: changed)
    with pytest.raises(RuntimeError, match="changed after bootstrap"):
        TerminalResponsesAdapter(RUNTIME_ABI)

MISSING_ARGUMENT = object()

class ScriptedPolicy:
    def __init__(
        self,
        responses: list[dict[str, Any] | BaseException],
        *,
        before_generate: Any = None,
    ) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.before_generate = before_generate

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        request = copy.deepcopy(dict(request_payload))
        self.requests.append(request)
        if self.before_generate is not None:
            self.before_generate(len(self.requests), request)
        if not self.responses:
            raise AssertionError("policy received more turns than scripted")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class RecordingWorkspace:
    def __init__(self, *, tool_bindings: Any = None) -> None:
        self.actions: list[tuple[Any, ...]] = []
        self.files: dict[str, str] = {}
        self.override: dict[str, Any] = {}
        self.after_action: Any = None
        self._tool_bindings = (
            _plan_tool_bindings() if tool_bindings is None else tool_bindings
        )

    @property
    def tool_bindings(self) -> Any:
        return self._tool_bindings

    async def run_shell(self, command: str, *, timeout: int) -> Mapping[str, Any]:
        self.actions.append(("shell", command, timeout))
        result = self.override.get(
            "shell", {"exit": 0, "stdout": f"ran:{command}", "stderr": ""}
        )
        if self.after_action is not None:
            self.after_action("shell")
        if isinstance(result, BaseException):
            raise result
        return result

    async def read_text(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> Mapping[str, Any]:
        self.actions.append(("read_file", path, offset, limit))
        result = self.override.get(
            "read_file",
            {
                "path": path,
                "content": self.files.get(path, "")[
                    offset : None if limit is None else offset + limit
                ],
                "truncated": False,
            },
        )
        if self.after_action is not None:
            self.after_action("read_file")
        if isinstance(result, BaseException):
            raise result
        return result

    async def write_text(self, path: str, content: str) -> Mapping[str, Any]:
        self.actions.append(("write_file", path, content))
        self.files[path] = content
        result = self.override.get(
            "write_file",
            {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))},
        )
        if self.after_action is not None:
            self.after_action("write_file")
        if isinstance(result, BaseException):
            raise result
        return result

    async def list_files(self, path: str, *, depth: int) -> Mapping[str, Any]:
        self.actions.append(("list_files", path, depth))
        result = self.override.get(
            "list_files", {"path": path, "entries": sorted(self.files)}
        )
        if self.after_action is not None:
            self.after_action("list_files")
        if isinstance(result, BaseException):
            raise result
        return result


class CancellationSignal(RuntimeError):
    pass


class ScriptedCancellationProbe:
    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, int | None, str | None]] = []
        self.cancel_on: tuple[str, int | None, str | None] | None = None
        self.reason = "operator stop"

    def raise_if_cancelled(
        self,
        checkpoint: str,
        *,
        turn: int | None = None,
        call_id: str | None = None,
    ) -> None:
        observed = (checkpoint, turn, call_id)
        self.checkpoints.append(observed)
        if self.cancel_on == observed:
            raise CancellationSignal(self.reason)


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[RunnerEvent] = []
        self.fail_at: int | None = None

    async def emit(self, event: RunnerEvent) -> None:
        if self.fail_at == len(self.events) + 1:
            raise OSError("event journal unavailable")
        self.events.append(event)


class MissingBindingsWorkspace(RecordingWorkspace):
    def __getattribute__(self, name: str) -> Any:
        if name == "tool_bindings":
            raise AttributeError(name)
        return super().__getattribute__(name)


class RaisingBindingsWorkspace(RecordingWorkspace):
    @property
    def tool_bindings(self) -> Any:
        raise OSError("binding registry unavailable")


class UntypedWorkspace(RecordingWorkspace):
    async def run_shell(self, command: str, *, timeout: int) -> Any:
        self.actions.append(("shell", command, timeout))
        return ["not", "an", "object"]


def _digest(character: str) -> str:
    return "sha256:" + hashlib.sha256(character.encode("ascii")).hexdigest()

def _plan_tool_bindings() -> tuple[RunnerToolBinding, ...]:
    tool_ids = ("list_files", "read_file", "shell", "submit", "write_file")
    return tuple(
        RunnerToolBinding(
            tool_id=tool_id,
            implementation_digest=_digest(chr(ord("b") + index)),
            capability_ids=(),
        )
        for index, tool_id in enumerate(tool_ids)
    )


def _effective_plan(
    *,
    max_turns: int = 4,
    action_timeout_ms: int = 9_000,
    observation_bytes: int = 20_000,
    response_bytes: int = 100_000,
    transcript_bytes: int = 100_000,
    adapter_id: str = TERMINAL_ADAPTER_ID,
    runtime_abi: str = RUNTIME_ABI,
    implementation_digest: str = IMPLEMENTATION_DIGEST,
    tool_ids: tuple[str, ...] = (
        "list_files",
        "read_file",
        "shell",
        "submit",
        "write_file",
    ),
) -> c.EffectiveExecutionPlan:
    runner = c.RunnerGrant(
        adapter_id=adapter_id,
        runtime_abi=runtime_abi,
        implementation_digest=implementation_digest,
    )
    limits = c.ExecutionLimits(
        max_turns=max_turns,
        action_timeout_ms=action_timeout_ms,
        observation_bytes=observation_bytes,
        response_bytes=response_bytes,
        artifact_bytes_each=10_000,
        artifact_bytes_total=20_000,
        transcript_bytes=transcript_bytes,
        setup_timeout_ms=5_000,
        verifier_timeout_ms=17_000,
    )
    tools = tuple(
        c.ToolGrant(
            tool_id=tool_id,
            implementation_digest=_digest(chr(ord("b") + index)),
            capability_ids=(),
        )
        for index, tool_id in enumerate(tool_ids)
    )
    sandbox = c.SandboxGrant(
        runtime_id="sandbox",
        runtime_class=c.RuntimeClass.TRUSTED_PROCESS,
        driver_implementation_digest=_digest("h"),
        runtime_binary_digest=_digest("i"),
        security_policy_digest=_digest("j"),
        image_digest=_digest("k"),
        network_policy_digest=_digest("l"),
        egress_route_ids=(),
        mounts=(),
    )
    resources = c.ResourceLimits(
        cpu_millis=1_000,
        memory_bytes=1_000_000,
        pids=32,
        storage_bytes=1_000_000,
        open_files=128,
        wall_time_ms=60_000,
    )
    task = c.TaskGrant(
        task_contract_digest=_digest("m"),
        task_binding_digest=_digest("n"),
        repository_snapshot_digest=None,
        dataset_digests=(),
        input_artifact_digests=(),
    )
    verifier = c.VerifierGrant(
        verifier_id="verifier",
        implementation_digest=_digest("o"),
        image_digest=_digest("p"),
        executable_digest=_digest("q"),
        code_digest=_digest("r"),
        input_schema_digest=_digest("s"),
        result_schema_digest=_digest("t"),
        network_policy_digest=_digest("u"),
        secret_handle_ids=(),
    )
    artifacts = c.ArtifactPolicyGrant(
        allowed_roles=(), max_each_bytes=10_000, max_total_bytes=20_000
    )
    evidence = c.PolicyRef(policy_id="evidence", revision_digest=_digest("v"))
    retention = c.PolicyRef(policy_id="retention", revision_digest=_digest("w"))
    capabilities = c.CapabilityVector(
        runner=runner,
        tools=tools,
        setup_plans=(),
        routes=(),
        secret_handles=(),
        sandbox=sandbox,
        resources=resources,
        limits=limits,
        task=task,
        policy_slots=(),
        verifier=verifier,
        mutable_pointers=(),
        artifacts=artifacts,
        evidence=evidence,
        retention=retention,
    )
    semantic: dict[str, Any] = {}
    semantic_digest = canonical_sha256(
        {"schema": c.COMPILED_CONFIG_SEMANTIC_SCHEMA_ID, "config": semantic}
    )
    compiler = c.CompilerIdentity(
        compiler_id="compiler",
        semantic_version="1.0.0",
        code_digest=_digest("x"),
        source_schema_id="source.v1",
        source_schema_digest=_digest("y"),
        manifest_schema_digest=_digest("z"),
        canonicalizer_id="canonical-json-v1",
        runtime_abi=RUNTIME_ABI,
    )
    compiled = c.CompiledArtifactIdentity(
        manifest_digest=_digest("0"),
        bundle_digest=_digest("1"),
        closure_digest=_digest("2"),
        compiler_input_digest=_digest("3"),
        semantic_digest=semantic_digest,
        compiler=compiler,
        provenance_digest=_digest("4"),
        diagnostics_digest=_digest("5"),
    )
    return c.EffectiveExecutionPlan(
        subject_digest=_digest("6"),
        base_compiled=compiled,
        base_receipt_digest=_digest("7"),
        selector_digest=_digest("8"),
        config_set_digest=None,
        admitted_set_root=_digest("9"),
        selection_record_digest=_digest("a"),
        task_eligibility_digest=_digest("b"),
        policy_capability_observation_digest=_digest("c"),
        policy_capability_digest=_digest("d"),
        overlay_applications=(),
        final_receipt_digest=_digest("7"),
        final_semantic_digest=semantic_digest,
        effective_semantics=semantic,
        effective_capabilities=capabilities,
        effective_capability_digest=capabilities.canonical_digest(),
        pins=(),
        runner=runner,
        policy_slots=(),
        sandbox=sandbox,
        verifier=verifier,
        task=task,
        artifacts=artifacts,
        evidence=evidence,
        retention=retention,
        revocation=c.RevocationBinding(
            scope_digest=_digest("e"), epoch=1, state_digest=_digest("f")
        ),
    )


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _tools(data: dict[str, Any] | None = None) -> tuple[TerminalToolDefinition, ...]:
    source = data or _fixture()
    return tuple(
        TerminalToolDefinition(
            tool_id=item["tool_id"], responses_schema=item["responses_schema"]
        )
        for item in source["terminal_tools"]
    )


def _request(
    *,
    params: Mapping[str, Any] | None = None,
    tools: tuple[TerminalToolDefinition, ...] | None = None,
    max_turns: int = 4,
    timeout: int = 9,
    observation_chars: int = 20_000,
) -> TerminalRunRequest:
    return TerminalRunRequest(
        responses_create_params=params or {"input": "work"},
        tools=tools or _tools(),
        limits=TerminalLoopLimits(
            max_turns=max_turns,
            action_timeout_seconds=timeout,
            max_observation_chars=observation_chars,
        ),
    )


async def _open(
    *,
    policy: ScriptedPolicy,
    workspace: RecordingWorkspace | None = None,
    cancellation: ScriptedCancellationProbe | None = None,
    sink: RecordingEventSink | None = None,
    plan: c.EffectiveExecutionPlan | None = None,
    episode_id: str = "episode",
) -> tuple[Any, RecordingWorkspace, ScriptedCancellationProbe, RecordingEventSink]:
    resolved_plan = plan or _effective_plan()
    resolved_workspace = workspace or RecordingWorkspace()
    resolved_cancellation = cancellation or ScriptedCancellationProbe()
    resolved_sink = sink or RecordingEventSink()
    adapter = TerminalResponsesAdapter(runtime_abi=RUNTIME_ABI)
    session = await adapter.open(
        RunnerOpenRequest(episode_id=episode_id, effective_plan=resolved_plan),
        policy=policy,
        workspace=resolved_workspace,
        cancellation=resolved_cancellation,
        events=resolved_sink,
    )
    return session, resolved_workspace, resolved_cancellation, resolved_sink


def _call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _default_policy_request_payload() -> dict[str, Any]:
    return {
        "input": [{"role": "user", "content": "work"}],
        "tools": [_thaw(tool.responses_schema) for tool in _tools()],
        "parallel_tool_calls": False,
    }

def _thaw(value: Any) -> Any:
    return thaw_json(value)


def _source_defines_symbol(source_bytes: bytes, qualified_name: str) -> bool:
    nodes: list[ast.stmt] = list(ast.parse(source_bytes).body)
    for part in qualified_name.split("."):
        matched: ast.stmt | None = None
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == part:
                    matched = node
                    break
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == part for target in targets):
                    matched = node
                    break
        if matched is None:
            return False
        nodes = list(matched.body) if hasattr(matched, "body") else []
    return True


def _contains_fixture_reference(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("$")
    if isinstance(value, list):
        return any(_contains_fixture_reference(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_fixture_reference(item) for item in value.values())
    return False


def test_terminal_adapter_parity_fixture_has_pinned_legacy_provenance() -> None:
    provenance_bytes = PROVENANCE_PATH.read_bytes()
    fixture_bytes = FIXTURE_PATH.read_bytes()
    assert hashlib.sha256(provenance_bytes).hexdigest() == PINNED_PROVENANCE_SHA256
    assert hashlib.sha256(fixture_bytes).hexdigest() == PINNED_FIXTURE_SHA256
    assert not _contains_fixture_reference(json.loads(fixture_bytes))

    provenance = json.loads(provenance_bytes)
    assert provenance["reference_repository"] == "."
    assert provenance["reference_revision"] == BREADBOARD_BASELINE
    assert provenance["capture_method"]["method_id"] == (
        "git-object-frozen-legacy-characterization-v1"
    )
    for source in provenance["sources"]:
        completed = subprocess.run(
            ["git", "show", f"{BREADBOARD_BASELINE}:{source['path']}"],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert completed.returncode == 0, (
            f"frozen baseline object unavailable for {source['path']}: "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
        assert hashlib.sha256(completed.stdout).hexdigest() == source["sha256"]
        assert all(
            _source_defines_symbol(completed.stdout, symbol)
            for symbol in source["symbols"]
        )

    assert provenance["fixture"] == {
        "path": "tests/fixtures/rl/harness/terminal_adapter_parity_v1.json",
        "sha256": PINNED_FIXTURE_SHA256,
        "characterization_id": "terminal-adapter-multiturn-submit-v1",
        "expected_records": [
            "policy_requests",
            "workspace_actions",
            "final_response",
            "turns",
            "events",
            "termination",
        ],
    }


async def test_terminal_adapter_matches_frozen_multiturn_submit_fixture() -> None:
    data = _fixture()
    assert [
        {"tool_id": tool.tool_id, "responses_schema": _thaw(tool.responses_schema)}
        for tool in TERMINAL_TOOL_DEFINITIONS
    ] == data["terminal_tools"]
    sink = RecordingEventSink()

    def before_generate(turn: int, request: dict[str, Any]) -> None:
        assert isinstance(sink.events[-1], PolicyRequestEvent)
        assert sink.events[-1].turn == turn
        assert request == data["expected_policy_requests"][turn - 1]

    policy = ScriptedPolicy(
        copy.deepcopy(data["policy_responses"]), before_generate=before_generate
    )
    session, workspace, probe, _ = await _open(
        policy=policy,
        sink=sink,
        episode_id=data["episode_id"],
    )
    request = _request(
        params=data["responses_create_params"],
        tools=_tools(data),
        max_turns=data["limits"]["max_turns"],
        timeout=data["limits"]["action_timeout_seconds"],
        observation_chars=data["limits"]["max_observation_chars"],
    )

    result = await session.run(request)

    assert _thaw(result.original_request) == data["responses_create_params"]
    assert _thaw(result.response) == data["expected_final_response"]
    assert result.termination.value == data["expected_termination"]
    assert result.turn_count == data["expected_turn_count"]
    assert workspace.actions == [tuple(item) for item in data["expected_workspace_actions"]]
    assert [_public_event_record(event) for event in result.events] == data[
        "expected_events"
    ]
    assert [_public_turn_record(turn) for turn in result.turns] == data[
        "expected_turns"
    ]
    assert probe.checkpoints == [
        ("before_policy", 1, None),
        ("after_policy", 1, None),
        ("before_action", 1, "shell-1"),
        ("before_policy", 2, None),
        ("after_policy", 2, None),
        ("before_action", 2, "write-1"),
        ("before_action", 2, "submit-1"),
        ("before_action", 2, "list-after-submit"),
        ("after_loop", 2, None),
    ]


async def test_terminal_adapter_replaces_tools_forces_serial_and_preserves_request_and_carriers() -> None:
    params = {
        "model": "model",
        "input": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
        "tools": [{"type": "function", "name": "caller-tool"}],
        "parallel_tool_calls": True,
        "sampling": {"temperature": 0.25},
    }
    original = copy.deepcopy(params)
    response = {
        "id": "final",
        "output": [{"type": "message", "role": "assistant", "content": "done"}],
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "token_ids": [1, 2],
        "token_logprobs": [-0.2, -0.1],
        "routing": {"route": "r1"},
        "unknown_vendor": {"nested": [True]},
    }
    policy = ScriptedPolicy([response])
    session, _, _, _ = await _open(policy=policy)
    request = _request(params=params)
    params["input"][0]["content"][0]["text"] = "mutated after construction"
    params["tools"].clear()

    result = await session.run(request)
    response["usage"]["input_tokens"] = 999
    response["unknown_vendor"]["nested"].append(False)

    assert _thaw(result.original_request) == original
    assert policy.requests[0]["input"] == original["input"]
    assert policy.requests[0]["parallel_tool_calls"] is False
    assert policy.requests[0]["tools"] == [
        _thaw(tool.responses_schema) for tool in _tools()
    ]
    assert _thaw(result.response)["usage"] == {"input_tokens": 10, "output_tokens": 2}
    assert _thaw(result.response)["unknown_vendor"] == {"nested": [True]}
    response_event = next(
        event for event in result.events if isinstance(event, PolicyResponseEvent)
    )
    assert _thaw(response_event.response_payload)["usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
    }
    with pytest.raises(TypeError):
        response_event.response_payload["id"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.response["id"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        result.turn_count = 99  # type: ignore[misc]
    assert isinstance(result.events, tuple)
    assert isinstance(result.turns, tuple)


async def test_terminal_adapter_dispatches_every_admitted_tool_with_exact_bounds() -> None:
    calls = [
        _call("shell-low", "shell", json.dumps({"command": "low", "timeout_seconds": 0})),
        _call("shell-high", "shell", json.dumps({"command": "high", "timeout_seconds": 99})),
        _call("read", "read_file", json.dumps({"path": "a", "offset": -4, "limit": -2})),
        _call("write", "write_file", json.dumps({"path": "a", "content": "hello"})),
        _call("list-low", "list_files", json.dumps({"path": ".", "depth": 0})),
        _call("list-high", "list_files", json.dumps({"path": ".", "depth": 99})),
        _call("submit", "submit", json.dumps({"result": "done"})),
    ]
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([{"output": calls}])
    )

    result = await session.run(_request())

    assert workspace.actions == [
        ("shell", "low", 1),
        ("shell", "high", 9),
        ("read_file", "a", 0, 0),
        ("write_file", "a", "hello"),
        ("list_files", ".", 1),
        ("list_files", ".", 8),
    ]
    observations = [
        event for event in result.events if isinstance(event, ToolObservationEvent)
    ]
    assert [event.call_id for event in observations] == [call["call_id"] for call in calls]
    assert [event.submitted for event in observations] == [False] * 6 + [True]
    assert result.termination is RunnerTermination.SUBMITTED


async def test_terminal_adapter_executes_identical_calls_independently_until_exact_max_turns() -> None:
    repeated = _call("same-call", "shell", '{"command":"echo same"}')
    policy = ScriptedPolicy(
        [{"output": [copy.deepcopy(repeated), copy.deepcopy(repeated)]}] * 3
    )
    session, workspace, _, _ = await _open(
        policy=policy, plan=_effective_plan(max_turns=3)
    )

    result = await session.run(_request(max_turns=3))

    assert result.termination is RunnerTermination.MAX_TURNS
    assert result.turn_count == 3
    assert [turn.turn for turn in result.turns] == [1, 2, 3]
    assert workspace.actions == [("shell", "echo same", 9)] * 6
    calls = [event for event in result.events if isinstance(event, ToolCallEvent)]
    observations = [
        event for event in result.events if isinstance(event, ToolObservationEvent)
    ]
    assert [(event.turn, event.ordinal, event.call_id) for event in calls] == [
        (1, 0, "same-call"),
        (1, 1, "same-call"),
        (2, 0, "same-call"),
        (2, 1, "same-call"),
        (3, 0, "same-call"),
        (3, 1, "same-call"),
    ]
    assert len(observations) == 6
    assert isinstance(result.events[-1], RunnerTerminationEvent)
    assert result.events[-1].turns == 3


@pytest.mark.parametrize(
    ("name", "response", "expected", "workspace_calls"),
    [
        (
            "submitted",
            {"output": [_call("submit", "submit", '{"result":"done"}')]},
            RunnerTermination.SUBMITTED,
            0,
        ),
        (
            "assistant",
            {"output": [{"type": "message", "role": "assistant", "content": "done"}]},
            RunnerTermination.ASSISTANT_COMPLETE,
            0,
        ),
        (
            "invalid",
            {"output": [{"type": "reasoning", "content": "thinking"}]},
            RunnerTermination.INVALID_POLICY_OUTPUT,
            0,
        ),
        (
            "incomplete",
            {
                "output": [_call("must-not-run", "shell", '{"command":"unsafe"}')],
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            RunnerTermination.POLICY_INCOMPLETE,
            0,
        ),
    ],
)
async def test_terminal_adapter_freezes_all_non_iteration_terminal_dispositions(
    name: str,
    response: dict[str, Any],
    expected: RunnerTermination,
    workspace_calls: int,
) -> None:
    session, workspace, _, _ = await _open(policy=ScriptedPolicy([response]))

    result = await session.run(_request())

    assert result.termination is expected, name
    assert result.turn_count == 1
    assert len(workspace.actions) == workspace_calls
    assert isinstance(result.events[-1], RunnerTerminationEvent)
    assert result.events[-1].reason is expected


@pytest.mark.parametrize(
    ("result", "error_type", "error_code", "expected_error"),
    [
        (
            OSError("workspace offline"),
            "WorkspaceActionError",
            "workspace_action_failed",
            "WorkspaceActionError: workspace action failed",
        ),
        (
            ["untyped", "result"],
            "ValueError",
            "workspace_result_invalid",
            "ValueError: workspace result was invalid",
        ),
    ],
)
async def test_terminal_adapter_returns_workspace_failures_and_untyped_results_as_observations(
    result: Any, error_type: str, error_code: str, expected_error: str
) -> None:
    workspace: RecordingWorkspace
    if isinstance(result, list):
        workspace = UntypedWorkspace()
    else:
        workspace = RecordingWorkspace()
        workspace.override["shell"] = result
    policy = ScriptedPolicy(
        [
            {"output": [_call("failed", "shell", '{"command":"pwd"}')]},
            {"output": [{"type": "message", "role": "assistant", "content": "recovered"}]},
        ]
    )
    session, _, _, _ = await _open(policy=policy, workspace=workspace)

    result_value = await session.run(_request())

    event = next(
        event
        for event in result_value.events
        if isinstance(event, ToolObservationEvent)
    )
    observation = _thaw(event.observation)
    assert event.error_type == error_type
    assert observation["type"] == "function_call_output"
    assert observation["call_id"] == "failed"
    decoded_error = json.loads(observation["output"])
    assert decoded_error["code"] == error_code
    assert decoded_error["error"] == expected_error
    assert policy.requests[1]["input"][-1] == observation
    assert result_value.termination is RunnerTermination.ASSISTANT_COMPLETE


@pytest.mark.parametrize(
    ("call", "call_id"),
    [
        (_call("bad-type", "shell", {"command": "pwd"}), "bad-type"),
        (_call("bad-json", "shell", "{"), "bad-json"),
        (_call("array", "shell", "[]"), "array"),
        (_call("unknown", "delete_everything", "{}"), "unknown"),
        (_call("", "shell", "{}"), "missing-call-id"),
    ],
)
async def test_terminal_adapter_returns_tool_failures_as_observations_and_continues(
    call: dict[str, Any], call_id: str
) -> None:
    policy = ScriptedPolicy(
        [
            {"output": [call]},
            {"output": [{"type": "message", "role": "assistant", "content": "recovered"}]},
        ]
    )
    session, _, _, _ = await _open(policy=policy)

    result = await session.run(_request())

    observed = policy.requests[1]["input"][-1]
    assert observed["call_id"] == call_id
    assert json.loads(observed["output"])["code"] == "invalid_arguments"
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE


@pytest.mark.parametrize(
    ("response", "code", "message"),
    [
        ([], "policy_response_not_object", "policy response must be an object"),
        ({}, "policy_output_not_list", "policy response output must be a list"),
        ({"output": {}}, "policy_output_not_list", "policy response output must be a list"),
        ({"output": ["not-object"]}, "policy_output_item_not_object", "policy response output[0] must be an object"),
    ],
)
async def test_terminal_adapter_rejects_malformed_policy_outputs_with_typed_errors(
    response: Any, code: str, message: str
) -> None:
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([response]), sink=sink
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request())

    assert captured.value.code == code
    assert str(captured.value) == message
    assert captured.value.episode_id == "episode"
    assert captured.value.events_so_far == tuple(sink.events)
    assert workspace.actions == []
    error_event = sink.events[-1]
    assert isinstance(error_event, RunnerErrorEvent)
    assert (error_event.category, error_event.code, error_event.message) == (
        "protocol",
        code,
        message,
    )
    digest = sink.events[0].effective_plan_digest
    assert [_public_event_record(event) for event in sink.events] == [
        {
            "type": "PolicyRequestEvent",
            "sequence": 0,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "turn": 1,
            "request_payload": _default_policy_request_payload(),
        },
        {
            "type": "RunnerErrorEvent",
            "sequence": 1,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "category": "protocol",
            "code": code,
            "message": message,
            "turn": 1,
            "call_id": None,
        },
    ]


async def test_terminal_adapter_rejects_oversized_policy_response_before_accepting_it() -> None:
    plan = _effective_plan(response_bytes=256)
    response = {
        "output": [{"type": "message", "role": "assistant", "content": "x" * 300}]
    }
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([response]), plan=plan, sink=sink
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request())

    assert captured.value.code == "response_bytes_exceeded"
    assert str(captured.value) == "policy response exceeded the effective response byte limit"
    assert workspace.actions == []
    assert [type(event) for event in sink.events] == [PolicyRequestEvent, RunnerErrorEvent]
    error_event = sink.events[-1]
    assert isinstance(error_event, RunnerErrorEvent)
    assert (error_event.category, error_event.code) == (
        "protocol",
        "response_bytes_exceeded",
    )



async def test_terminal_adapter_rejects_accumulated_response_over_effective_limit() -> None:
    limit = 350
    command = "x" * 140
    responses = [
        {"output": [_call("one", "shell", json.dumps({"command": command}))]},
        {"output": [_call("two", "shell", json.dumps({"command": command}))]},
    ]
    assert all(
        len(json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        < limit
        for response in responses
    )
    plan = _effective_plan(max_turns=2, response_bytes=limit)
    policy = ScriptedPolicy(responses)
    session, workspace, _, sink = await _open(policy=policy, plan=plan)

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request(max_turns=2))

    assert len(policy.requests) == 1
    assert workspace.actions == [("shell", command, 9)]
    assert isinstance(sink.events[-1], RunnerErrorEvent)

@pytest.mark.parametrize(
    ("value_size", "limit", "truncated"),
    [(61, 70, False), (62, 70, False), (63, 70, True)],
)
async def test_terminal_adapter_observation_truncation_boundaries(
    value_size: int, limit: int, truncated: bool
) -> None:
    value = "a" * value_size
    workspace = RecordingWorkspace()
    workspace.override["shell"] = {"x": value}
    plan = _effective_plan(max_turns=1, observation_bytes=limit)
    policy = ScriptedPolicy(
        [{"output": [_call("shell", "shell", '{"command":"pwd"}')]}]
    )
    session, _, _, _ = await _open(
        policy=policy, workspace=workspace, plan=plan
    )

    result = await session.run(_request(max_turns=1, observation_chars=limit))

    observation_event = next(
        event for event in result.events if isinstance(event, ToolObservationEvent)
    )
    output = _thaw(observation_event.observation)["output"]
    decoded = json.loads(output)
    if truncated:
        assert decoded == {"preview": f'{{"x":"{value}'[: limit - 64], "truncated": True}
    else:
        assert decoded == {"x": value}


@pytest.mark.parametrize(
    ("checkpoint", "turn", "call_id", "expected_policy_calls", "expected_workspace_calls"),
    [
        ("before_policy", 1, None, 0, 0),
        ("after_policy", 1, None, 1, 0),
        ("before_action", 1, "second", 1, 1),
        ("after_loop", 1, None, 1, 1),
    ],
)
async def test_terminal_adapter_cancellation_is_typed_at_every_checkpoint(
    checkpoint: str,
    turn: int | None,
    call_id: str | None,
    expected_policy_calls: int,
    expected_workspace_calls: int,
) -> None:
    calls = [
        _call("first", "shell", '{"command":"first"}'),
        _call("second", "submit", '{"result":"done"}'),
    ]
    probe = ScriptedCancellationProbe()
    probe.cancel_on = (checkpoint, turn, call_id)
    sink = RecordingEventSink()
    policy = ScriptedPolicy([{"output": calls}])
    session, workspace, _, _ = await _open(
        policy=policy, cancellation=probe, sink=sink
    )

    with pytest.raises(RunnerCancelled) as captured:
        await session.run(_request())

    assert captured.value.code == "cancelled"
    assert captured.value.cancellation.reason == "external cancellation requested"
    assert captured.value.cancellation.observed_checkpoint == checkpoint
    assert captured.value.cancellation.turn == turn
    assert captured.value.cancellation.call_id == call_id
    assert len(policy.requests) == expected_policy_calls
    assert len(workspace.actions) == expected_workspace_calls
    assert isinstance(sink.events[-1], RunnerCancellationObservedEvent)
    assert isinstance(sink.events[-2], RunnerCancellationRequestedEvent)
    assert sink.events[-2].reason == sink.events[-1].reason == (
        "external cancellation requested"
    )
    assert [event.sequence for event in sink.events[-2:]] == [
        len(sink.events) - 2,
        len(sink.events) - 1,
    ]
    assert captured.value.events_so_far == tuple(sink.events)



async def test_terminal_adapter_observes_cancellation_between_turns() -> None:
    probe = ScriptedCancellationProbe()
    probe.cancel_on = ("before_policy", 2, None)
    policy = ScriptedPolicy(
        [
            {"output": [_call("first", "shell", '{"command":"first"}')]},
            {"output": [{"type": "message", "role": "assistant", "content": "too late"}]},
        ]
    )
    session, workspace, _, sink = await _open(
        policy=policy, cancellation=probe
    )

    with pytest.raises(RunnerCancelled) as captured:
        await session.run(_request())

    assert captured.value.cancellation.observed_checkpoint == "before_policy"
    assert captured.value.cancellation.turn == 2
    assert len(policy.requests) == 1
    assert workspace.actions == [("shell", "first", 9)]
    assert isinstance(sink.events[-1], RunnerCancellationObservedEvent)

async def test_terminal_adapter_cancel_before_start_and_close_cancel_are_idempotent() -> None:
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([{"output": []}]), sink=sink
    )

    first = await session.cancel("first reason")
    second = await session.cancel("ignored reason")
    assert first is second
    assert first.reason == "first reason"
    assert [type(event) for event in sink.events] == [RunnerCancellationRequestedEvent]

    with pytest.raises(RunnerCancelled) as captured:
        await session.run(_request())
    assert captured.value.cancellation.observed_checkpoint == "before_run"
    assert workspace.actions == []
    digest = sink.events[0].effective_plan_digest
    assert [_public_event_record(event) for event in sink.events] == [
        {
            "type": "RunnerCancellationRequestedEvent",
            "sequence": 0,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "reason": "first reason",
        },
        {
            "type": "RunnerCancellationObservedEvent",
            "sequence": 1,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "reason": "first reason",
            "checkpoint": "before_run",
            "turn": None,
            "call_id": None,
        },
    ]
    assert captured.value.events_so_far == tuple(sink.events)

    first_close = await session.close()
    second_close = await session.close()
    assert first_close.already_closed is False
    assert second_close.already_closed is True
    assert first_close.cancellation == second_close.cancellation == captured.value.cancellation
    with pytest.raises(RunnerStateError) as closed:
        await session.run(_request())
    assert closed.value.code == "session_closed"


async def test_terminal_adapter_is_single_run_even_after_success() -> None:
    session, _, _, _ = await _open(
        policy=ScriptedPolicy(
            [{"output": [{"type": "message", "role": "assistant", "content": "done"}]}]
        )
    )
    await session.run(_request())

    with pytest.raises(RunnerStateError) as captured:
        await session.run(_request())

    assert captured.value.code == "run_already_started"
    assert str(captured.value) == "runner session permits exactly one run"


async def test_terminal_adapter_external_policy_failure_is_typed_and_preserves_cause() -> None:
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([OSError("policy offline")]), sink=sink
    )

    with pytest.raises(RunnerDependencyError) as captured:
        await session.run(_request())

    assert captured.value.code == "policy_invoke_failed"
    assert str(captured.value) == "policy invocation failed"
    assert isinstance(captured.value.__cause__, OSError)
    assert str(captured.value.__cause__) == "policy offline"
    assert workspace.actions == []
    assert isinstance(sink.events[-1], RunnerErrorEvent)
    assert (sink.events[-1].category, sink.events[-1].code) == (
        "dependency",
        "policy_invoke_failed",
    )
    digest = sink.events[0].effective_plan_digest
    assert [_public_event_record(event) for event in sink.events] == [
        {
            "type": "PolicyRequestEvent",
            "sequence": 0,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "turn": 1,
            "request_payload": _default_policy_request_payload(),
        },
        {
            "type": "RunnerErrorEvent",
            "sequence": 1,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "category": "dependency",
            "code": "policy_invoke_failed",
            "message": "policy invocation failed",
            "turn": 1,
            "call_id": None,
        },
    ]
    assert captured.value.events_so_far == tuple(sink.events)


async def test_terminal_adapter_sink_failure_is_visible_and_excludes_failed_event() -> None:
    sink = RecordingEventSink()
    sink.fail_at = 2
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([{"output": []}]), sink=sink
    )

    with pytest.raises(RunnerEventSinkError) as captured:
        await session.run(_request())

    assert captured.value.code == "event_sink_failed"
    assert str(captured.value) == "runner event sink rejected an event"
    assert isinstance(captured.value.failed_event, PolicyResponseEvent)
    assert captured.value.events_so_far == tuple(sink.events)
    assert [type(event) for event in sink.events] == [PolicyRequestEvent]
    assert isinstance(captured.value.__cause__, OSError)
    assert workspace.actions == []
    digest = sink.events[0].effective_plan_digest
    assert [_public_event_record(event) for event in sink.events] == [
        {
            "type": "PolicyRequestEvent",
            "sequence": 0,
            "episode_id": "episode",
            "effective_plan_digest": digest,
            "turn": 1,
            "request_payload": _default_policy_request_payload(),
        }
    ]
    assert _public_event_record(captured.value.failed_event) == {
        "type": "PolicyResponseEvent",
        "sequence": 1,
        "episode_id": "episode",
        "effective_plan_digest": digest,
        "turn": 1,
        "response_payload": {"output": []},
        "normalized_output": [],
    }
    with pytest.raises(RunnerStateError) as repeated:
        await session.run(_request())
    assert repeated.value.code == "session_failed"


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("adapter_id", "other.adapter", "adapter_mismatch"),
        ("runtime_abi", "other-abi", "runtime_abi_mismatch"),
        ("implementation_digest", "sha256:" + "0" * 64, "implementation_digest_mismatch"),
    ],
)
async def test_terminal_adapter_rejects_plan_identity_mismatch_before_port_use(
    field: str, replacement: str, code: str
) -> None:
    kwargs = {field: replacement}
    plan = _effective_plan(**kwargs)
    policy = ScriptedPolicy([])
    workspace = RecordingWorkspace()
    probe = ScriptedCancellationProbe()
    sink = RecordingEventSink()
    adapter = TerminalResponsesAdapter(runtime_abi=RUNTIME_ABI)

    with pytest.raises(RunnerPlanError) as captured:
        await adapter.open(
            RunnerOpenRequest(episode_id="mismatch", effective_plan=plan),
            policy=policy,
            workspace=workspace,
            cancellation=probe,
            events=sink,
        )

    assert captured.value.code == code
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []
    assert sink.events == []


async def test_terminal_adapter_accepts_exact_canonical_tool_binding_tuple() -> None:
    workspace = RecordingWorkspace(tool_bindings=_plan_tool_bindings())
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "done"}]}]
    )
    session, _, _, _ = await _open(policy=policy, workspace=workspace)

    result = await session.run(_request())

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert workspace.actions == []


@pytest.mark.parametrize(
    ("name", "workspace_factory"),
    [
        (
            "implementation_digest",
            lambda: RecordingWorkspace(
                tool_bindings=(
                    RunnerToolBinding(
                        tool_id=_plan_tool_bindings()[0].tool_id,
                        implementation_digest=_digest("changed"),
                        capability_ids=(),
                    ),
                    *_plan_tool_bindings()[1:],
                )
            ),
        ),
        (
            "capability_ids",
            lambda: RecordingWorkspace(
                tool_bindings=(
                    RunnerToolBinding(
                        tool_id=_plan_tool_bindings()[0].tool_id,
                        implementation_digest=_plan_tool_bindings()[0].implementation_digest,
                        capability_ids=("workspace.read",),
                    ),
                    *_plan_tool_bindings()[1:],
                )
            ),
        ),
        (
            "tool_id",
            lambda: RecordingWorkspace(
                tool_bindings=(
                    RunnerToolBinding(
                        tool_id="different_tool",
                        implementation_digest=_plan_tool_bindings()[0].implementation_digest,
                        capability_ids=(),
                    ),
                    *_plan_tool_bindings()[1:],
                )
            ),
        ),
        (
            "missing",
            lambda: RecordingWorkspace(tool_bindings=_plan_tool_bindings()[:-1]),
        ),
        (
            "extra",
            lambda: RecordingWorkspace(
                tool_bindings=(
                    *_plan_tool_bindings(),
                    RunnerToolBinding(
                        tool_id="extra_tool",
                        implementation_digest=_digest("extra"),
                        capability_ids=(),
                    ),
                )
            ),
        ),
        (
            "duplicate",
            lambda: RecordingWorkspace(
                tool_bindings=(*_plan_tool_bindings(), _plan_tool_bindings()[0])
            ),
        ),
        (
            "noncanonical_order",
            lambda: RecordingWorkspace(
                tool_bindings=tuple(reversed(_plan_tool_bindings()))
            ),
        ),
        (
            "untyped_collection",
            lambda: RecordingWorkspace(tool_bindings=list(_plan_tool_bindings())),
        ),
        (
            "malformed_binding",
            lambda: RecordingWorkspace(tool_bindings=(object(),)),
        ),
        ("missing_property", MissingBindingsWorkspace),
        ("property_failure", RaisingBindingsWorkspace),
    ],
)
async def test_terminal_adapter_rejects_full_tool_binding_mismatch_before_effects(
    name: str, workspace_factory: Any
) -> None:
    policy = ScriptedPolicy([])
    workspace = workspace_factory()
    probe = ScriptedCancellationProbe()
    sink = RecordingEventSink()
    adapter = TerminalResponsesAdapter(runtime_abi=RUNTIME_ABI)

    with pytest.raises(RunnerPlanError) as captured:
        await adapter.open(
            RunnerOpenRequest(episode_id=f"binding-{name}", effective_plan=_effective_plan()),
            policy=policy,
            workspace=workspace,
            cancellation=probe,
            events=sink,
        )

    assert captured.value.code == "tool_grant_mismatch"
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []
    assert sink.events == []


async def test_terminal_adapter_rejects_limit_and_tool_projection_before_policy_call() -> None:
    cases = [
        (_request(max_turns=3), "limit_projection_mismatch"),
        (_request(tools=_tools()[:-1]), "tool_grant_mismatch"),
    ]
    for request, code in cases:
        policy = ScriptedPolicy([])
        sink = RecordingEventSink()
        session, workspace, probe, _ = await _open(policy=policy, sink=sink)
        with pytest.raises(RunnerPlanError) as captured:
            await session.run(request)
        assert captured.value.code == code
        assert policy.requests == []
        assert workspace.actions == []
        assert probe.checkpoints == []
        assert isinstance(sink.events[-1], RunnerErrorEvent)



async def test_terminal_adapter_rejects_modified_tool_schema_before_policy_call() -> None:
    schema = _thaw(TERMINAL_TOOL_DEFINITIONS[0].responses_schema)
    schema["description"] = "same ID, different model-visible contract"
    changed = (
        TerminalToolDefinition(tool_id=TERMINAL_TOOL_DEFINITIONS[0].tool_id, responses_schema=schema),
        *TERMINAL_TOOL_DEFINITIONS[1:],
    )
    policy = ScriptedPolicy([])
    session, workspace, probe, sink = await _open(policy=policy)

    with pytest.raises(RunnerRequestError) as captured:
        await session.run(_request(tools=changed))

    assert captured.value.code == "tool_schema_invalid"
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []
    assert isinstance(sink.events[-1], RunnerErrorEvent)

async def test_terminal_adapter_concurrent_instances_remain_isolated() -> None:
    async def run_one(episode_id: str, command: str) -> tuple[Any, Any, Any]:
        policy = ScriptedPolicy(
            [
                {"output": [_call(f"{episode_id}-call", "shell", json.dumps({"command": command}))]},
                {"output": [{"type": "message", "role": "assistant", "content": episode_id}]},
            ]
        )
        session, workspace, _, sink = await _open(
            policy=policy, episode_id=episode_id
        )
        result = await session.run(_request(params={"input": episode_id}))
        return result, workspace, sink

    first, second = await asyncio.gather(
        run_one("episode-a", "alpha"), run_one("episode-b", "beta")
    )

    first_result, first_workspace, first_sink = first
    second_result, second_workspace, second_sink = second
    assert first_workspace.actions == [("shell", "alpha", 9)]
    assert second_workspace.actions == [("shell", "beta", 9)]
    assert _thaw(first_result.original_request) == {"input": "episode-a"}
    assert _thaw(second_result.original_request) == {"input": "episode-b"}
    assert all(event.episode_id == "episode-a" for event in first_sink.events)
    assert all(event.episode_id == "episode-b" for event in second_sink.events)
    assert first_result.events == tuple(first_sink.events)
    assert second_result.events == tuple(second_sink.events)
    assert first_result.events is not second_result.events


SECRET_SENTINEL = "Authorization: Bearer wp5-secret-sentinel"
MIN_TRUNCATION_BYTES = len(b'{"truncated":true}')


class HostileCarrierError(RuntimeError):
    pass


class ThrowingCarrier(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        if key == "output":
            return []
        raise KeyError(key)

    def __iter__(self):
        return iter(("output",))

    def __len__(self) -> int:
        return 1

    def items(self):
        raise HostileCarrierError(SECRET_SENTINEL)


class ManyItemCarrier(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        if key == "output":
            return []
        if key.startswith("item-"):
            return key
        raise KeyError(key)

    def __iter__(self):
        yield "output"
        for index in range(10_000):
            yield f"item-{index}"

    def __len__(self) -> int:
        return 10_001

    def items(self):
        yield "output", []
        for index in range(10_000):
            value = f"item-{index}"
            yield value, value


class StatefulPolicyCarrier(Mapping[str, Any]):
    def __init__(self) -> None:
        self.unrecorded_call = _call(
            "unrecorded", "write_file", '{"path":"secret","content":"effect"}'
        )
        self.recorded_message = {
            "type": "message",
            "role": "assistant",
            "content": "recorded snapshot",
        }

    def __getitem__(self, key: str) -> Any:
        if key == "output":
            return [self.unrecorded_call]
        raise KeyError(key)

    def __iter__(self):
        return iter(("output",))

    def __len__(self) -> int:
        return 1

    def items(self):
        return iter((("output", [self.recorded_message]),))


class ThrowingWorkspaceResult(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise HostileCarrierError(SECRET_SENTINEL)

    def __iter__(self):
        raise HostileCarrierError(SECRET_SENTINEL)

    def __len__(self) -> int:
        return 1

    def items(self):
        raise HostileCarrierError(SECRET_SENTINEL)


class NonJsonWorkspaceValue:
    def __str__(self) -> str:
        raise HostileCarrierError(SECRET_SENTINEL)


class NeverReturningWorkspace(RecordingWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._never = asyncio.Event()

    async def _block(self, action: tuple[Any, ...]) -> Mapping[str, Any]:
        self.actions.append(action)
        self.started.set()
        try:
            await self._never.wait()
        finally:
            self.cancelled.set()
        raise AssertionError("unreachable")

    async def run_shell(self, command: str, *, timeout: int) -> Mapping[str, Any]:
        return await self._block(("shell", command, timeout))

    async def read_text(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> Mapping[str, Any]:
        return await self._block(("read_file", path, offset, limit))

    async def write_text(self, path: str, content: str) -> Mapping[str, Any]:
        return await self._block(("write_file", path, content))

    async def list_files(self, path: str, *, depth: int) -> Mapping[str, Any]:
        return await self._block(("list_files", path, depth))


class BlockingEventSink(RecordingEventSink):
    def __init__(self, blocked_type: type[Any]) -> None:
        super().__init__()
        self.blocked_type = blocked_type
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._blocked = False

    async def emit(self, event: RunnerEvent) -> None:
        if isinstance(event, self.blocked_type) and not self._blocked:
            self._blocked = True
            self.entered.set()
            await self.release.wait()
        self.events.append(event)




def _cyclic_policy_carrier() -> dict[str, Any]:
    item: dict[str, Any] = {"type": "reasoning"}
    item["cycle"] = item
    return {"output": [item]}


def _deep_policy_carrier() -> dict[str, Any]:
    nested: dict[str, Any] = {"leaf": True}
    for _ in range(70):
        nested = {"child": nested}
    return {"output": [{"type": "reasoning", "summary": nested}]}


def _cyclic_workspace_result() -> dict[str, Any]:
    result: dict[str, Any] = {}
    result["cycle"] = result
    return result


def _public_event_record(event: RunnerEvent) -> dict[str, Any]:
    record: dict[str, Any] = {"type": type(event).__name__}
    for event_field in fields(event):
        value = getattr(event, event_field.name)
        if isinstance(value, RunnerTermination):
            value = value.value
        else:
            value = _thaw(value)
        record[event_field.name] = value
    return record


def _public_turn_record(turn: RunnerTurn) -> dict[str, Any]:
    return {
        "turn": turn.turn,
        "policy_output": _thaw(turn.policy_output),
        "observations": _thaw(turn.observations),
    }


def _visible_text(value: Any) -> str:
    if isinstance(value, BaseException):
        return str(value)
    return json.dumps(_thaw(value), sort_keys=True, default=lambda _: "<non-json>")


@pytest.mark.parametrize(
    ("carrier_factory", "expected_code"),
    [
        (_cyclic_policy_carrier, "policy_response_invalid"),
        (_deep_policy_carrier, "response_bytes_exceeded"),
        (ThrowingCarrier, "policy_response_invalid"),
        (ManyItemCarrier, "response_bytes_exceeded"),
    ],
)
async def test_terminal_adapter_rejects_hostile_policy_carriers_before_effects(
    carrier_factory: Any, expected_code: str
) -> None:
    carrier = carrier_factory()
    sink = RecordingEventSink()
    plan = _effective_plan(response_bytes=256)
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([carrier]), plan=plan, sink=sink
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request())

    assert captured.value.code == expected_code
    if expected_code == "policy_response_invalid":
        assert str(captured.value) == "policy response is not bounded closed JSON"
    else:
        assert str(captured.value) == (
            "policy response exceeded the effective response byte limit"
        )
    assert workspace.actions == []
    assert not any(isinstance(event, PolicyResponseEvent) for event in sink.events)
    assert isinstance(sink.events[-1], RunnerErrorEvent)
    assert SECRET_SENTINEL not in str(captured.value)
    assert all(SECRET_SENTINEL not in _visible_text(event) for event in sink.events)


async def test_terminal_adapter_dispatches_only_the_single_recorded_policy_snapshot() -> None:
    carrier = StatefulPolicyCarrier()
    session, workspace, _, _ = await _open(policy=ScriptedPolicy([carrier]))

    result = await session.run(_request())

    assert workspace.actions == []
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    response_event = next(
        event for event in result.events if isinstance(event, PolicyResponseEvent)
    )
    assert _thaw(response_event.normalized_output) == [carrier.recorded_message]
    assert _thaw(response_event.response_payload) == {
        "output": [carrier.recorded_message]
    }


async def test_terminal_adapter_rejects_mixed_unknown_output_before_any_call() -> None:
    response = {
        "output": [
            {"type": "computer_call", "id": "unsupported"},
            _call("write", "write_file", '{"path":"effect","content":"x"}'),
            _call("submit", "submit", '{"result":"done"}'),
        ]
    }
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([response]), sink=sink
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request())

    assert captured.value.code == "policy_output_type_unsupported"
    assert str(captured.value) == "policy response contains an unsupported output type"
    assert workspace.actions == []
    assert not any(isinstance(event, ToolCallEvent) for event in sink.events)
    assert not any(isinstance(event, ToolObservationEvent) for event in sink.events)


@pytest.mark.parametrize(
    "result_factory",
    [
        _cyclic_workspace_result,
        ThrowingWorkspaceResult,
        lambda: {"value": NonJsonWorkspaceValue()},
        lambda: {"value": "é" * 10_000},
    ],
)
async def test_terminal_adapter_contains_invalid_workspace_results_as_bounded_observations(
    result_factory: Any,
) -> None:
    workspace = RecordingWorkspace()
    workspace.override["shell"] = result_factory()
    policy = ScriptedPolicy(
        [
            {"output": [_call("hostile", "shell", '{"command":"pwd"}')]},
            {"output": [{"type": "message", "role": "assistant", "content": "safe"}]},
        ]
    )
    plan = _effective_plan(observation_bytes=256)
    session, _, _, _ = await _open(policy=policy, workspace=workspace, plan=plan)

    result = await session.run(_request(observation_chars=256))

    observation_event = next(
        event for event in result.events if isinstance(event, ToolObservationEvent)
    )
    decoded = json.loads(_thaw(observation_event.observation)["output"])
    assert decoded["code"] == "workspace_result_invalid"
    assert observation_event.error_type == "ValueError"
    assert len(_thaw(observation_event.observation)["output"].encode("utf-8")) <= 256
    assert policy.requests[1]["input"][-1] == _thaw(observation_event.observation)
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert SECRET_SENTINEL not in _visible_text(result.response)
    assert all(SECRET_SENTINEL not in _visible_text(event) for event in result.events)


async def test_terminal_adapter_enforces_runner_timeout_for_every_workspace_method() -> None:
    cases = [
        ("shell", '{"command":"pwd"}', ("shell", "pwd", 1)),
        ("read_file", '{"path":"a"}', ("read_file", "a", 0, None)),
        ("write_file", '{"path":"a","content":"x"}', ("write_file", "a", "x")),
        ("list_files", '{"path":"."}', ("list_files", ".", 1)),
    ]
    runs: list[tuple[Any, NeverReturningWorkspace, ScriptedPolicy]] = []
    for tool_name, arguments, _ in cases:
        workspace = NeverReturningWorkspace()
        policy = ScriptedPolicy(
            [
                {"output": [_call(tool_name, tool_name, arguments)]},
                {"output": [{"type": "message", "role": "assistant", "content": "after timeout"}]},
            ]
        )
        session, _, _, _ = await _open(
            policy=policy,
            workspace=workspace,
            plan=_effective_plan(action_timeout_ms=1_000),
        )
        runs.append((session.run(_request(timeout=1)), workspace, policy))

    async with asyncio.timeout(3):
        results = await asyncio.gather(*(run for run, _, _ in runs))

    for result, (_, workspace, policy), (_, _, expected_action) in zip(
        results, runs, cases, strict=True
    ):
        assert workspace.actions == [expected_action]
        assert workspace.cancelled.is_set()
        observation_event = next(
            event for event in result.events if isinstance(event, ToolObservationEvent)
        )
        decoded = json.loads(_thaw(observation_event.observation)["output"])
        assert decoded["code"] == "action_timeout"
        assert observation_event.error_type == "TimeoutError"
        assert len(policy.requests) == 2
        assert result.termination is RunnerTermination.ASSISTANT_COMPLETE


@pytest.mark.parametrize("tool_name", ["write_file", "submit"])
@pytest.mark.parametrize(
    ("present", "bad_value"),
    [
        pytest.param(False, MISSING_ARGUMENT, id="missing"),
        pytest.param(True, None, id="null"),
        pytest.param(True, True, id="bool"),
        pytest.param(True, {}, id="object"),
    ],
)
async def test_terminal_adapter_rejects_invalid_write_and_submit_strings_without_effects(
    tool_name: str, present: bool, bad_value: Any
) -> None:
    arguments: dict[str, Any] = {"path": "effect"} if tool_name == "write_file" else {}
    key = "content" if tool_name == "write_file" else "result"
    if present:
        arguments[key] = bad_value
    policy = ScriptedPolicy(
        [
            {"output": [_call("invalid", tool_name, json.dumps(arguments))]},
            {"output": [{"type": "message", "role": "assistant", "content": "continued"}]},
        ]
    )
    session, workspace, _, _ = await _open(policy=policy)

    result = await session.run(_request())

    assert workspace.actions == []
    observation = next(
        event for event in result.events if isinstance(event, ToolObservationEvent)
    )
    assert json.loads(_thaw(observation.observation)["output"])["code"] == "invalid_arguments"
    assert observation.submitted is False
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE


async def test_terminal_adapter_accepts_explicit_empty_write_content_and_submit_result() -> None:
    policy = ScriptedPolicy(
        [
            {
                "output": [
                    _call("write", "write_file", '{"path":"empty","content":""}'),
                    _call("submit", "submit", '{"result":""}'),
                ]
            }
        ]
    )
    session, workspace, _, _ = await _open(policy=policy)

    result = await session.run(_request())

    assert workspace.actions == [("write_file", "empty", "")]
    assert result.termination is RunnerTermination.SUBMITTED
    submit_event = next(
        event
        for event in result.events
        if isinstance(event, ToolObservationEvent) and event.call_id == "submit"
    )
    assert json.loads(_thaw(submit_event.observation)["output"]) == {
        "accepted": True,
        "result": "",
    }


async def test_terminal_adapter_rejects_every_subminimum_observation_limit_before_ports() -> None:
    for limit in range(1, MIN_TRUNCATION_BYTES):
        policy = ScriptedPolicy([])
        sink = RecordingEventSink()
        session, workspace, probe, _ = await _open(
            policy=policy,
            sink=sink,
            plan=_effective_plan(observation_bytes=limit),
        )
        with pytest.raises(RunnerPlanError) as captured:
            await session.run(_request(observation_chars=limit))
        assert captured.value.code == "limit_projection_mismatch"
        assert policy.requests == []
        assert workspace.actions == []
        assert probe.checkpoints == []
        assert [_public_event_record(event)["type"] for event in sink.events] == [
            "RunnerErrorEvent"
        ]


async def test_terminal_adapter_minimum_observation_envelope_fits_utf8_bytes_exactly() -> None:
    workspace = RecordingWorkspace()
    workspace.override["shell"] = {"value": "é" * 100}
    session, _, _, _ = await _open(
        policy=ScriptedPolicy(
            [{"output": [_call("unicode", "shell", '{"command":"pwd"}')]}]
        ),
        workspace=workspace,
        plan=_effective_plan(max_turns=1, observation_bytes=MIN_TRUNCATION_BYTES),
    )

    result = await session.run(
        _request(max_turns=1, observation_chars=MIN_TRUNCATION_BYTES)
    )

    observation = next(
        event for event in result.events if isinstance(event, ToolObservationEvent)
    )
    output = _thaw(observation.observation)["output"]
    assert output == '{"truncated":true}'
    assert len(output.encode("utf-8")) == MIN_TRUNCATION_BYTES


@pytest.mark.parametrize(
    ("plan_observation", "request_observation", "plan_timeout", "request_timeout", "accepted"),
    [
        (70, 69, 9_000, 9, False),
        (70, 70, 9_000, 9, True),
        (70, 71, 9_000, 9, False),
        (70, 70, 9_000, 8, False),
    ],
)
async def test_terminal_adapter_requires_exact_observation_and_timeout_projection_before_effects(
    plan_observation: int,
    request_observation: int,
    plan_timeout: int,
    request_timeout: int,
    accepted: bool,
) -> None:
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "done"}]}]
    )
    sink = RecordingEventSink()
    session, workspace, probe, _ = await _open(
        policy=policy,
        sink=sink,
        plan=_effective_plan(
            observation_bytes=plan_observation, action_timeout_ms=plan_timeout
        ),
    )
    request = _request(
        observation_chars=request_observation, timeout=request_timeout
    )
    if accepted:
        result = await session.run(request)
        assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
        assert len(policy.requests) == 1
    else:
        with pytest.raises(RunnerPlanError) as captured:
            await session.run(request)
        assert captured.value.code == "limit_projection_mismatch"
        assert policy.requests == []
        assert workspace.actions == []
        assert probe.checkpoints == []
        assert [type(event) for event in sink.events] == [RunnerErrorEvent]


async def test_terminal_adapter_close_wins_before_termination_commit_and_is_observed() -> None:
    sink = BlockingEventSink(PolicyResponseEvent)
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "too late"}]}]
    )
    session, workspace, _, _ = await _open(policy=policy, sink=sink)
    run_task = asyncio.create_task(session.run(_request()))
    await sink.entered.wait()
    close_task = asyncio.create_task(session.close())

    sink.release.set()
    close_result = await close_task
    with pytest.raises(RunnerCancelled) as captured:
        await run_task

    assert close_result.cancellation is not None
    assert close_result.cancellation.requested is True
    assert captured.value.cancellation.observed_checkpoint == "after_loop"
    assert workspace.actions == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyResponseEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2, 3]
    assert sink.events[2].reason == sink.events[3].reason == "runner session closed"
    assert captured.value.events_so_far == tuple(sink.events)


async def test_terminal_request_recursively_resnapshots_a_forged_frozen_wrapper() -> None:
    mutable_input = [{"role": "user", "content": "original"}]
    forged = runner_base._FrozenJsonObject({"input": mutable_input})
    request = _request(params=forged)
    mutable_input[0]["content"] = "mutated"
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "done"}]}]
    )
    session, _, _, _ = await _open(policy=policy)

    result = await session.run(request)

    assert policy.requests[0]["input"] == [{"role": "user", "content": "original"}]
    assert _thaw(result.original_request) == {
        "input": [{"role": "user", "content": "original"}]
    }


class ForeignToolDefinition:
    @property
    def tool_id(self) -> str:
        raise HostileCarrierError(SECRET_SENTINEL)


class TerminalToolSubclass(TerminalToolDefinition):
    pass


@pytest.mark.parametrize(
    "invalid_tool",
    [object(), ForeignToolDefinition(), TerminalToolSubclass("shell", {"type": "function"})],
)
def test_terminal_request_rejects_every_nonexact_tool_definition(
    invalid_tool: Any,
) -> None:
    with pytest.raises(
        TypeError, match="tools must contain only TerminalToolDefinition values"
    ):
        _request(tools=(invalid_tool,))


async def test_terminal_adapter_serializes_blocked_run_cancel_and_close_events() -> None:
    sink = BlockingEventSink(PolicyRequestEvent)
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "late"}]}]
    )
    session, workspace, _, _ = await _open(policy=policy, sink=sink)
    run_task = asyncio.create_task(session.run(_request()))
    await sink.entered.wait()
    cancel_task = asyncio.create_task(session.cancel("operator first"))
    await _advance_loop_turns(3)
    assert not cancel_task.done()
    close_task = asyncio.create_task(session.close())

    sink.release.set()
    cancellation = await cancel_task
    close_result = await close_task
    with pytest.raises(RunnerCancelled) as captured:
        await run_task

    assert cancellation.requested is True
    assert close_result.cancellation == cancellation
    assert workspace.actions == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2]
    assert sink.events[1].reason == sink.events[2].reason == "operator first"
    assert sink.events[2].checkpoint == "after_policy"
    assert captured.value.events_so_far == tuple(sink.events)


async def test_terminal_adapter_completion_commit_beats_close_blocked_on_termination_sink() -> None:
    sink = BlockingEventSink(RunnerTerminationEvent)
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "done"}]}]
    )
    session, workspace, _, _ = await _open(policy=policy, sink=sink)
    run_task = asyncio.create_task(session.run(_request()))
    await sink.entered.wait()

    close_task = asyncio.create_task(session.close())
    late_cancel_task = asyncio.create_task(session.cancel("too late"))
    sink.release.set()
    result = await run_task
    close_result = await close_task
    late_cancel = await late_cancel_task
    assert close_result.cancellation is None
    assert late_cancel.requested is False
    assert late_cancel.reason == "too late"
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert workspace.actions == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyResponseEvent,
        RunnerTerminationEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2]


async def test_terminal_adapter_tiny_transcript_limit_stops_before_policy_or_workspace() -> None:
    policy = ScriptedPolicy([])
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=policy, sink=sink, plan=_effective_plan(transcript_bytes=1)
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request())

    assert captured.value.code == "transcript_bytes_exceeded"
    assert str(captured.value) == (
        "runner transcript exceeded the effective transcript byte limit"
    )
    assert policy.requests == []
    assert workspace.actions == []
    assert all(not isinstance(event, PolicyRequestEvent) for event in sink.events)


async def test_terminal_adapter_multiturn_transcript_stops_at_first_breach() -> None:
    command = "x" * 300
    policy = ScriptedPolicy(
        [
            {"output": [_call("first", "shell", json.dumps({"command": command}))]},
            {"output": [_call("second", "shell", json.dumps({"command": "too late"}))]},
        ]
    )
    session, workspace, _, _ = await _open(
        policy=policy,
        plan=_effective_plan(max_turns=2, transcript_bytes=4_500),
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request(max_turns=2))

    assert captured.value.code == "transcript_bytes_exceeded"
    assert len(policy.requests) == 1
    assert workspace.actions == [("shell", command, 9)]


async def test_terminal_adapter_redacts_policy_and_workspace_secret_sentinels() -> None:
    policy_sink = RecordingEventSink()
    policy_session, _, _, _ = await _open(
        policy=ScriptedPolicy([OSError(SECRET_SENTINEL)]), sink=policy_sink
    )
    with pytest.raises(RunnerDependencyError) as policy_error:
        await policy_session.run(_request())
    assert str(policy_error.value) == "policy invocation failed"
    assert SECRET_SENTINEL not in str(policy_error.value)
    assert all(
        SECRET_SENTINEL not in _visible_text(event) for event in policy_sink.events
    )

    workspace = RecordingWorkspace()
    workspace.override["shell"] = OSError(SECRET_SENTINEL)
    workspace_session, _, _, _ = await _open(
        policy=ScriptedPolicy(
            [
                {"output": [_call("secret", "shell", '{"command":"pwd"}')]},
                {"output": [{"type": "message", "role": "assistant", "content": "safe"}]},
            ]
        ),
        workspace=workspace,
    )
    result = await workspace_session.run(_request())
    assert SECRET_SENTINEL not in _visible_text(result.response)
    assert all(SECRET_SENTINEL not in _visible_text(event) for event in result.events)


@pytest.mark.parametrize("foreign_member", ["turn", "event"])
def test_runner_result_rejects_foreign_mutable_turns_and_events(
    foreign_member: str,
) -> None:
    digest = _digest("result")
    valid_turn = RunnerTurn(
        turn=1,
        policy_output=({"type": "message", "role": "assistant", "content": "done"},),
    )

    class MutableEvent:
        sequence = 0
        episode_id = "episode"
        effective_plan_digest = digest

    turns: tuple[Any, ...] = (object(),) if foreign_member == "turn" else (valid_turn,)
    events: tuple[Any, ...] = (MutableEvent(),) if foreign_member == "event" else ()
    with pytest.raises(TypeError):
        RunnerResult(
            episode_id="episode",
            effective_plan_digest=digest,
            original_request={"input": "work"},
            response={"output": []},
            termination=RunnerTermination.ASSISTANT_COMPLETE,
            turn_count=1,
            turns=turns,
            events=events,
        )


def _huge_text(kind: str) -> str:
    unit = {"ascii": "a", "multibyte": "é", "escaped": "\n"}[kind]
    return unit * 100_000


class PoisonAfterOversizedString(Mapping[str, Any]):
    def __init__(
        self, text: str, *, prefix: tuple[tuple[str, Any], ...] = ()
    ) -> None:
        self.text = text
        self.prefix = prefix
        self.visited: list[str] = []

    def __getitem__(self, key: str) -> Any:
        values = dict(self.prefix)
        if key == "oversized":
            return self.text
        return values[key]

    def __iter__(self):
        return iter((*dict(self.prefix), "oversized"))

    def __len__(self) -> int:
        return len(self.prefix) + 1

    def items(self):
        for key, value in self.prefix:
            self.visited.append(key)
            yield key, value
        self.visited.append("oversized")
        yield "oversized", self.text
        self.visited.append("past-budget")
        raise HostileCarrierError(SECRET_SENTINEL)


@pytest.mark.parametrize(
    ("kind", "expected_string_units"),
    [("ascii", 31), ("multibyte", 6), ("escaped", 16)],
)
def test_json_snapshot_stops_inside_single_string_at_encoded_budget_plus_one(
    kind: str, expected_string_units: int
) -> None:
    text = _huge_text(kind)

    with pytest.raises(JsonSnapshotError) as captured:
        runner_base.freeze_json(text, max_encoded_bytes=32)

    assert captured.value.code == "encoded_bytes"
    assert captured.value.encoded_bytes_examined == 33
    assert captured.value.string_units_examined == expected_string_units
    assert captured.value.string_units_examined < len(text)


@pytest.mark.parametrize("kind", ["ascii", "multibyte", "escaped"])
async def test_policy_snapshot_stops_before_member_after_oversized_single_string(
    kind: str,
) -> None:
    carrier = PoisonAfterOversizedString(
        _huge_text(kind), prefix=(("output", []),)
    )
    policy = ScriptedPolicy([carrier])
    sink = RecordingEventSink()
    session, workspace, probe, _ = await _open(
        policy=policy, sink=sink, plan=_effective_plan(response_bytes=128)
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request())

    assert captured.value.code == "response_bytes_exceeded"
    assert isinstance(captured.value.__cause__, JsonSnapshotError)
    assert captured.value.__cause__.encoded_bytes_examined == 129
    assert captured.value.__cause__.string_units_examined < len(carrier.text)
    assert carrier.visited == ["output", "oversized"]
    assert workspace.actions == []
    assert probe.checkpoints == [("before_policy", 1, None), ("after_policy", 1, None)]
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        RunnerErrorEvent,
    ]
    assert SECRET_SENTINEL not in _visible_text(captured.value)


@pytest.mark.parametrize("kind", ["ascii", "multibyte", "escaped"])
async def test_workspace_snapshot_stops_before_member_after_oversized_single_string(
    kind: str,
) -> None:
    carrier = PoisonAfterOversizedString(_huge_text(kind))
    workspace = RecordingWorkspace()
    workspace.override["shell"] = carrier
    policy = ScriptedPolicy(
        [
            {"output": [_call("bounded", "shell", '{"command":"pwd"}')]},
            {"output": [{"type": "message", "role": "assistant", "content": "safe"}]},
        ]
    )
    session, _, _, sink = await _open(
        policy=policy,
        workspace=workspace,
        plan=_effective_plan(observation_bytes=128),
    )

    result = await session.run(_request(observation_chars=128))

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert carrier.visited == ["oversized"]
    assert workspace.actions == [("shell", "pwd", 9)]
    observation = next(
        event for event in sink.events if isinstance(event, ToolObservationEvent)
    )
    assert observation.error_type == "ValueError"
    assert json.loads(observation.observation["output"]) == {
        "code": "workspace_result_invalid",
        "error": "ValueError: workspace result was invalid",
    }
    assert SECRET_SENTINEL not in _visible_text(result.response)


@pytest.mark.parametrize("kind", ["ascii", "multibyte", "escaped"])
async def test_transcript_budget_stops_inside_huge_request_string_before_policy(
    kind: str,
) -> None:
    text = _huge_text(kind)
    policy = ScriptedPolicy([])
    sink = RecordingEventSink()
    session, workspace, probe, _ = await _open(
        policy=policy,
        sink=sink,
        plan=_effective_plan(transcript_bytes=128),
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request(params={"input": text}))

    assert captured.value.code == "transcript_bytes_exceeded"
    assert isinstance(captured.value.__cause__, JsonSnapshotError)
    assert captured.value.__cause__.encoded_bytes_examined == 127
    assert 0 < captured.value.__cause__.string_units_examined < len(text)
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == [("before_policy", 1, None)]
    assert [type(event) for event in sink.events] == [RunnerErrorEvent]


@pytest.mark.parametrize(
    ("kind", "expected_remaining_breach"),
    [("ascii", 301), ("multibyte", 301), ("escaped", 301)],
)
async def test_accumulated_response_budget_stops_inside_first_huge_observation(
    kind: str, expected_remaining_breach: int
) -> None:
    text = _huge_text(kind)
    workspace = RecordingWorkspace()
    workspace.override["shell"] = {"payload": text}
    policy = ScriptedPolicy(
        [
            {
                "output": [
                    _call("first", "shell", '{"command":"first"}'),
                    _call("must-not-run", "shell", '{"command":"later"}'),
                ]
            }
        ]
    )
    sink = RecordingEventSink()
    session, _, probe, _ = await _open(
        policy=policy,
        workspace=workspace,
        sink=sink,
        plan=_effective_plan(
            response_bytes=512,
            observation_bytes=100_000,
            transcript_bytes=1_000_000,
        ),
    )

    with pytest.raises(RunnerProtocolError) as captured:
        await session.run(_request(observation_chars=100_000))

    assert captured.value.code == "response_bytes_exceeded"
    assert isinstance(captured.value.__cause__, JsonSnapshotError)
    assert captured.value.__cause__.encoded_bytes_examined == expected_remaining_breach
    assert 0 < captured.value.__cause__.string_units_examined < len(text) * 6
    assert workspace.actions == [("shell", "first", 9)]
    assert probe.checkpoints == [
        ("before_policy", 1, None),
        ("after_policy", 1, None),
        ("before_action", 1, "first"),
    ]
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyResponseEvent,
        ToolCallEvent,
        ToolObservationEvent,
        RunnerErrorEvent,
    ]


class ReentrantLifecycleSink(RecordingEventSink):
    def __init__(self, operation: str) -> None:
        super().__init__()
        self.operation = operation
        self.session: Any = None
        self.callback_result: Any = None
        self._triggered = False

    async def emit(self, event: RunnerEvent) -> None:
        self.events.append(event)
        if isinstance(event, PolicyRequestEvent) and not self._triggered:
            self._triggered = True
            if self.operation == "cancel":
                self.callback_result = await self.session.cancel("sink requested stop")
            else:
                self.callback_result = await self.session.close()


async def _advance_loop_turns(count: int) -> None:
    loop = asyncio.get_running_loop()
    for _ in range(count):
        next_turn = loop.create_future()
        loop.call_soon(next_turn.set_result, None)
        await next_turn


async def _finish_within_loop_turns(task: asyncio.Task[Any]) -> Any:
    loop = asyncio.get_running_loop()
    for _ in range(100):
        if task.done():
            return await task
        next_turn = loop.create_future()
        loop.call_soon(next_turn.set_result, None)
        await next_turn
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    raise AssertionError("operation did not complete within 100 deterministic loop turns")


@pytest.mark.parametrize(
    ("operation", "reason"),
    [("cancel", "sink requested stop"), ("close", "runner session closed")],
)
async def test_event_sink_can_await_session_lifecycle_without_deadlock(
    operation: str, reason: str
) -> None:
    sink = ReentrantLifecycleSink(operation)
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy(
            [{"output": [{"type": "message", "role": "assistant", "content": "late"}]}]
        ),
        sink=sink,
    )
    sink.session = session
    run_task = asyncio.create_task(session.run(_request()))

    with pytest.raises(RunnerCancelled) as captured:
        await _finish_within_loop_turns(run_task)

    provisional = (
        sink.callback_result
        if operation == "cancel"
        else sink.callback_result.cancellation
    )
    assert isinstance(provisional, RunnerCancellation)
    assert provisional == RunnerCancellation(reason=reason, requested=False)
    if operation == "close":
        assert sink.callback_result == RunnerCloseResult(
            already_closed=False, cancellation=provisional
        )
        assert (await session.close()).already_closed is True
    assert workspace.actions == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2]
    assert sink.events[1].reason == sink.events[2].reason == reason
    assert sink.events[2].checkpoint == "after_policy"
    assert captured.value.events_so_far == tuple(sink.events)


class RejectRequestedEventOnceSink(RecordingEventSink):
    def __init__(self) -> None:
        super().__init__()
        self.failed_events: list[RunnerEvent] = []

    async def emit(self, event: RunnerEvent) -> None:
        if isinstance(event, RunnerCancellationRequestedEvent) and not self.failed_events:
            self.failed_events.append(event)
            raise OSError("cancellation journal unavailable")
        self.events.append(event)


async def test_rejected_cancellation_request_never_commits_or_launders_observation() -> None:
    sink = RejectRequestedEventOnceSink()
    policy = ScriptedPolicy([{"output": []}])
    session, workspace, probe, _ = await _open(policy=policy, sink=sink)

    with pytest.raises(RunnerEventSinkError) as rejected:
        await session.cancel("operator stop")

    assert rejected.value.events_so_far == ()
    assert _public_event_record(rejected.value.failed_event) == {
        "type": "RunnerCancellationRequestedEvent",
        "sequence": 0,
        "episode_id": "episode",
        "effective_plan_digest": rejected.value.effective_plan_digest,
        "reason": "operator stop",
    }
    with pytest.raises(RunnerStateError) as repeated_cancel:
        await session.cancel("must not be accepted")
    assert repeated_cancel.value.code == "session_failed"
    with pytest.raises(RunnerStateError) as run_after_failure:
        await session.run(_request())
    assert run_after_failure.value.code == "session_failed"
    first_close = await session.close()
    second_close = await session.close()
    assert first_close == RunnerCloseResult(already_closed=False, cancellation=None)
    assert second_close == RunnerCloseResult(already_closed=True, cancellation=None)
    assert sink.events == []
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []


class BlockingPolicy(ScriptedPolicy):
    def __init__(self) -> None:
        super().__init__([{"output": []}])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        request = copy.deepcopy(dict(request_payload))
        self.requests.append(request)
        self.entered.set()
        await self.release.wait()
        return self.responses.pop(0)


async def test_active_cancel_then_close_marks_closed_and_second_close_is_idempotent() -> None:
    policy = BlockingPolicy()
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(policy=policy, sink=sink)
    run_task = asyncio.create_task(session.run(_request()))
    await policy.entered.wait()

    cancellation = await session.cancel("operator first")
    first_close = await session.close()
    second_close = await session.close()
    policy.release.set()
    with pytest.raises(RunnerCancelled) as captured:
        await run_task

    assert cancellation == RunnerCancellation(reason="operator first", requested=True)
    assert first_close == RunnerCloseResult(
        already_closed=False, cancellation=cancellation
    )
    assert second_close == RunnerCloseResult(
        already_closed=True, cancellation=cancellation
    )
    assert workspace.actions == []
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2]
    assert sink.events[1].reason == sink.events[2].reason == "operator first"
    assert sink.events[2].checkpoint == "after_policy"
    assert captured.value.events_so_far == tuple(sink.events)


MALICIOUS_EXCEPTION_NAME = "Authorization_Bearer_wp5_secret_class"
MaliciousWorkspaceException = type(MALICIOUS_EXCEPTION_NAME, (RuntimeError,), {})


class SecretWorkspaceMethodLookup(RecordingWorkspace):
    def __getattribute__(self, name: str) -> Any:
        if name == "run_shell":
            raise OSError(SECRET_SENTINEL)
        return super().__getattribute__(name)


@pytest.mark.parametrize(
    "workspace",
    [
        pytest.param(
            RecordingWorkspace(),
            id="malicious-exception-class-name",
        ),
        pytest.param(SecretWorkspaceMethodLookup(), id="method-lookup-secret"),
    ],
)
async def test_workspace_dependency_failures_use_fixed_public_type_and_redact_secrets(
    workspace: RecordingWorkspace,
) -> None:
    if type(workspace) is RecordingWorkspace:
        workspace.override["shell"] = MaliciousWorkspaceException(SECRET_SENTINEL)
    policy = ScriptedPolicy(
        [
            {"output": [_call("secret", "shell", '{"command":"pwd"}')]},
            {"output": [{"type": "message", "role": "assistant", "content": "safe"}]},
        ]
    )
    session, _, _, sink = await _open(policy=policy, workspace=workspace)

    result = await session.run(_request())

    observation = next(
        event for event in sink.events if isinstance(event, ToolObservationEvent)
    )
    assert observation.error_type == "WorkspaceActionError"
    assert json.loads(observation.observation["output"]) == {
        "code": "workspace_action_failed",
        "error": "WorkspaceActionError: workspace action failed",
    }
    visible = _visible_text(
        {
            "policy_requests": policy.requests,
            "response": result.response,
            "events": [_public_event_record(event) for event in result.events],
        }
    )
    assert SECRET_SENTINEL not in visible
    assert MALICIOUS_EXCEPTION_NAME not in visible


async def test_cancellation_probe_secret_uses_fixed_requested_and_observed_reason() -> None:
    probe = ScriptedCancellationProbe()
    probe.cancel_on = ("before_policy", 1, None)
    probe.reason = SECRET_SENTINEL
    sink = RecordingEventSink()
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy([]), cancellation=probe, sink=sink
    )

    with pytest.raises(RunnerCancelled) as captured:
        await session.run(_request())

    assert isinstance(captured.value.__cause__, CancellationSignal)
    assert captured.value.cancellation.reason == "external cancellation requested"
    assert [type(event) for event in sink.events] == [
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1]
    assert sink.events[0].reason == sink.events[1].reason == (
        "external cancellation requested"
    )
    assert sink.events[1].checkpoint == "before_policy"
    assert SECRET_SENTINEL not in str(captured.value)
    assert all(SECRET_SENTINEL not in _visible_text(event) for event in sink.events)
    assert workspace.actions == []


def _build_event(kind: str, **overrides: Any) -> RunnerEvent:
    identity: dict[str, Any] = {
        "sequence": 0,
        "episode_id": "episode",
        "effective_plan_digest": _digest("event"),
    }
    values: dict[str, dict[str, Any]] = {
        "policy_request": {"turn": 1, "request_payload": {"input": "work"}},
        "policy_response": {
            "turn": 1,
            "response_payload": {"output": []},
            "normalized_output": (),
        },
        "tool_call": {
            "turn": 1,
            "ordinal": 0,
            "call_id": "call",
            "tool_name": "shell",
            "arguments_json": "{}",
        },
        "tool_observation": {
            "turn": 1,
            "ordinal": 0,
            "call_id": "call",
            "tool_name": "shell",
            "observation": {"type": "function_call_output"},
            "submitted": False,
            "error_type": None,
        },
        "termination": {"turns": 1, "reason": RunnerTermination.SUBMITTED},
        "cancellation_requested": {"reason": "stop"},
        "cancellation_observed": {
            "reason": "stop",
            "checkpoint": "before_policy",
            "turn": 1,
            "call_id": None,
        },
        "error": {
            "category": "protocol",
            "code": "bad_output",
            "message": "bad output",
            "turn": 1,
            "call_id": None,
        },
    }
    constructors = {
        "policy_request": PolicyRequestEvent,
        "policy_response": PolicyResponseEvent,
        "tool_call": ToolCallEvent,
        "tool_observation": ToolObservationEvent,
        "termination": RunnerTerminationEvent,
        "cancellation_requested": RunnerCancellationRequestedEvent,
        "cancellation_observed": RunnerCancellationObservedEvent,
        "error": RunnerErrorEvent,
    }
    return constructors[kind](**(identity | values[kind] | overrides))


@pytest.mark.parametrize(
    ("kind", "field_name", "invalid"),
    [
        ("policy_request", "turn", []),
        ("policy_response", "turn", []),
        ("tool_call", "turn", []),
        ("tool_call", "ordinal", []),
        ("tool_call", "call_id", []),
        ("tool_call", "tool_name", []),
        ("tool_call", "arguments_json", {}),
        ("tool_observation", "turn", []),
        ("tool_observation", "ordinal", []),
        ("tool_observation", "call_id", []),
        ("tool_observation", "tool_name", []),
        ("tool_observation", "submitted", 0),
        ("tool_observation", "error_type", []),
        ("termination", "turns", []),
        ("termination", "reason", "submitted"),
        ("cancellation_requested", "reason", []),
        ("cancellation_observed", "reason", []),
        ("cancellation_observed", "checkpoint", []),
        ("cancellation_observed", "turn", []),
        ("cancellation_observed", "call_id", []),
        ("error", "category", []),
        ("error", "code", []),
        ("error", "message", []),
        ("error", "turn", []),
        ("error", "call_id", []),
    ],
)
def test_every_event_variant_rejects_wrong_typed_scalar_members(
    kind: str, field_name: str, invalid: Any
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _build_event(kind, **{field_name: invalid})


def test_structured_event_members_are_recursively_snapshotted() -> None:
    request_payload = {"input": [{"content": "original"}]}
    response_payload = {"output": [{"type": "reasoning", "summary": ["original"]}]}
    normalized_output = [{"type": "reasoning", "summary": ["original"]}]
    observation = {"output": {"nested": ["original"]}}
    request_event = _build_event("policy_request", request_payload=request_payload)
    response_event = _build_event(
        "policy_response",
        response_payload=response_payload,
        normalized_output=normalized_output,
    )
    observation_event = _build_event("tool_observation", observation=observation)

    request_payload["input"][0]["content"] = "mutated"
    response_payload["output"][0]["summary"][0] = "mutated"
    normalized_output[0]["summary"][0] = "mutated"
    observation["output"]["nested"][0] = "mutated"

    assert _thaw(request_event.request_payload) == {
        "input": [{"content": "original"}]
    }
    assert _thaw(response_event.response_payload) == {
        "output": [{"type": "reasoning", "summary": ["original"]}]
    }
    assert _thaw(response_event.normalized_output) == [
        {"type": "reasoning", "summary": ["original"]}
    ]
    assert _thaw(observation_event.observation) == {
        "output": {"nested": ["original"]}
    }


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("reason", []),
        ("requested", 1),
        ("observed_checkpoint", []),
        ("turn", []),
        ("call_id", []),
    ],
)
def test_runner_cancellation_rejects_every_wrong_typed_member(
    field_name: str, invalid: Any
) -> None:
    values = {
        "reason": "stop",
        "requested": True,
        "observed_checkpoint": "before_policy",
        "turn": 1,
        "call_id": None,
    }
    with pytest.raises((TypeError, ValueError)):
        RunnerCancellation(**(values | {field_name: invalid}))


class RunnerCancellationSubclass(RunnerCancellation):
    pass


@pytest.mark.parametrize(
    ("already_closed", "cancellation"),
    [
        (0, None),
        (False, object()),
        (False, RunnerCancellationSubclass(reason="stop", requested=True)),
    ],
)
def test_runner_close_result_rejects_wrong_or_nonexact_members(
    already_closed: Any, cancellation: Any
) -> None:
    with pytest.raises((TypeError, ValueError)):
        RunnerCloseResult(
            already_closed=already_closed,
            cancellation=cancellation,
        )


def _valid_result(**overrides: Any) -> RunnerResult:
    digest = _digest("closed-result")
    turn = RunnerTurn(
        turn=1,
        policy_output=({"type": "message", "role": "assistant", "content": "done"},),
    )
    values: dict[str, Any] = {
        "episode_id": "episode",
        "effective_plan_digest": digest,
        "original_request": {"input": "work"},
        "response": {"output": []},
        "termination": RunnerTermination.ASSISTANT_COMPLETE,
        "turn_count": 1,
        "turns": (turn,),
        "events": (
            RunnerTerminationEvent(
                sequence=0,
                episode_id="episode",
                effective_plan_digest=digest,
                turns=1,
                reason=RunnerTermination.ASSISTANT_COMPLETE,
            ),
        ),
    }
    return RunnerResult(**(values | overrides))


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("episode_id", []),
        ("effective_plan_digest", []),
        ("original_request", []),
        ("response", []),
        ("termination", "assistant_complete"),
        ("turn_count", True),
        ("turns", (object(),)),
        ("events", (object(),)),
    ],
)
def test_runner_result_rejects_wrong_typed_or_open_variant_members(
    field_name: str, invalid: Any
) -> None:
    with pytest.raises((TypeError, ValueError, JsonSnapshotError)):
        _valid_result(**{field_name: invalid})


def test_runner_result_recursively_snapshots_request_and_response() -> None:
    original_request = {"input": [{"content": "original"}]}
    response = {"output": [{"content": "original"}]}
    result = _valid_result(original_request=original_request, response=response)

    original_request["input"][0]["content"] = "mutated"
    response["output"][0]["content"] = "mutated"

    assert _thaw(result.original_request) == {
        "input": [{"content": "original"}]
    }
    assert _thaw(result.response) == {"output": [{"content": "original"}]}


class TerminalLoopLimitsSubclass(TerminalLoopLimits):
    pass


class TerminalRunRequestSubclass(TerminalRunRequest):
    pass


def test_terminal_request_rejects_terminal_loop_limits_subclass() -> None:
    with pytest.raises(TypeError, match="limits must be TerminalLoopLimits"):
        TerminalRunRequest(
            responses_create_params={"input": "work"},
            tools=_tools(),
            limits=TerminalLoopLimitsSubclass(
                max_turns=4,
                action_timeout_seconds=9,
                max_observation_chars=20_000,
            ),
        )


async def test_terminal_adapter_rejects_request_subclass_before_effects() -> None:
    base_request = _request()
    request = TerminalRunRequestSubclass(
        responses_create_params=base_request.responses_create_params,
        tools=base_request.tools,
        limits=base_request.limits,
    )
    policy = ScriptedPolicy([])
    sink = RecordingEventSink()
    session, workspace, probe, _ = await _open(policy=policy, sink=sink)

    with pytest.raises(RunnerRequestError) as captured:
        await session.run(request)

    assert captured.value.code == "request_type_invalid"
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []
    assert [type(event) for event in sink.events] == [RunnerErrorEvent]
    assert (sink.events[0].category, sink.events[0].code) == (
        "request",
        "request_type_invalid",
    )


class EqualitySpoofRunnerToolBinding(RunnerToolBinding):
    def __eq__(self, other: object) -> bool:
        return True


async def test_workspace_rejects_binding_subclass_with_spoofed_equality_before_effects() -> None:
    expected = _plan_tool_bindings()
    workspace = RecordingWorkspace(
        tool_bindings=(
            EqualitySpoofRunnerToolBinding(
                tool_id="different_tool",
                implementation_digest=_digest("spoof"),
                capability_ids=(),
            ),
            *expected[1:],
        )
    )
    policy = ScriptedPolicy([])
    probe = ScriptedCancellationProbe()
    sink = RecordingEventSink()
    adapter = TerminalResponsesAdapter(runtime_abi=RUNTIME_ABI)

    with pytest.raises(RunnerPlanError) as captured:
        await adapter.open(
            RunnerOpenRequest(episode_id="episode", effective_plan=_effective_plan()),
            policy=policy,
            workspace=workspace,
            cancellation=probe,
            events=sink,
        )

    assert captured.value.code == "tool_grant_mismatch"
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []
    assert sink.events == []


class CrossSessionCancellationSink(RecordingEventSink):
    def __init__(self, other_session: Any) -> None:
        super().__init__()
        self.other_session = other_session
        self.cross_session_cancellation: RunnerCancellation | None = None
        self._triggered = False

    async def emit(self, event: RunnerEvent) -> None:
        self.events.append(event)
        if isinstance(event, PolicyRequestEvent) and not self._triggered:
            self._triggered = True
            self.cross_session_cancellation = await self.other_session.cancel(
                "cross-session stop"
            )


async def test_event_sink_awaiting_other_session_cancel_publishes_on_other_ledger() -> None:
    other_sink = RecordingEventSink()
    other_session, other_workspace, _, _ = await _open(
        policy=ScriptedPolicy([{"output": []}]),
        sink=other_sink,
        episode_id="other-episode",
    )
    sink = CrossSessionCancellationSink(other_session)
    session, workspace, _, _ = await _open(
        policy=ScriptedPolicy(
            [{"output": [{"type": "message", "role": "assistant", "content": "done"}]}]
        ),
        sink=sink,
        episode_id="emitting-episode",
    )

    result = await _finish_within_loop_turns(
        asyncio.create_task(session.run(_request()))
    )

    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE
    assert sink.cross_session_cancellation == RunnerCancellation(
        reason="cross-session stop", requested=True
    )
    assert [type(event) for event in sink.events] == [
        PolicyRequestEvent,
        PolicyResponseEvent,
        RunnerTerminationEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1, 2]
    assert [_public_event_record(event) for event in other_sink.events] == [
        {
            "type": "RunnerCancellationRequestedEvent",
            "sequence": 0,
            "episode_id": "other-episode",
            "effective_plan_digest": other_sink.events[0].effective_plan_digest,
            "reason": "cross-session stop",
        }
    ]
    with pytest.raises(RunnerCancelled) as captured:
        await other_session.run(_request())
    assert captured.value.cancellation == RunnerCancellation(
        reason="cross-session stop",
        requested=True,
        observed_checkpoint="before_run",
    )
    assert [type(event) for event in other_sink.events] == [
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in other_sink.events] == [0, 1]
    assert workspace.actions == []
    assert other_workspace.actions == []


async def test_large_astral_workspace_observation_truncates_to_exact_maximal_prefix() -> None:
    limit = 20_000
    value = "😀" * 6_000
    raw = json.dumps(
        {"payload": value}, sort_keys=True, separators=(",", ":")
    )
    assert len(raw) > len(value) * 11
    workspace = RecordingWorkspace()
    workspace.override["shell"] = {"payload": value}
    policy = ScriptedPolicy(
        [
            {"output": [_call("astral", "shell", '{"command":"pwd"}')]},
            {"output": [{"type": "message", "role": "assistant", "content": "done"}]},
        ]
    )
    session, _, _, sink = await _open(
        policy=policy,
        workspace=workspace,
        plan=_effective_plan(observation_bytes=limit),
    )

    result = await session.run(_request(observation_chars=limit))

    observation = next(
        event for event in sink.events if isinstance(event, ToolObservationEvent)
    )
    output = observation.observation["output"]
    decoded = json.loads(output)
    assert decoded["truncated"] is True
    assert decoded["preview"] == raw[: len(decoded["preview"])]
    assert output == json.dumps(
        {"truncated": True, "preview": decoded["preview"]},
        separators=(",", ":"),
    )
    assert len(output.encode("utf-8")) <= limit
    next_envelope = json.dumps(
        {
            "truncated": True,
            "preview": raw[: len(decoded["preview"]) + 1],
        },
        separators=(",", ":"),
    )
    assert len(next_envelope.encode("utf-8")) > limit
    assert workspace.actions == [("shell", "pwd", 9)]
    assert len(policy.requests) == 2
    assert result.termination is RunnerTermination.ASSISTANT_COMPLETE


class RaisingStripReason(str):
    def __new__(cls):
        instance = super().__new__(cls, "attacker reason")
        instance.strip_calls = 0
        return instance

    def strip(self, chars: str | None = None) -> str:
        self.strip_calls += 1
        raise HostileCarrierError(SECRET_SENTINEL)


class WrongTypeStripReason(str):
    def __new__(cls):
        instance = super().__new__(cls, "attacker reason")
        instance.strip_calls = 0
        return instance

    def strip(self, chars: str | None = None) -> Any:
        self.strip_calls += 1
        return ["not", "text"]


@pytest.mark.parametrize("reason", [RaisingStripReason(), WrongTypeStripReason()])
async def test_cancel_never_invokes_hostile_str_subclass_normalization(
    reason: Any,
) -> None:
    sink = RecordingEventSink()
    session, workspace, probe, _ = await _open(
        policy=ScriptedPolicy([{"output": []}]), sink=sink
    )

    cancellation = await session.cancel(reason)

    assert reason.strip_calls == 0
    assert cancellation == RunnerCancellation(reason="runner cancelled", requested=True)
    with pytest.raises(RunnerCancelled) as captured:
        await session.run(_request())
    assert captured.value.cancellation == RunnerCancellation(
        reason="runner cancelled",
        requested=True,
        observed_checkpoint="before_run",
    )
    assert [type(event) for event in sink.events] == [
        RunnerCancellationRequestedEvent,
        RunnerCancellationObservedEvent,
    ]
    assert [event.sequence for event in sink.events] == [0, 1]
    assert sink.events[0].reason == sink.events[1].reason == "runner cancelled"
    assert SECRET_SENTINEL not in _visible_text(captured.value)
    assert workspace.actions == []
    assert probe.checkpoints == []


class HostileEffectiveExecutionPlan(c.EffectiveExecutionPlan):
    def canonical_digest(self) -> str:
        raise HostileCarrierError(SECRET_SENTINEL)


class HostileRunnerOpenRequest(RunnerOpenRequest):
    def __getattribute__(self, name: str) -> Any:
        armed = object.__getattribute__(self, "__dict__").get("armed", False)
        if armed and name in {"episode_id", "effective_plan", "effective_plan_digest"}:
            raise HostileCarrierError(SECRET_SENTINEL)
        return super().__getattribute__(name)


def test_runner_open_request_rejects_effective_plan_subclass_before_digest_access() -> None:
    plan = _effective_plan()
    hostile_plan = HostileEffectiveExecutionPlan.model_validate(
        plan.model_dump(mode="python")
    )

    with pytest.raises(
        TypeError, match="effective_plan must be an exact EffectiveExecutionPlan"
    ) as captured:
        RunnerOpenRequest(episode_id="episode", effective_plan=hostile_plan)

    assert SECRET_SENTINEL not in str(captured.value)


async def test_terminal_adapter_rejects_open_request_subclass_before_dependency_access() -> None:
    request = HostileRunnerOpenRequest(
        episode_id="episode", effective_plan=_effective_plan()
    )
    object.__setattr__(request, "armed", True)
    policy = ScriptedPolicy([])
    workspace = RecordingWorkspace()
    probe = ScriptedCancellationProbe()
    sink = RecordingEventSink()
    adapter = TerminalResponsesAdapter(runtime_abi=RUNTIME_ABI)

    with pytest.raises(
        TypeError, match="request must be an exact RunnerOpenRequest"
    ) as captured:
        await adapter.open(
            request,
            policy=policy,
            workspace=workspace,
            cancellation=probe,
            events=sink,
        )

    assert SECRET_SENTINEL not in str(captured.value)
    assert policy.requests == []
    assert workspace.actions == []
    assert probe.checkpoints == []
    assert sink.events == []
