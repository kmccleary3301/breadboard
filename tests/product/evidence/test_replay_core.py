from __future__ import annotations

import json
import functools
import types
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from agentic_coder_prototype.logging.provider_dump import provider_dump_logger
from agentic_coder_prototype.provider.runtime import ProviderMessage, ProviderResult, ProviderRuntimeContext, ProviderRuntimeError, ProviderToolCall, normalized_provider_usage
from agentic_coder_prototype.provider.routing import ProviderDescriptor
from breadboard.product.evidence import (
    BreadBoardWorkspace,
    ReplayExecution,
    ReplayArtifactManifest,
    ReplayExecutionError,
    ReplayPlan,
    ReplayPlanError,
    ReplayRunError,
    ReplayScenario,
    build_replay_plan,
    run_replay,
)
from breadboard.product.evidence.replay_plan import HASH_BINDING_NAMES
from breadboard.product.evidence.replay_execution import _validate_usage
import breadboard.product.evidence.replay_runner as replay_runner_module
from breadboard.product.evidence.replay_runner import _callable_identity, _environment_binding, _host_containment_identity, _host_platform_binding, _provider_client_binding, _provider_route_binding, _redact, _replay_schema_registry, _runtime_type_identity
from breadboard.product.evidence.workspace import WorkspacePathError
from breadboard.product.harness.compile import compile_harness_definition
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.integrations.tool import ToolIntegrationAdapter
from breadboard.product.integrations.host import SandboxHostAdapter
from breadboard.product.integrations.provider import ProviderRuntimeAdapter

ROOT = Path(__file__).resolve().parents[3]
HASH = "sha256:" + "1" * 64
_SPECIAL_BINDINGS = {"scenario_sha256", "interaction_script_sha256", "initial_workspace_sha256", "initial_messages_sha256", "tool_schema_lock_sha256"}


class _Clock:
    def __init__(self) -> None: self.value = 0
    def now(self) -> str:
        self.value += 1
        return f"2026-07-21T10:00:{self.value:02d}Z"


class _Ids:
    def __init__(self) -> None: self.value = 0
    def new_id(self) -> str:
        self.value += 1
        return f"id-{self.value}"


class _Monotonic:
    def __init__(self) -> None: self.value = 0.0
    def __call__(self) -> float:
        self.value += 0.001
        return self.value

class _SlowMonotonic:
    def __init__(self) -> None: self.value = 0.0
    def __call__(self) -> float:
        self.value += 1
        return self.value

class _CancelAfterProvider:
    def __init__(self) -> None: self.polls = 0
    def __call__(self) -> bool:
        self.polls += 1
        return self.polls > 1
class _CancelBeforePolicy:
    def __init__(self) -> None:
        self.polls = 0
    def __call__(self) -> bool:
        self.polls += 1
        return self.polls > 2




class _AddTool:
    def __init__(self, *, fail: bool = False, invalid: bool = False, hang: bool = False) -> None: self.fail, self.invalid, self.hang = fail, invalid, hang
    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.hang: time.sleep(1)
        if self.fail: raise RuntimeError("tool failed with Authorization: Bearer UNLISTED")
        if self.invalid: return {"value": object()}
        return {"sum": arguments["a"] + arguments["b"], "token": "SECRET"}
class _RelativeWriteTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        Path("relative-output.txt").write_text("contained", encoding="utf-8")
        return {"written": True}
class _AbsoluteWriteTool:
    def __init__(self, outside: Path) -> None:
        self.outside = outside
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.outside.write_text("escaped", encoding="utf-8")
        return {"escaped": True}
class _OutsideReadTool:
    def __init__(self, outside: Path) -> None:
        self.outside = outside
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"content": self.outside.read_text(encoding="utf-8")}
class _InheritedFdReadTool:
    def __init__(self, outside: Path) -> None:
        self.descriptor = __import__("os").open(outside, __import__("os").O_RDONLY)
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"content": __import__("os").read(self.descriptor, 4096).decode("utf-8")}
class _InheritedSocketReadTool:
    def __init__(self) -> None:
        import socket
        self.reader, self.writer = socket.socketpair()
        self.writer.sendall(b"INHERITED_SOCKET_CONTENT")
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"content": self.reader.recv(4096).decode("utf-8")}
    def close(self) -> None:
        self.reader.close()
        self.writer.close()


class _EnvironmentReadTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"value": __import__("os").environ.get("BB_REPLAY_ENV_SECRET")}


class _SecretSymlinkTool:
    def __init__(self, secret: str) -> None:
        self.secret = secret
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        Path(self.secret).symlink_to("input.txt")
        return {"created": True}






class _StageFifoTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        __import__("os").mkfifo(Path.cwd().parent / "untracked.fifo")
        return {"created": True}



class _NetworkTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        import socket
        connection = socket.socket()
        try:
            connection.connect(("127.0.0.1", 9))
        finally:
            connection.close()
        return {"connected": True}


class _PrintingTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        print("Authorization: Bearer SECRET", flush=True)
        return {"printed": True}


class _WorkspaceFifoTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        __import__("os").mkfifo(Path.cwd() / "untracked.fifo")
        return {"created": True}



class _ChangingCwdTool:
    def __init__(self, outside: Path) -> None:
        self.outside = outside
        self.calls = 0
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            __import__("os").chdir(self.outside)
        else:
            Path("after-cwd-change.txt").write_text("contained", encoding="utf-8")
        return {"call": self.calls}






class _FrameInspectionTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        import inspect
        exposed = set()
        frame = inspect.currentframe()
        while frame is not None:
            exposed.update(name for name in ("host", "authorize", "secret_bindings", "provider_client") if frame.f_locals.get(name) is not None)
            frame = frame.f_back
        return {"exposed": sorted(exposed)}


class _Host:
    def __init__(self, *, fail: bool = False, replace: bool = False) -> None: self.fail, self.replace = fail, replace
    def get_workspace(self) -> str: return "sandbox:fixture"
    def replay_process_containment(self) -> dict[str, str]: return {"mechanism": "fixture-supervisor", "detached_descendants": "contained"}
    def execute(self, command: str, **kwargs: Any) -> dict[str, Any]:
        if self.fail: raise RuntimeError("host failed with Authorization: Bearer UNLISTED")
        cwd = Path(kwargs["cwd"])
        if self.replace:
            cwd.rename(cwd.parent / "workspace-original")
            cwd.mkdir()
        (cwd / "host.txt").write_text(command, encoding="utf-8")
        return {"exit_code": 0, "cwd": kwargs["cwd"], "token": "SECRET"}


class _SubprocessHost(_Host):
    def execute(self, command: str, **kwargs: Any) -> dict[str, Any]:
        import subprocess
        completed = subprocess.run(["/usr/bin/touch", "host-subprocess.txt"], cwd=kwargs["cwd"], check=False, capture_output=True)
        return {"exit_code": completed.returncode, "error": completed.stderr.decode("utf-8", "replace"), "command": command}






def _allow(_name: str, _arguments: Mapping[str, Any]) -> bool:
    return True
def _deny(_name: str, _arguments: Mapping[str, Any]) -> bool:
    return False
def _hang_policy(_name: str, _arguments: Mapping[str, Any]) -> bool:
    time.sleep(1)
    return True
def _add_callable(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"sum": arguments["a"] + arguments["b"], "token": "SECRET"}


def _prefixed_policy(expected: str, name: str, _arguments: Mapping[str, Any]) -> bool:
    return expected == name


class _PolicyOwner:
    def allow(self, _name: str, _arguments: Mapping[str, Any]) -> bool:
        return True




class _ForkTool:
    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        __import__("os").fork()
        return {"escaped": True}


class _BadIds:
    def new_id(self) -> str:
        return "../../escaped"




class _Provider:
    provider_id = "fixture"
    runtime_id = "fixture_runtime"
    descriptor = types.SimpleNamespace(provider_id="fixture", runtime_id="fixture_runtime", default_api_variant="fixture")
    def __init__(self, sequence: tuple[str, ...]) -> None: self.sequence, self.calls, self.tools_seen, self.messages_seen = sequence, 0, None, None
    def replay_client_identity(self, client: Any, secret_bindings: Mapping[str, str]) -> dict[str, Any]:
        return {"verified": True, "client_type": f"{type(client).__module__}.{type(client).__qualname__}", "secret_references": sorted(secret_bindings)}
    def invoke(self, **kwargs: Any) -> ProviderResult:
        self.tools_seen = kwargs.get("tools")
        self.messages_seen = [dict(row) for row in kwargs.get("messages", [])]
        action = self.sequence[self.calls]; self.calls += 1
        if action == "provider_error": raise RuntimeError("provider failed with SECRET")
        if action == "provider_secret_error": raise RuntimeError("Authorization: Bearer UNLISTED")
        if action == "hang": time.sleep(1)
        if action == "add": message = ProviderMessage("assistant", None, [ProviderToolCall("call-add", "add", json.dumps({"a": 2, "b": 3}))], "tool_calls")
        elif action == "add_without_id": message = ProviderMessage("assistant", None, [ProviderToolCall(None, "add", json.dumps({"a": 2, "b": 3}))], "tool_calls")
        elif action == "two_adds": message = ProviderMessage("assistant", None, [ProviderToolCall("call-add-1", "add", '{"a":1,"b":2}'), ProviderToolCall("call-add-2", "add", '{"a":3,"b":4}')], "tool_calls")
        elif action == "nan_args": message = ProviderMessage("assistant", None, [ProviderToolCall("call-nan", "add", '{"a":NaN,"b":2}')], "tool_calls")
        elif action == "host":
            host_names = [tool["function"]["name"] for tool in kwargs["tools"] if tool["function"]["name"].startswith("host_execute")]
            if len(host_names) != 1 or "." in host_names[0]:
                raise RuntimeError("provider did not receive the canonical host tool through a valid wire alias")
            message = ProviderMessage("assistant", None, [ProviderToolCall("call-host", host_names[0], json.dumps({"command": "fixture"}))], "tool_calls")
        elif action == "unknown": message = ProviderMessage("assistant", None, [ProviderToolCall("call-danger", "danger", "{}")], "tool_calls")
        elif action == "invalid_message": message = ProviderMessage("assistant", "done", [], 7)  # type: ignore[arg-type]
        elif action == "environment_value": message = ProviderMessage("assistant", __import__("os").environ["BB_REPLAY_ENV_SECRET"], [], "stop")
        elif action == "overlapping_environment_value": message = ProviderMessage("assistant", __import__("os").environ["BB_REPLAY_LONG_SECRET"], [], "stop")
        elif action == "resolve_localhost":
            __import__("socket").getaddrinfo("localhost", 443)
            message = ProviderMessage("assistant", "resolved", [], "stop")
        else: message = ProviderMessage("assistant", "done", [], "stop", annotations={"opaque": object()} if action == "opaque" else {})
        usage = {"input_tokens": 100, "output_tokens": 100, "total_tokens": 1} if action == "malformed_usage" else {"input_tokens": 1, "output_tokens": 1}
        metadata = {"cost_currency": "EUR"} if action == "eur" else ({"Authorization": "Bearer UNLISTED", "Cookie": "sid=UNLISTED", "apiKey": "UNLISTED", "accessToken": "UNLISTED", "privateKey": "UNLISTED", "access_key": "UNLISTED", "session_id": "UNLISTED", "bearer": "UNLISTED"} if action == "credential_fields" else {"api_key": "SECRET"})
        return ProviderResult([message], {"raw": "must not be persisted"}, usage, model="fixture-model", metadata=metadata)


class _LiveToolResultProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        if self.calls == 1:
            live_result = json.loads(kwargs["messages"][-1]["content"])
            if live_result["token"] != "SECRET":
                raise RuntimeError("live tool result was redacted")
        return super().invoke(**kwargs)
class _WritingProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        Path("provider-bypass.txt").write_text("bypass", encoding="utf-8")
        return super().invoke(**kwargs)


class _DumpingProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        if provider_dump_logger.enabled or provider_dump_logger.log_dir is not None:
            raise RuntimeError("provider dump logger remained enabled")
        return super().invoke(**kwargs)

class _StatefulProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        continuation = kwargs["context"].session_state.get_provider_metadata("custom_continuation")
        if continuation is None:
            message = ProviderMessage("assistant", None, [ProviderToolCall("call-add", "add", '{"a":2,"b":3}')], "tool_calls")
            return ProviderResult([message], None, {"input_tokens": 1, "output_tokens": 1}, model="fixture-model", metadata={"custom_continuation": "response-1"})
        if continuation != "response-1":
            raise RuntimeError("stale provider response state")
        return ProviderResult([ProviderMessage("assistant", "done", [], "stop")], None, {"input_tokens": 1, "output_tokens": 1}, model="fixture-model", metadata={"custom_continuation": "response-2"})
class _EnvironmentFactoryProvider(_Provider):
    def replay_worker_client_spec(self, _client: Any, _secret_bindings: Mapping[str, str]) -> dict[str, Any]:
        return {}

    def replay_worker_client(self, _spec: Mapping[str, Any]) -> Any:
        if __import__("os").environ.get("BB_REPLAY_ENV_SECRET") != "ENVIRONMENT_ONLY_CREDENTIAL":
            raise RuntimeError("provider client factory did not receive its allowlisted environment")
        return object()
class _CollisionAliasProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        wire_names = [tool["function"]["name"] for tool in kwargs["tools"]]
        if self.calls == 0:
            if wire_names[0] != "a_b" or wire_names[1] == "a_b":
                raise RuntimeError("collision aliases are not stable")
            self.calls += 1
            return ProviderResult([ProviderMessage("assistant", None, [ProviderToolCall("collision-call", wire_names[0], '{"a":2,"b":3}')], "tool_calls")], None, {"input_tokens": 1, "output_tokens": 1}, model="fixture-model", metadata={})
        prior_name = kwargs["messages"][-2]["tool_calls"][0]["function"]["name"]
        if prior_name != wire_names[0]:
            raise RuntimeError("prior wire alias drifted across turns")
        self.calls += 1
        return ProviderResult([ProviderMessage("assistant", "done", [], "stop")], None, {"input_tokens": 1, "output_tokens": 1}, model="fixture-model", metadata={})


class _ForcedToolChoiceProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        tool_choice = kwargs["context"].agent_config["provider_tools"]["anthropic"]["tool_choice"]
        if tool_choice != {"type": "tool", "name": "host_execute"}:
            raise RuntimeError("forced tool choice did not follow the provider wire alias")
        return super().invoke(**kwargs)




class _SpecOnlyProvider(_Provider):
    def replay_worker_client_spec(self, _client: Any, _secret_bindings: Mapping[str, str]) -> dict[str, Any]:
        return {}


class _ToolWithProviderCapability:
    def __init__(self) -> None:
        self.provider = _Provider(("done",))

    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"should_not_run": True}
class _ToolWithPolicyCapability:
    def __init__(self) -> None:
        self.authorize = _allow

    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"should_not_run": True}









class _ExplosiveRaw:
    def __init__(self, marker: Path) -> None:
        self.marker = marker
    def __reduce__(self):
        return eval, (f"__import__('pathlib').Path({str(self.marker)!r}).write_text('owned')",)


class _RawProvider(_Provider):
    def __init__(self, marker: Path) -> None:
        super().__init__(("done",))
        self.marker = marker
    def invoke(self, **kwargs: Any) -> ProviderResult:
        result = super().invoke(**kwargs)
        result.raw_response = _ExplosiveRaw(self.marker)
        return result


class _ContextCapability:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def execute(self) -> None:
        self.marker.write_text("called", encoding="utf-8")


class _ContextInspectingProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        capability = kwargs["context"].extra.get("host")
        if capability is not None:
            capability.execute()
        return super().invoke(**kwargs)


class _ProviderMetadataInspectingProvider(_Provider):
    def invoke(self, **kwargs: Any) -> ProviderResult:
        session_state = kwargs["context"].session_state
        expected = {
            "anthropic_rate_limits": {"tokens_remaining": 17},
            "conversation_id": "conversation-fixture",
            "current_turn_index": 3,
            "previous_response_id": "response-fixture",
        }
        if {name: session_state.get_provider_metadata(name) for name in expected} != expected:
            raise RuntimeError("provider replay metadata was not preserved")
        if session_state.get_provider_metadata("control_queue") is not None:
            raise RuntimeError("runtime-only provider metadata escaped into replay")
        return super().invoke(**kwargs)


class _CallableConfiguredTool:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def execute(self, _arguments: Mapping[str, Any]) -> dict[str, int]:
        return {"value": self.value}


class _MetadataSink:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def set_provider_metadata(self, name: str, value: Any) -> None:
        self.marker.write_text(json.dumps({name: value}, sort_keys=True), encoding="utf-8")


def _harness_lock():
    document = yaml.safe_load((ROOT / "agent_configs/templates/minimal_harness.v3.yaml").read_text(encoding="utf-8"))
    return compile_harness_definition(document, source_ref="agent_configs/templates/minimal_harness.v3.yaml").lock


def _scenario(interaction_script: tuple[Mapping[str, Any], ...] = ()) -> ReplayScenario:
    return ReplayScenario(
        "fixture task",
        ({"role": "user", "content": "go SECRET"},),
        {"input.txt": "hello"},
        (
            {"type": "function", "function": {"name": "add", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "host.execute", "parameters": {"type": "object"}}},
        ),
        interaction_script,
    )


def _plan(scenario: ReplayScenario, *, deadlines: dict[str, int] | None = None, provider_route: dict[str, Any] | None = None, budgets: dict[str, int | float] | None = None, authorize=_allow, provider_instance: _Provider | None = None, provider_client: Any = None, tool_instances: Mapping[str, Any] | None = None, host_instance: Any = None, environment_allowlist: tuple[str, ...] = ()):
    lock = _harness_lock()
    frozen = {name: {"binding": name} for name in HASH_BINDING_NAMES if name not in _SPECIAL_BINDINGS}
    frozen["operation_policy_sha256"] = {"callable": _callable_identity(authorize)}
    frozen["secret_references_sha256"] = {"references": ["fixture-secret"]}
    frozen["environment_allowlist_sha256"] = _environment_binding(environment_allowlist, {"fixture-secret": "SECRET"})[0]
    frozen["schema_registry_sha256"] = _replay_schema_registry()[0]
    frozen["host_platform_sha256"] = _host_platform_binding()
    active_provider = provider_instance or _Provider(("done",))
    active_client = object() if provider_client is None else provider_client
    frozen["provider_route_lock_sha256"] = provider_route or _provider_route_binding("fixture", "fixture_runtime", "fixture", "fixture-model", None, "1.0", active_provider, _provider_client_binding(active_provider, active_client, {"fixture-secret": "SECRET"}))
    declared_names = {row["function"]["name"] for row in scenario.tool_schemas}
    active_tools = dict(tool_instances) if tool_instances is not None else ({"add": _AddTool()} if "add" in declared_names else {})
    active_host = host_instance or _Host()
    frozen["tool_executor_identity_sha256"] = {"executors": {name: _runtime_type_identity(active_tools[name]) for name in sorted(active_tools)}}
    frozen["host_driver_identity_sha256"] = {"driver_type": _runtime_type_identity(active_host), "workspace_identity": "sandbox:fixture", "process_containment": _host_containment_identity(active_host)}
    bindings = scenario.binding_inputs(frozen)
    plan = build_replay_plan(
        lane_lock_sha256=HASH,
        harness_lock_sha256=lock.as_dict()["graph_hash"],
        binding_inputs=bindings,
        deadlines_ms=deadlines or {"total": 10_000, "idle": 1_000, "provider_call": 1_000, "tool_call": 1_000},
        budgets=budgets or {"turns": 5, "provider_calls": 5, "tool_calls": 5, "tokens": 100, "cost": 1},
        cancellation_grace_ms=100,
        scenario_ref="scenario:fixture",
        provider_route_ref="route:fixture",
        operation_policy_ref="policy:fixture",
        host_ref="host:fixture",
        toolset_lock_ref="tools:fixture",
    )
    return lock, bindings, plan

def _run(tmp_path: Path, *, sequence: tuple[str, ...] = ("add", "host", "done"), tool_fail: bool = False, tool_invalid: bool = False, tool_hang: bool = False, host_fail: bool = False, host_replace: bool = False, host_instance: Any = None, authorize=None, deadlines: dict[str, int] | None = None, budgets: dict[str, int | float] | None = None, monotonic=None, cancelled=None, provider_route: dict[str, Any] | None = None, tool_schemas: tuple[dict[str, Any], ...] | None = None, interaction_script: tuple[Mapping[str, Any], ...] = (), provider_instance: _Provider | None = None, provider_context: Any = None, tool_instances: Mapping[str, Any] | None = None, ids: Any = None, runtime_bindings: Mapping[str, Any] | None = None, environment_allowlist: tuple[str, ...] = ()):
    workspace = BreadBoardWorkspace(tmp_path)
    scenario = _scenario(interaction_script) if tool_schemas is None else ReplayScenario("fixture task", ({"role": "user", "content": "go SECRET"},), {"input.txt": "hello"}, tool_schemas, interaction_script)
    active_authorize = authorize or _allow
    active_provider = provider_instance or _Provider(sequence)
    active_client = object()
    declared_names = {row["function"]["name"] for row in scenario.tool_schemas}
    active_tools = dict(tool_instances) if tool_instances is not None else ({"add": _AddTool(fail=tool_fail, invalid=tool_invalid, hang=tool_hang)} if "add" in declared_names else {})
    active_host = host_instance or _Host(fail=host_fail, replace=host_replace)
    lock, bindings, plan = _plan(scenario, deadlines=deadlines, provider_route=provider_route, budgets=budgets, authorize=active_authorize, provider_instance=active_provider, provider_client=active_client, tool_instances=active_tools, host_instance=active_host, environment_allowlist=environment_allowlist)
    result = run_replay(
        plan,
        binding_inputs=bindings,
        runtime_bindings=runtime_bindings or {name: {"binding": name} for name in ("capability_probe_sha256", "model_policy_sha256", "normalizer_config_sha256", "comparator_config_sha256")},
        lane_lock={"lock_sha256": HASH},
        harness_lock=lock,
        workspace=workspace,
        scenario=scenario,
        provider_client=active_client,
        provider=active_provider,
        provider_model="fixture-model",
        provider_context=provider_context if provider_context is not None else object(),
        tools=active_tools,
        host=active_host,
        authorize=active_authorize,
        secret_bindings={"fixture-secret": "SECRET"},
        environment_allowlist=environment_allowlist,
        runtime_version="1.0",
        ids=ids or _Ids(),
        monotonic=monotonic or _Monotonic(),
        cancelled=cancelled,
    )
    return workspace, plan, result


def _validator(name: str) -> Draft202012Validator:
    root = ROOT / "contracts/public/schemas"; schema = json.loads((root / name).read_text(encoding="utf-8")); problem = json.loads((root / "bb.problem.v1.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resource(problem["$id"], Resource.from_contents(problem))
    return Draft202012Validator(schema, registry=registry)


def test_deterministic_multiturn_replay_completes_with_full_redacted_artifact_graph(tmp_path: Path) -> None:
    workspace, plan, result = _run(tmp_path)
    execution = result.execution.as_dict(); manifest = result.manifest.as_dict()
    assert execution["terminal_status"] == "completed"
    assert execution["claimable"] is False
    assert execution["comparison_report_id"] is None
    assert execution["normalization_evidence_ids"] == []
    assert len(execution["provider_exchanges"]) == 3
    assert len(execution["tool_outcomes"]) == 2
    assert result.execution_path.is_file()
    assert not (result.execution_path.parent / "workspace").exists()
    _validator("bb.replay_plan.v1.schema.json").validate(plan.as_dict())
    _validator("bb.replay_execution.v1.schema.json").validate(execution)
    _validator("bb.replay_artifact_manifest.v1.schema.json").validate(manifest)
    store = ArtifactStore(workspace.path(".breadboard/artifacts"))
    entries = {entry["artifact_id"]: entry for entry in manifest["entries"]}
    stored_digests = {path.name for path in workspace.path(".breadboard/artifacts/sha256").glob("*/*") if path.is_file()}
    assert stored_digests == {entry["sha256"].removeprefix("sha256:") for entry in entries.values()}
    assert {entry["role"] for entry in entries.values()} >= {"kernel_event_stream", "provider_exchange", "tool_outcome", "workspace_before", "workspace_after", "workspace_diff", "redaction_report", "policy_decision"}
    for entry in entries.values():
        payload = store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))
        assert b"SECRET" not in payload
        assert str(tmp_path).encode() not in payload
    policy_entry = next(entry for entry in entries.values() if entry["role"] == "policy_decision")
    policy = json.loads(store.read(ArtifactRef(policy_entry["sha256"], policy_entry["size_bytes"], policy_entry["media_type"])))
    assert "host_identity_sha256" in policy and "host_identity" not in policy
    exchange_entry = next(entry for entry in entries.values() if entry["role"] == "provider_exchange")
    exchange = json.loads(store.read(ArtifactRef(exchange_entry["sha256"], exchange_entry["size_bytes"], exchange_entry["media_type"])))
    _validator("bb.provider_exchange.v2.schema.json").validate(exchange)


def test_replay_executes_tool_integration_adapter_in_its_capability_worker(tmp_path: Path) -> None:
    adapter = ToolIntegrationAdapter("add", _AddTool())
    workspace, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": adapter})
    assert result.execution.as_dict()["terminal_status"] == "completed"
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "tool_outcome")
    outcome = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert outcome["result"]["sum"] == 5
    assert outcome["result"]["token"] == "<redacted>"


def test_live_provider_receives_unredacted_tool_result_while_artifact_is_redacted(tmp_path: Path) -> None:
    provider = _LiveToolResultProvider(("add", "done"))
    workspace, _, result = _run(tmp_path, provider_instance=provider)
    assert result.execution.as_dict()["terminal_status"] == "completed"
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "tool_outcome")
    outcome = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert outcome["result"]["token"] == "<redacted>"


def test_replay_executes_callable_tool_integration_adapter_in_its_capability_worker(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": ToolIntegrationAdapter("add", _add_callable)})
    assert result.execution.as_dict()["terminal_status"] == "completed"


@pytest.mark.parametrize("authorize", [_PolicyOwner().allow, functools.partial(_prefixed_policy, "add")])
def test_replay_executes_bound_and_partial_policy_capabilities(tmp_path: Path, authorize: Any) -> None:
    _, _, result = _run(tmp_path, sequence=("add", "done"), authorize=authorize)
    assert result.execution.as_dict()["terminal_status"] == "completed"


@pytest.mark.parametrize("executor", [functools.partial(_add_callable), len])
def test_replay_executes_partial_and_builtin_tool_adapter_callables(tmp_path: Path, executor: Any) -> None:
    _, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": ToolIntegrationAdapter("add", executor)})
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_replay_executes_sandbox_host_adapter_in_its_capability_worker(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("host", "done"), host_instance=SandboxHostAdapter("fixture", _Host()))
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_tool_capability_rejects_reachable_provider_capability(tmp_path: Path) -> None:
    with pytest.raises(ReplayRunError, match="tool capability state contains a forbidden provider capability"):
        _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _ToolWithProviderCapability()})


def test_tool_capability_rejects_reachable_policy_callable(tmp_path: Path) -> None:
    with pytest.raises(ReplayRunError, match="tool capability state contains a forbidden policy callable"):
        _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _ToolWithPolicyCapability()})


def test_provider_wire_messages_alias_canonical_tool_calls_without_mutating_transcript() -> None:
    schemas, reverse = replay_runner_module._provider_tool_wire_schemas(({"type": "function", "function": {"name": "host.execute", "parameters": {"type": "object"}}},))
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "host.execute", "arguments": "{}"}}]},
        {"role": "tool", "name": "host.execute", "tool_call_id": "call-1", "content": "{}"},
        {"type": "function_call", "name": "host.execute", "arguments": "{}"},
        {"role": "assistant", "content": [{"type": "tool_use", "name": "host.execute", "input": {}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-2", "type": "function", "name": "host.execute", "arguments": "{}"}]},
    ]
    wire_messages = replay_runner_module._provider_wire_messages(messages, reverse)
    assert schemas[0]["function"]["name"] == "host_execute"
    assert wire_messages[0]["tool_calls"][0]["function"]["name"] == "host_execute"
    assert wire_messages[1]["name"] == "host_execute"
    assert wire_messages[2]["name"] == "host_execute"
    assert wire_messages[3]["content"][0]["name"] == "host_execute"
    assert wire_messages[4]["tool_calls"][0]["name"] == "host_execute"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "host.execute"


def test_colliding_provider_aliases_remain_stable_across_turns(tmp_path: Path) -> None:
    schemas = (
        {"type": "function", "function": {"name": "a.b", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "a_b", "parameters": {"type": "object"}}},
    )
    _, _, result = _run(
        tmp_path,
        sequence=("unused",),
        provider_instance=_CollisionAliasProvider(("unused",)),
        tool_schemas=schemas,
        tool_instances={"a.b": _AddTool(), "a_b": _AddTool()},
    )
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_forced_tool_choice_follows_provider_wire_alias(tmp_path: Path) -> None:
    provider_context = types.SimpleNamespace(
        agent_config={"provider_tools": {"anthropic": {"tool_choice": {"type": "tool", "name": "host.execute"}}}},
        extra={},
    )
    _, _, result = _run(
        tmp_path,
        sequence=("host", "done"),
        provider_instance=_ForcedToolChoiceProvider(("host", "done")),
        provider_context=provider_context,
    )
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_isolated_provider_context_advances_state_between_turns(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("unused",), provider_instance=_StatefulProvider(("unused",)))
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_provider_client_factory_receives_allowlisted_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BB_REPLAY_ENV_SECRET", "ENVIRONMENT_ONLY_CREDENTIAL")
    _, _, result = _run(tmp_path, sequence=("done",), provider_instance=_EnvironmentFactoryProvider(("done",)), environment_allowlist=("BB_REPLAY_ENV_SECRET",))
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_provider_client_spec_requires_reconstruction_factory(tmp_path: Path) -> None:
    with pytest.raises(ReplayRunError, match="requires replay_worker_client"):
        _run(tmp_path, sequence=("done",), provider_instance=_SpecOnlyProvider(("done",)))


def test_interaction_script_is_bound_into_provider_messages(tmp_path: Path) -> None:
    script = ({"role": "system", "content": "frozen replay step"},)
    workspace, _, result = _run(tmp_path, sequence=("done",), interaction_script=script)
    request_entry = next(entry for entry in result.manifest.as_dict()["entries"] if entry["role"] == "provider_request")
    request = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(request_entry["sha256"], request_entry["size_bytes"], request_entry["media_type"])))
    assert [row["role"] for row in request["messages"]] == ["user", "system"]
    assert request["messages"][1]["content"] == "frozen replay step"


@pytest.mark.parametrize(
    ("sequence", "tool_fail", "host_fail", "authorize", "status"),
    [
        (("provider_error",), False, False, None, "provider_failed"),
        (("add",), True, False, None, "tool_failed"),
        (("host",), False, True, None, "host_failed"),
        (("add",), False, False, _deny, "policy_denied"),
    ],
)
def test_runtime_failures_are_persisted_but_never_claimable(tmp_path: Path, sequence, tool_fail, host_fail, authorize, status) -> None:
    _, _, result = _run(tmp_path, sequence=sequence, tool_fail=tool_fail, host_fail=host_fail, authorize=authorize)
    record = result.execution.as_dict()
    assert record["terminal_status"] == status
    assert record["claimable"] is False
    assert record["comparison_report_id"] is None
    assert record["normalization_evidence_ids"] == []
    assert record["problem"]["failed_stage"] == "replay"
    _validator("bb.replay_execution.v1.schema.json").validate(record)
    with pytest.raises(ReplayExecutionError, match="only completed"):
        result.execution.require_comparable()

def test_reuse_execution_is_never_comparable(tmp_path: Path) -> None:
    _, plan, result = _run(tmp_path, sequence=("done",))
    record = result.execution.as_dict()
    record.update(
        mode="reuse",
        fresh_nonce=None,
        reuse_attestation_id="reuse-attestation.fixture",
        provider_exchanges=[],
    )
    execution = ReplayExecution.from_dict(record)
    with pytest.raises(ReplayExecutionError, match="execute-mode"):
        execution.require_comparable()
    with pytest.raises(ReplayExecutionError, match="mode does not match"):
        execution.verify_plan(plan)



@pytest.mark.parametrize(
    ("sequence", "deadlines", "provider_status", "tool_count"),
    [
        (("done",), {"total": 10_000, "idle": 1_000, "provider_call": 1, "tool_call": 2_000}, "timed_out", 0),
        (("add",), {"total": 10_000, "idle": 1_000, "provider_call": 2_000, "tool_call": 1}, "completed", 1),
    ],
)
def test_provider_and_tool_deadlines_persist_timeout_evidence(tmp_path: Path, sequence, deadlines, provider_status, tool_count) -> None:
    _, _, result = _run(tmp_path, sequence=sequence, deadlines=deadlines, monotonic=_SlowMonotonic())
    record = result.execution.as_dict()
    assert record["terminal_status"] == "timed_out"
    assert record["provider_exchanges"][0]["status"] == provider_status
    assert len(record["tool_outcomes"]) == tool_count
    assert record["claimable"] is False
    _validator("bb.replay_execution.v1.schema.json").validate(record)


@pytest.mark.parametrize(
    ("sequence", "tool_hang", "error_code"),
    [
        (("hang",), False, "replay.provider_timeout"),
        (("add",), True, "replay.tool_timeout"),
    ],
)
def test_hung_external_calls_are_terminated_at_deadline(tmp_path: Path, sequence, tool_hang, error_code) -> None:
    deadlines = {"total": 2_000, "idle": 1_000, "provider_call": 20, "tool_call": 20}
    _, _, result = _run(tmp_path, sequence=sequence, tool_hang=tool_hang, deadlines=deadlines)
    record = result.execution.as_dict()
    assert record["terminal_status"] == "timed_out"
    assert record["problem"]["error_code"] == error_code


def test_tool_budget_counts_each_dispatch(tmp_path: Path) -> None:
    budgets = {"turns": 5, "provider_calls": 5, "tool_calls": 1, "tokens": 100, "cost": 1}
    _, _, result = _run(tmp_path, sequence=("two_adds",), budgets=budgets)
    record = result.execution.as_dict()
    assert record["terminal_status"] == "budget_exhausted"
    assert record["problem"]["error_code"] == "replay.tool_budget"
    assert len(record["tool_outcomes"]) == 1


def test_total_and_idle_deadlines_are_enforced_independently(tmp_path: Path) -> None:
    _, _, total_result = _run(
        tmp_path / "total",
        sequence=("done",),
        deadlines={"total": 2_500, "idle": 1_000, "provider_call": 2_000, "tool_call": 2_000},
        monotonic=_SlowMonotonic(),
    )
    total = total_result.execution.as_dict()
    assert total["terminal_status"] == "timed_out"
    assert total["provider_exchanges"][0]["status"] == "timed_out"
    assert total["problem"]["error_code"] == "replay.total_timeout"
    _, _, idle_result = _run(
        tmp_path / "idle",
        sequence=("done",),
        deadlines={"total": 10_000, "idle": 1, "provider_call": 2_000, "tool_call": 2_000},
        monotonic=_SlowMonotonic(),
    )
    idle = idle_result.execution.as_dict()
    assert idle["terminal_status"] == "timed_out"
    assert idle["provider_exchanges"] == []
    assert idle["problem"]["error_code"] == "replay.idle_timeout"


def test_cancellation_after_provider_call_is_not_misclassified_by_elapsed_work(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("done",), cancelled=_CancelAfterProvider())
    record = result.execution.as_dict()
    assert record["terminal_status"] == "cancelled"
    assert record["provider_exchanges"][0]["status"] == "cancelled"
    assert record["problem"]["error_code"] == "replay.cancelled"
    _, _, grace_result = _run(
        tmp_path / "grace",
        sequence=("done",),
        deadlines={"total": 10_000, "idle": 1_000, "provider_call": 2_000, "tool_call": 2_000},
        monotonic=_SlowMonotonic(),
        cancelled=_CancelAfterProvider(),
    )
    assert grace_result.execution.as_dict()["problem"]["error_code"] == "replay.cancelled"


def test_cancellation_wins_when_provider_failure_arrives_concurrently(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("provider_error",), cancelled=_CancelAfterProvider())
    record = result.execution.as_dict()
    assert record["terminal_status"] == "cancelled"
    assert record["provider_exchanges"][0]["status"] == "cancelled"
    assert record["problem"]["error_code"] == "replay.cancelled"


def test_runtime_rejects_route_and_toolset_drift(tmp_path: Path) -> None:
    wrong_route = {"provider_id": "other", "runtime_id": "fixture_runtime", "endpoint_class": "fixture", "model_id": "fixture-model", "model_revision": None}
    with pytest.raises(ReplayPlanError, match="provider runtime"):
        _run(tmp_path / "route", sequence=("done",), provider_route=wrong_route)
    _, _, result = _run(tmp_path / "tool", sequence=("unknown",))
    record = result.execution.as_dict()
    assert record["terminal_status"] == "policy_denied"
    assert record["problem"]["error_code"] == "replay.undeclared_tool"
    assert {row["kind"]: row["decision"] for row in record["policy_decisions"]}["capability"] == "denied"


def test_provider_route_identity_excludes_transient_replay_client_specs() -> None:
    descriptor = ProviderDescriptor("fixture", "fixture_runtime", "chat", True, False, False, False, "openai", None, "FIXTURE_KEY", {})

    class Runtime:
        def create_client(self, api_key: str, *, base_url: str | None = None, default_headers: dict[str, str] | None = None) -> Any:
            return types.SimpleNamespace(api_key=api_key, base_url=base_url, default_headers=default_headers or {})

        def invoke(self, **kwargs: Any) -> ProviderResult:
            raise AssertionError("not used")

    adapter = ProviderRuntimeAdapter(Runtime(), descriptor)
    active_client = adapter.create_client("ACTIVE_SECRET", base_url="https://active.invalid")
    active_identity = _provider_client_binding(adapter, active_client, {"FIXTURE_KEY": "ACTIVE_SECRET"})
    before = _provider_route_binding("fixture", "fixture_runtime", "chat", "fixture-model", None, "1.0", adapter, active_identity)
    adapter.create_client("UNRELATED_SECRET", base_url="https://unrelated.invalid")
    after = _provider_route_binding("fixture", "fixture_runtime", "chat", "fixture-model", None, "1.0", adapter, active_identity)
    assert after == before
    encoded = json.dumps(after, sort_keys=True)
    assert "ACTIVE_SECRET" not in encoded
    assert "UNRELATED_SECRET" not in encoded


def test_provider_cost_currency_cannot_change_mid_replay(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add", "eur"))
    record = result.execution.as_dict()
    assert record["terminal_status"] == "provider_failed"
    assert record["problem"]["error_code"] == "replay.cost_currency_mismatch"


def test_usage_normalization_is_conservative_and_rejects_invalid_counts() -> None:
    understated = ProviderResult([], None, {"input_tokens": 100, "output_tokens": 100, "total_tokens": 1})
    assert normalized_provider_usage(understated)["total_tokens"] == 200
    with pytest.raises(ProviderRuntimeError, match="nonnegative integer"):
        normalized_provider_usage(ProviderResult([], None, {"input_tokens": -1}))
    charged = normalized_provider_usage(ProviderResult([], None, {"cost_amount": 0.75}, metadata={}))
    assert charged["cost_amount"] == 0.75


def test_opaque_provider_values_fail_without_nondeterministic_evidence(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("opaque",))
    record = result.execution.as_dict()
    assert record["terminal_status"] == "provider_failed"
    assert record["provider_exchanges"][0]["status"] == "provider_error"
    _, _, invalid_result = _run(tmp_path / "invalid", sequence=("invalid_message",))
    assert invalid_result.execution.as_dict()["terminal_status"] == "provider_failed"

def test_noncanonical_tool_arguments_are_invalid_provider_responses(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("nan_args",))
    record = result.execution.as_dict()
    assert record["terminal_status"] == "provider_failed"
    assert record["provider_exchanges"][0]["status"] == "invalid_response"
    assert record["problem"]["error_code"] == "replay.invalid_provider_response"
    assert record["tool_outcomes"] == []


def test_provider_request_hash_matches_empty_tool_payload(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("done",), tool_schemas=())
    request_entry = next(entry for entry in result.manifest.as_dict()["entries"] if entry["role"] == "provider_request")
    request = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(request_entry["sha256"], request_entry["size_bytes"], request_entry["media_type"])))
    assert request["tools"] == []




def test_tool_arguments_must_match_frozen_parameter_schema(tmp_path: Path) -> None:
    strict_schema = (
        {
            "type": "function",
            "function": {
                "name": "add",
                "parameters": {
                    "type": "object",
                    "properties": {"required_value": {"type": "integer"}},
                    "required": ["required_value"],
                    "additionalProperties": False,
                },
            },
        },
    )
    _, _, result = _run(tmp_path, sequence=("add",), tool_schemas=strict_schema)
    record = result.execution.as_dict()
    assert record["terminal_status"] == "provider_failed"
    assert record["provider_exchanges"][0]["status"] == "invalid_response"
    assert record["tool_outcomes"] == []


def test_noncanonical_tool_results_persist_failure_evidence(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add",), tool_invalid=True)
    record = result.execution.as_dict()
    assert record["terminal_status"] == "tool_failed"
    assert record["problem"]["error_code"] == "replay.tool_invalid_result"
    assert len(record["tool_outcomes"]) == 1
    _, _, timed_result = _run(
        tmp_path / "timed",
        sequence=("add",),
        tool_invalid=True,
        deadlines={"total": 10_000, "idle": 1_000, "provider_call": 2_000, "tool_call": 1},
        monotonic=_SlowMonotonic(),
    )
    timed = timed_result.execution.as_dict()
    assert timed["terminal_status"] == "timed_out"
    assert timed["problem"]["error_code"] == "replay.tool_timeout"


def test_workspace_directory_replacement_is_blocked_by_worker_sandbox(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("host",), host_replace=True)
    record = result.execution.as_dict()
    assert record["terminal_status"] == "host_failed"
    assert record["problem"]["error_code"] == "replay.host_failed"
    assert not any(path.is_dir() for path in result.execution_path.parent.iterdir())
    assert record["integrity_verified"] is True
    assert result.manifest.as_dict()["publish_status"] == "complete"
    assert result.manifest.as_dict()["integrity_verified"] is True


def test_initial_workspace_paths_must_be_canonical() -> None:
    with pytest.raises(ReplayRunError, match="unsafe initial workspace path"):
        ReplayScenario("task", ({"role": "user", "content": "go"},), {"a//b": "content"}, ())
    with pytest.raises(ReplayRunError, match="tool_schemas"):
        ReplayScenario("task", ({"role": "user", "content": "go"},), {}, (None,))  # type: ignore[arg-type]


def test_authorization_and_cookie_fields_are_always_redacted(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("credential_fields",))
    store = ArtifactStore(workspace.path(".breadboard/artifacts"))
    for entry in result.manifest.as_dict()["entries"]:
        payload = store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))
        assert b"UNLISTED" not in payload
    for subdir, kwargs in (
        ("provider-error", {"sequence": ("provider_secret_error",)}),
        ("tool-error", {"sequence": ("add",), "tool_fail": True}),
    ):
        failure_workspace, _, failure_result = _run(tmp_path / subdir, **kwargs)
        failure_store = ArtifactStore(failure_workspace.path(".breadboard/artifacts"))
        for entry in failure_result.manifest.as_dict()["entries"]:
            payload = failure_store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))
            assert b"UNLISTED" not in payload


def test_manifest_paths_and_content_address_are_revalidated(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("done",))
    unsafe = result.manifest.as_dict()
    unsafe["entries"][0]["location_kind"] = "workspace_relative_path"
    unsafe["entries"][0]["location"] = "../escape"
    with pytest.raises(ReplayExecutionError, match="within the workspace"):
        ReplayArtifactManifest.from_dict(unsafe)
    tampered = result.manifest.as_dict()
    tampered["entries"][0]["role"] = "changed"
    with pytest.raises(ReplayExecutionError, match="manifest_id"):
        ReplayArtifactManifest.from_dict(tampered)
    noncanonical = result.manifest.as_dict()
    noncanonical["entries"][0]["location_kind"] = "workspace_relative_path"
    noncanonical["entries"][0]["location"] = "a//b"
    with pytest.raises(ReplayExecutionError, match="canonical"):
        ReplayArtifactManifest.from_dict(noncanonical)


def test_plan_mutation_invalidates_identity_and_execution_reuse(tmp_path: Path) -> None:
    _, plan, result = _run(tmp_path, sequence=("done",))
    changed = plan.as_dict(); changed["budgets"]["turns"] += 1
    with pytest.raises(ReplayPlanError, match="plan_id"):
        ReplayPlan.from_dict(changed)
    replacement = _scenario(); lock, bindings, other = _plan(replacement); other_record = other.as_dict(); other_record["budgets"]["turns"] += 1; other_record["plan_id"] = "replay_plan:" + "2" * 64
    with pytest.raises(ReplayPlanError):
        ReplayPlan.from_dict(other_record)
    assert lock.as_dict()["graph_hash"] == plan.as_dict()["harness_lock_sha256"]
    result.execution.verify_plan(plan)
    mismatched_locks = result.execution.as_dict()
    mismatched_locks["lane_lock_sha256"] = "sha256:" + "2" * 64
    with pytest.raises(ReplayExecutionError, match="lock hashes"):
        ReplayExecution.from_dict(mismatched_locks).verify_plan(plan)


def test_stored_execution_cannot_acquire_executed_pass_or_claimability(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("done",))
    injected = result.execution.as_dict(); injected["executed_pass"] = True
    with pytest.raises(ReplayExecutionError, match="fields"):
        ReplayExecution.from_dict(injected)
    promoted = result.execution.as_dict()
    promoted["claimable"] = True
    promoted["normalization_evidence_ids"] = ["normalization:forged"]
    promoted["comparison_report_id"] = "comparison:forged"
    with pytest.raises(ReplayExecutionError, match="candidate replay"):
        ReplayExecution.from_dict(promoted)


def test_persistence_failure_rolls_back_cas_and_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = replay_runner_module.AnchoredStorage.write_at
    def fail_execution(parent, name, content):
        if name == "execution.json": raise OSError("injected persistence failure")
        return original(parent, name, content)
    monkeypatch.setattr(replay_runner_module.AnchoredStorage, "write_at", staticmethod(fail_execution))
    with pytest.raises(OSError, match="injected"):
        _run(tmp_path, sequence=("done",))
    artifact_root = tmp_path / ".breadboard/artifacts"
    digest_files = [path for path in artifact_root.rglob("*") if path.is_file() and len(path.name) == 64]
    assert digest_files == []
    assert list((tmp_path / ".breadboard/replays").glob(".*.staging")) == []


def test_replay_contracts_remain_candidate_until_lifecycle_promotion() -> None:
    surface = json.loads((ROOT / "contracts/public/record_surface.v1.json").read_text(encoding="utf-8"))
    roles = {row["role_id"]: row["status"] for row in surface["roles"]}
    assert roles["replay_plan"] == "candidate"
    assert roles["replay_execution"] == "candidate"
    assert roles["provider_exchange"] == "candidate"


def test_worker_ipc_never_unpickles_raw_provider_objects(tmp_path: Path) -> None:
    marker = tmp_path / "unpickled"
    _, _, result = _run(tmp_path / "workspace", sequence=("done",), provider_instance=_RawProvider(marker))
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert not marker.exists()


def test_runtime_and_environment_bindings_capture_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _runtime_type_identity(_AddTool()) != _runtime_type_identity(_AddTool(fail=True))
    numeric_key = _AddTool(); numeric_key.config = {1: "x"}
    string_key = _AddTool(); string_key.config = {"1": "x"}
    assert _runtime_type_identity(numeric_key) != _runtime_type_identity(string_key)
    first_bytes = _AddTool(); first_bytes.config = {b"a": "x"}
    second_bytes = _AddTool(); second_bytes.config = {b"b": "x"}
    assert _runtime_type_identity(first_bytes) != _runtime_type_identity(second_bytes)
    monkeypatch.setenv("BB_REPLAY_FIXTURE", "first")
    first, _ = _environment_binding(("BB_REPLAY_FIXTURE",), {})
    monkeypatch.setenv("BB_REPLAY_FIXTURE", "second")
    second, _ = _environment_binding(("BB_REPLAY_FIXTURE",), {})
    original_execute = _AddTool.execute
    def replacement_execute(self, arguments):
        return {"sum": 999}
    before_runtime = _runtime_type_identity(_AddTool())
    monkeypatch.setattr(_AddTool, "execute", replacement_execute)
    assert _runtime_type_identity(_AddTool()) != before_runtime
    monkeypatch.setattr(_AddTool, "execute", original_execute)
    assert first != second
    monkeypatch.delenv("BB_REPLAY_FIXTURE")
    with pytest.raises(ReplayRunError, match="absent"):
        _environment_binding(("BB_REPLAY_FIXTURE",), {})


def test_environment_only_values_are_redacted_from_all_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = "ENVIRONMENT_ONLY_CREDENTIAL"
    monkeypatch.setenv("BB_REPLAY_ENV_SECRET", value)
    workspace, _, result = _run(tmp_path, sequence=("environment_value",), environment_allowlist=("BB_REPLAY_ENV_SECRET",))
    execution = result.execution.as_dict()
    assert execution["terminal_status"] == "completed"
    assert execution["provider_exchanges"][0]["status"] == "completed"
    store = ArtifactStore(workspace.path(".breadboard/artifacts"))
    for entry in result.manifest.as_dict()["entries"]:
        payload = store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))
        assert value.encode() not in payload
    response_entry = next(entry for entry in result.manifest.as_dict()["entries"] if entry["role"] == "provider_response")
    response = json.loads(store.read(ArtifactRef(response_entry["sha256"], response_entry["size_bytes"], response_entry["media_type"])))
    assert response["messages"][0]["content"] == "<redacted>"


def test_overlapping_environment_secrets_are_redacted_longest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BB_REPLAY_SHORT_SECRET", "OVERLAP")
    monkeypatch.setenv("BB_REPLAY_LONG_SECRET", "OVERLAP_SECRET")
    workspace, _, result = _run(
        tmp_path,
        sequence=("overlapping_environment_value",),
        environment_allowlist=("BB_REPLAY_SHORT_SECRET", "BB_REPLAY_LONG_SECRET"),
    )
    response_entry = next(entry for entry in result.manifest.as_dict()["entries"] if entry["role"] == "provider_response")
    response = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(response_entry["sha256"], response_entry["size_bytes"], response_entry["media_type"])))
    assert response["messages"][0]["content"] == "<redacted>"




def test_manifest_rejects_workspace_root_location(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("done",))
    malformed = result.manifest.as_dict()
    malformed["entries"][0]["location_kind"] = "workspace_relative_path"
    malformed["entries"][0]["location"] = "."
    with pytest.raises(ReplayExecutionError, match="workspace_relative_path"):
        ReplayArtifactManifest.from_dict(malformed)


def test_policy_evaluation_obeys_deadline_and_persists_timeout(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add",), authorize=_hang_policy, deadlines={"total": 1_000, "idle": 1_000, "provider_call": 100, "tool_call": 20})
    record = result.execution.as_dict()
    assert record["terminal_status"] == "timed_out"
    assert record["problem"]["error_code"] == "replay.policy_timeout"

    assert next(row for row in record["policy_decisions"] if row["kind"] == "policy")["decision"] == "denied"

def test_cancellation_before_policy_dispatch_records_denial(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add",), cancelled=_CancelBeforePolicy())
    record = result.execution.as_dict()
    assert record["terminal_status"] == "cancelled"
    assert next(row for row in record["policy_decisions"] if row["kind"] == "policy")["decision"] == "denied"


def test_tool_relative_filesystem_writes_are_workspace_anchored(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("add",), tool_instances={"add": _RelativeWriteTool()})
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "workspace_after")
    snapshot = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert "relative-output.txt" in {row["path"] for row in snapshot["files"]}
    assert not (ROOT / "relative-output.txt").exists()


def test_worker_os_sandbox_denies_process_creation(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add",), tool_instances={"add": _ForkTool()})
    record = result.execution.as_dict()
    assert record["terminal_status"] == "tool_failed"
    assert record["tool_outcomes"]


def test_worker_os_sandbox_denies_tool_network_egress(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add",), tool_instances={"add": _NetworkTool()})
    record = result.execution.as_dict()
    assert record["terminal_status"] == "tool_failed"
    assert record["problem"]["error_code"] == "replay.tool_failed"


def test_worker_stdio_cannot_bypass_evidence_redaction(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    _, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _PrintingTool()})
    captured = capfd.readouterr()
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert "SECRET" not in captured.out
    assert "SECRET" not in captured.err


def test_worker_os_sandbox_denies_writes_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "escaped.txt"
    _, _, result = _run(tmp_path / "workspace", sequence=("add",), tool_instances={"add": _AbsoluteWriteTool(outside)})
    record = result.execution.as_dict()
    assert record["terminal_status"] == "tool_failed"
    assert not outside.exists()


def test_worker_os_sandbox_denies_reads_outside_workspace(tmp_path: Path) -> None:
    secret = "OUTSIDE_WORKSPACE_CONTENT"
    outside = tmp_path / "outside.txt"
    outside.write_text(secret, encoding="utf-8")
    workspace, _, result = _run(tmp_path / "workspace", sequence=("add",), tool_instances={"add": _OutsideReadTool(outside)})
    assert result.execution.as_dict()["terminal_status"] == "tool_failed"
    store = ArtifactStore(workspace.path(".breadboard/artifacts"))
    for entry in result.manifest.as_dict()["entries"]:
        assert secret.encode() not in store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))


def test_worker_closes_inherited_outside_file_descriptors(tmp_path: Path) -> None:
    secret = "INHERITED_DESCRIPTOR_CONTENT"
    outside = tmp_path / "inherited.txt"
    outside.write_text(secret, encoding="utf-8")
    tool = _InheritedFdReadTool(outside)
    try:
        workspace, _, result = _run(tmp_path / "workspace", sequence=("add",), tool_instances={"add": tool})
    finally:
        __import__("os").close(tool.descriptor)
    assert result.execution.as_dict()["terminal_status"] == "tool_failed"
    store = ArtifactStore(workspace.path(".breadboard/artifacts"))
    for entry in result.manifest.as_dict()["entries"]:
        assert secret.encode() not in store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))
def test_worker_closes_inherited_socket_descriptors(tmp_path: Path) -> None:
    tool = _InheritedSocketReadTool()
    try:
        workspace, _, result = _run(tmp_path, sequence=("add",), tool_instances={"add": tool})
    finally:
        tool.close()
    assert result.execution.as_dict()["terminal_status"] == "tool_failed"
    store = ArtifactStore(workspace.path(".breadboard/artifacts"))
    for entry in result.manifest.as_dict()["entries"]:
        assert b"INHERITED_SOCKET_CONTENT" not in store.read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"]))


def test_workspace_special_file_is_rejected_from_snapshot(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add",), tool_instances={"add": _WorkspaceFifoTool()})
    record = result.execution.as_dict()
    assert record["terminal_status"] == "host_failed"
    assert record["integrity_verified"] is False
    assert record["problem"]["error_code"] == "replay.workspace_special_file"
    assert result.manifest.as_dict()["publish_status"] == "quarantined"


def test_exec_isolated_tool_cannot_reach_sibling_capabilities(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _FrameInspectionTool()})
    assert result.execution.as_dict()["terminal_status"] == "completed"
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "tool_outcome")
    outcome = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert outcome["result"]["exposed"] == []


def test_provider_worker_cannot_mutate_workspace(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("done",), provider_instance=_WritingProvider(("done",)))
    assert result.execution.as_dict()["terminal_status"] == "provider_failed"
    assert not list(tmp_path.rglob("provider-bypass.txt"))


def test_host_worker_supports_attested_subprocess_execution(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("host", "done"), host_instance=_SubprocessHost())
    assert result.execution.as_dict()["terminal_status"] == "completed"
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "tool_outcome")
    outcome = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert outcome["result"]["exit_code"] == 0, outcome
    after_entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "workspace_after")
    after = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(after_entry["sha256"], after_entry["size_bytes"], after_entry["media_type"])))
    assert "host-subprocess.txt" in {row["path"] for row in after["files"]}


def test_allowlisted_environment_is_provider_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BB_REPLAY_ENV_SECRET", "PROVIDER_ONLY_SECRET")
    workspace, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _EnvironmentReadTool()}, environment_allowlist=("BB_REPLAY_ENV_SECRET",))
    assert result.execution.as_dict()["terminal_status"] == "completed"
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "tool_outcome")
    outcome = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert outcome["result"]["value"] is None


def test_provider_dump_logger_is_disabled_inside_replay_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "provider-dumps"
    monkeypatch.setenv("KC_PROVIDER_LOG_DIR", str(outside))
    previous = (provider_dump_logger.log_dir, provider_dump_logger.workspace_override, provider_dump_logger.session_override, provider_dump_logger.enabled)
    provider_dump_logger.log_dir = str(outside)
    provider_dump_logger.enabled = True
    try:
        _, _, result = _run(tmp_path / "workspace", sequence=("done",), provider_instance=_DumpingProvider(("done",)), environment_allowlist=("KC_PROVIDER_LOG_DIR",))
    finally:
        provider_dump_logger.log_dir, provider_dump_logger.workspace_override, provider_dump_logger.session_override, provider_dump_logger.enabled = previous
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert not outside.exists()


def test_synthesizes_deterministic_missing_provider_call_ids(tmp_path: Path) -> None:
    workspace, _, result = _run(tmp_path, sequence=("add_without_id", "done"))
    assert result.execution.as_dict()["terminal_status"] == "completed"
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "provider_response")
    response = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert response["messages"][0]["tool_calls"][0]["id"].endswith(":message:1:call:1")


def test_terminal_failure_details_are_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SYMLINK_NAME_SECRET"
    monkeypatch.setenv("BB_REPLAY_ENV_SECRET", secret)
    _, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _SecretSymlinkTool(secret)}, environment_allowlist=("BB_REPLAY_ENV_SECRET",))
    execution = result.execution.as_dict()
    assert execution["terminal_status"] == "host_failed"
    assert secret not in json.dumps(execution, sort_keys=True)
    assert "<redacted>" in execution["completion_reason"]


def test_artifact_store_remains_anchored_during_namespace_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    moved = tmp_path / "workspace" / ".breadboard" / "artifacts-original"
    original = replay_runner_module._anchored_artifact_store
    def swap_store(workspace: BreadBoardWorkspace):
        store, descriptors = original(workspace)
        artifact_path = workspace.root / ".breadboard" / "artifacts"
        artifact_path.rename(moved)
        artifact_path.symlink_to(outside, target_is_directory=True)
        return store, descriptors
    monkeypatch.setattr(replay_runner_module, "_anchored_artifact_store", swap_store)
    _, _, result = _run(tmp_path / "workspace", sequence=("done",))
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert not list(outside.iterdir())
    assert any(path.is_file() for path in moved.glob("sha256/*/*"))


def test_replay_parent_remains_anchored_during_namespace_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside-replays"
    outside.mkdir()
    moved = tmp_path / "workspace" / ".breadboard" / "replays-original"
    original = replay_runner_module._anchored_replay_parent

    def swap_parent(workspace: BreadBoardWorkspace):
        replay_path, descriptors = original(workspace)
        replay_path.rename(moved)
        replay_path.symlink_to(outside, target_is_directory=True)
        return replay_path, descriptors

    monkeypatch.setattr(replay_runner_module, "_anchored_replay_parent", swap_parent)
    _, _, result = _run(tmp_path / "workspace", sequence=("done",))
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert not list(outside.iterdir())
    assert any(path.name == "execution.json" for path in moved.glob("*/execution.json"))


def test_failed_publication_removes_swapped_final_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside-publication"
    outside.mkdir()
    original = replay_runner_module._rename_no_replace
    moved: list[Path] = []
    def swap_stage(source: Path, destination: Path, *, parent_descriptor: int | None = None) -> None:
        original_stage = source.with_name(source.name + ".original")
        source.rename(original_stage)
        moved.append(original_stage)
        source.symlink_to(outside, target_is_directory=True)
        original(source, destination, parent_descriptor=parent_descriptor)
    monkeypatch.setattr(replay_runner_module, "_rename_no_replace", swap_stage)
    with pytest.raises(ReplayRunError, match="published replay identity"):
        _run(tmp_path / "workspace", sequence=("done",))
    assert moved and not moved[0].exists()
    assert not list(outside.iterdir())
    assert not any((tmp_path / "workspace" / ".breadboard" / "replays").iterdir())
def test_publication_fsyncs_parent_directory_after_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renamed = [False]
    parent_synced = [False]
    original_rename = replay_runner_module._rename_no_replace
    original_fsync = replay_runner_module.os.fsync

    def track_rename(source: Path, destination: Path, *, parent_descriptor: int | None = None) -> None:
        original_rename(source, destination, parent_descriptor=parent_descriptor)
        renamed[0] = True

    def track_fsync(descriptor: int) -> None:
        if renamed[0]:
            parent_synced[0] = True
        original_fsync(descriptor)

    monkeypatch.setattr(replay_runner_module, "_rename_no_replace", track_rename)
    monkeypatch.setattr(replay_runner_module.os, "fsync", track_fsync)
    _, _, result = _run(tmp_path, sequence=("done",))
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert parent_synced == [True]






def test_worker_can_resolve_provider_hostnames(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("resolve_localhost",))
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_staging_cleanup_removes_special_filesystem_entries(tmp_path: Path) -> None:
    _, _, result = _run(tmp_path, sequence=("add", "done"), tool_instances={"add": _StageFifoTool()})
    assert result.execution.as_dict()["terminal_status"] == "tool_failed"
    assert not (result.execution_path.parent / "untracked.fifo").exists()
    assert sorted(path.name for path in result.execution_path.parent.iterdir()) == ["execution.json", "manifest.json"]


def test_generated_execution_id_is_validated_before_path_creation(tmp_path: Path) -> None:
    with pytest.raises(ReplayExecutionError, match="portable identifier"):
        _run(tmp_path, sequence=("done",), ids=_BadIds())
    assert not (tmp_path / ".breadboard/replays").exists()

def test_worker_resets_workspace_cwd_between_calls(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace, _, result = _run(tmp_path / "workspace", sequence=("add", "add", "done"), tool_instances={"add": _ChangingCwdTool(outside)})
    entry = next(row for row in result.manifest.as_dict()["entries"] if row["role"] == "workspace_after")
    snapshot = json.loads(ArtifactStore(workspace.path(".breadboard/artifacts")).read(ArtifactRef(entry["sha256"], entry["size_bytes"], entry["media_type"])))
    assert "after-cwd-change.txt" in {row["path"] for row in snapshot["files"]}
    assert not (outside / "after-cwd-change.txt").exists()


def test_active_runtime_bindings_must_match_frozen_plan(tmp_path: Path) -> None:
    active = {name: {"binding": name} for name in ("capability_probe_sha256", "model_policy_sha256", "normalizer_config_sha256", "comparator_config_sha256")}
    active["model_policy_sha256"] = {"binding": "changed"}
    with pytest.raises(ReplayPlanError, match="model_policy_sha256"):
        _run(tmp_path, sequence=("done",), runtime_bindings=active)
    assert not (tmp_path / ".breadboard/replays").exists()


def test_redaction_covers_mapping_keys_and_rejects_collisions(tmp_path: Path) -> None:
    counter = [0]
    redacted = _redact({"preSECRETpost": "value"}, secrets=("SECRET",), workspace=tmp_path, counter=counter)
    assert redacted == {"pre<redacted>post": "value"}
    assert counter == [1]
    with pytest.raises(ReplayRunError, match="collide"):
        _redact({"SECRET": 1, "<redacted>": 2}, secrets=("SECRET",), workspace=tmp_path, counter=[0])




def test_replay_namespace_symlink_is_rejected_without_writing_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    metadata = workspace / ".breadboard"
    outside = tmp_path / "outside"
    metadata.mkdir(parents=True)
    outside.mkdir()
    (metadata / "replays").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ReplayRunError, WorkspacePathError)):
        _run(workspace, sequence=("done",))
    assert not list(outside.iterdir())


def test_provider_metadata_is_not_applied_before_result_validation(tmp_path: Path) -> None:
    marker = tmp_path / "provider-metadata.json"
    context = types.SimpleNamespace(session_state=_MetadataSink(marker))
    _, _, result = _run(tmp_path / "workspace", sequence=("invalid_message",), provider_context=context)
    assert result.execution.as_dict()["terminal_status"] == "provider_failed"
    assert not marker.exists()


def test_provider_metadata_is_applied_after_result_validation(tmp_path: Path) -> None:
    marker = tmp_path / "provider-metadata.json"
    context = types.SimpleNamespace(session_state=_MetadataSink(marker))
    _, _, result = _run(tmp_path / "workspace", sequence=("done",), provider_context=context)
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert json.loads(marker.read_text(encoding="utf-8")) == {"api_key": "SECRET"}


def test_usage_accounting_is_exact_at_large_integer_and_decimal_boundaries() -> None:
    accountant = replay_runner_module._UsageAccountant.from_budgets({"tokens": 9_007_199_254_740_993, "cost": 0.3})
    assert accountant.add({"total_tokens": 9_007_199_254_740_992, "cost_amount": 0.1, "cost_currency": "USD"}) is False
    assert accountant.add({"total_tokens": 1, "cost_amount": 0.2, "cost_currency": "USD"}) is False
    assert accountant.add({"total_tokens": 1, "cost_amount": 0, "cost_currency": "USD"}) is True


def test_redaction_rejects_recursive_containers(tmp_path: Path) -> None:
    recursive: list[Any] = []
    recursive.append(recursive)
    with pytest.raises(ReplayRunError, match="cyclic"):
        _redact(recursive, secrets=(), workspace=tmp_path, counter=[0])


def test_normalized_usage_preserves_large_integer_cost_exactly() -> None:
    value = 9_007_199_254_740_993
    usage = normalized_provider_usage(ProviderResult([ProviderMessage("assistant", "done")], None, {"cost_amount": value}, metadata={}))
    assert usage["cost_amount"] == value
    assert type(usage["cost_amount"]) is int


def test_real_session_provider_context_excludes_runtime_only_metadata(tmp_path: Path) -> None:
    from agentic_coder_prototype.state.session_state import SessionState
    session_state = SessionState(str(tmp_path), "fixture")
    session_state.set_provider_metadata("anthropic_rate_limits", {"tokens_remaining": 17})
    session_state.set_provider_metadata("conversation_id", "conversation-fixture")
    session_state.set_provider_metadata("current_turn_index", 3)
    session_state.set_provider_metadata("previous_response_id", "response-fixture")
    session_state.set_provider_metadata("control_queue", object())
    context = ProviderRuntimeContext(session_state=session_state, agent_config={}, extra={})
    _, _, result = _run(
        tmp_path / "workspace",
        sequence=("done",),
        provider_instance=_ProviderMetadataInspectingProvider(("done",)),
        provider_context=context,
    )
    assert result.execution.as_dict()["terminal_status"] == "completed"


def test_provider_context_strips_execution_capabilities(tmp_path: Path) -> None:
    marker = tmp_path / "provider-capability-called"
    provider = _ContextInspectingProvider(("done",))
    context = types.SimpleNamespace(session_state=None, agent_config={}, extra={"host": _ContextCapability(marker)})
    _, _, result = _run(tmp_path / "workspace", sequence=("done",), provider_instance=provider, provider_context=context)
    assert result.execution.as_dict()["terminal_status"] == "completed"
    assert not marker.exists()

def test_callable_executor_identity_includes_mutable_object_state() -> None:
    tool = _CallableConfiguredTool(1)
    before = _runtime_type_identity(tool)
    tool.value = 2
    after = _runtime_type_identity(tool)
    assert before["configuration_sha256"] != after["configuration_sha256"]



def test_scenario_rejects_invalid_tool_schema_before_plan_construction() -> None:
    with pytest.raises(ReplayRunError, match="invalid parameters schema"):
        ReplayScenario(
            "fixture",
            ({"role": "user", "content": "go"},),
            {},
            ({"type": "function", "function": {"name": "bad", "parameters": {"type": "not-a-json-schema-type"}}},),
        )


@pytest.mark.parametrize("location", ("C:relative", "C:/absolute"))
def test_manifest_rejects_windows_drive_prefixed_locations(tmp_path: Path, location: str) -> None:
    _, _, result = _run(tmp_path, sequence=("done",))
    malformed = result.manifest.as_dict()
    malformed["entries"][0]["location_kind"] = "workspace_relative_path"
    malformed["entries"][0]["location"] = location
    with pytest.raises(ReplayExecutionError, match="workspace_relative_path"):
        ReplayArtifactManifest.from_dict(malformed)


@pytest.mark.parametrize("currency", ("KSD", "ＵSD", "İSD"))
def test_usage_rejects_unicode_uppercase_currency(currency: str) -> None:
    result = ProviderResult([ProviderMessage("assistant", "done")], None, {"cost_amount": 1, "cost_currency": currency}, metadata={})
    with pytest.raises(ProviderRuntimeError, match="three-letter uppercase"):
        normalized_provider_usage(result)


def test_usage_rejects_unrepresentable_integer_cost() -> None:
    result = ProviderResult([ProviderMessage("assistant", "done")], None, {"cost_amount": 10 ** 10_000, "cost_currency": "USD"}, metadata={})
    with pytest.raises(ProviderRuntimeError, match="representable"):
        normalized_provider_usage(result)


def test_stored_usage_rejects_unrepresentable_integer_cost() -> None:
    with pytest.raises(ReplayExecutionError, match="representable"):
        _validate_usage({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_amount": 10 ** 10_000, "cost_currency": "USD"})
