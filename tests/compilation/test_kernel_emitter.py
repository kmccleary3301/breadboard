from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation.primitive_records import PrimitiveCompileError, SPEC_REGISTRY, finalize_record, get_spec
from agentic_coder_prototype.runtime.kernel_emitter import JsonlKernelEmitter
from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest
from agentic_coder_prototype.api.cli_bridge.runtime_emission import emit_session_start_records
from agentic_coder_prototype.conductor.execution import build_exec_func, execute_agent_calls
from agentic_coder_prototype.state.session_state import SessionState

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
REGISTRY_DIR = ROOT / "contracts" / "kernel" / "registries"
PAYLOAD_SCHEMA_DIR = SCHEMA_DIR / "payloads"


def _actor() -> dict[str, str]:
    return {"actor_kind": "system", "actor_id": "kernel"}


def _visibility() -> dict[str, bool | str]:
    return {"model_visible": True, "provider_visible": True, "host_visible": True, "redaction_state": "none"}


def _kernel_event(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "event_id": "event-1",
        "run_id": "run-1",
        "session_id": "session-1",
        "seq": 1,
        "occurred_at_utc": "2026-07-03T00:00:00Z",
        "actor": _actor(),
        "visibility": _visibility(),
        "kind": "turn.started",
        "payload": {"turn": 0, "seq": 1},
        "payload_schema_version": "bb.payload.turn.started.v1",
    }
    record.update(overrides)
    return record



def test_jsonl_kernel_emitter_strict_writes_stream_and_manifest_on_flush(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(tmp_path, mode="strict", clock=lambda: "2026-07-03T00:00:00Z")

    finalized = emitter.emit("bb.kernel_event.v2", _kernel_event())
    emitter.flush()

    assert finalized["schema_version"] == "bb.kernel_event.v2"
    stream_lines = (tmp_path / "records" / "bb.kernel_event.v2.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in stream_lines] == [finalized]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "session_id": "session-1",
        "run_id": "run-1",
        "started_at_utc": "2026-07-03T00:00:00Z",
        "counts_by_schema": {"bb.kernel_event.v2": 1},
        "quarantine_count": 0,
        "session_transcript_digest": None,
    }


def test_jsonl_kernel_emitter_strict_raises_invalid_record(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(tmp_path, mode="strict", clock=lambda: "2026-07-03T00:00:00Z")
    invalid = _kernel_event()
    invalid.pop("run_id")

    with pytest.raises(PrimitiveCompileError):
        emitter.emit("bb.kernel_event.v2", invalid)


def test_jsonl_kernel_emitter_quarantines_invalid_record_and_continues(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(tmp_path, mode="quarantine", clock=lambda: "2026-07-03T00:00:00Z")
    invalid = _kernel_event()
    invalid.pop("run_id")

    returned = emitter.emit("bb.kernel_event.v2", invalid)
    valid = emitter.emit("bb.kernel_event.v2", _kernel_event(event_id="event-2"))
    emitter.flush()

    assert returned == invalid
    assert valid["event_id"] == "event-2"
    quarantine = json.loads((tmp_path / "records" / "_quarantine.jsonl").read_text(encoding="utf-8"))
    assert quarantine["schema_version"] == "bb.kernel_event.v2"
    assert quarantine["record"] == invalid
    assert "run_id" in quarantine["error"]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts_by_schema"] == {"bb.kernel_event.v2": 1}
    assert manifest["quarantine_count"] == 1
    assert manifest["session_transcript_digest"] is None

def test_jsonl_kernel_emitter_require_payload_schema_rejects_null_policy(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(
        tmp_path,
        mode="strict",
        clock=lambda: "2026-07-03T00:00:00Z",
        require_payload_schema=True,
    )

    with pytest.raises(PrimitiveCompileError, match="payload_schema_version is required by emitter policy"):
        emitter.emit("bb.kernel_event.v2", _kernel_event(payload_schema_version=None))


def test_jsonl_kernel_emitter_require_payload_schema_quarantines_null_policy(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(
        tmp_path,
        mode="quarantine",
        clock=lambda: "2026-07-03T00:00:00Z",
        require_payload_schema=True,
    )
    record = _kernel_event(payload_schema_version=None)

    assert emitter.emit("bb.kernel_event.v2", record) == record
    emitter.flush()
    quarantine = json.loads((tmp_path / "records" / "_quarantine.jsonl").read_text(encoding="utf-8"))
    assert quarantine["schema_version"] == "bb.kernel_event.v2"
    assert quarantine["record"] == record
    assert quarantine["errors"] == [
        {"pointer": "/payload_schema_version", "message": "payload_schema_version is required by emitter policy"}
    ]


def test_jsonl_kernel_emitter_require_tool_spec_ref_rejects_null_policy(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(
        tmp_path,
        mode="strict",
        clock=lambda: "2026-07-03T00:00:00Z",
        require_tool_spec_ref=True,
    )
    record = {
        "call_id": "call-1",
        "tool_name": "bash",
        "args": {"command": "true"},
        "state": "declared",
        "requested_at_utc": "2026-07-03T00:00:00Z",
        "actor": _actor(),
        "visibility": _visibility(),
        "tool_spec_ref": None,
    }

    with pytest.raises(PrimitiveCompileError, match="tool_spec_ref is required by emitter policy"):
        emitter.emit("bb.tool_call.v2", record)


def test_jsonl_kernel_emitter_off_is_noop(tmp_path: Path) -> None:
    emitter = JsonlKernelEmitter(tmp_path, mode="off", clock=lambda: "2026-07-03T00:00:00Z")
    record = _kernel_event()

    assert emitter.emit("bb.kernel_event.v2", record) == record
    emitter.flush()

    assert not (tmp_path / "records").exists()
    assert not (tmp_path / "manifest.json").exists()


def test_runtime_emission_soak_report_covers_strict_sessions_and_flag_off_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.runtime_emission_soak import build_soak_report

    out_dir = tmp_path / "runtime_soak"
    monkeypatch.delenv("BREADBOARD_PRIMITIVES_MODE", raising=False)

    report = build_soak_report(out_dir=out_dir, sessions=25, provider_latency_ms=0)

    assert report["schema_version"] == "bb.ns.runtime_soak.v1"
    assert report["sessions"] >= 25
    assert report["strict_seconds"] >= 0
    assert report["flag_off_seconds"] >= 0
    assert report["quarantine_count"] == 0
    assert report["quarantine_passed"] is True
    assert len(report["strict_rows"]) == report["sessions"]
    assert len(report["flag_off_rows"]) == report["sessions"]
    assert len(report["varied_workload_labels"]) >= 3
    assert all(row["manifest"]["quarantine_count"] == 0 for row in report["strict_rows"])
    assert all(row["manifest"]["counts_by_schema"] for row in report["strict_rows"])
    assert all(row["manifest"]["counts_by_schema"] == {} for row in report["flag_off_rows"])

    assert report["mode_default_without_env"] == "strict"
    assert report["dev_ci_default_mode"] == "strict"
    assert report["default_mode_passed"] is True
    assert report["invalid_mode_fallback_passed"] is True


def test_dev_ci_runtime_emission_mode_resolves_strict_when_unset_or_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_coder_prototype.api.cli_bridge.models import SessionStatus
    from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
    from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner

    class FakeAgent:
        _local_mode = True

        def __init__(self) -> None:
            self.config: dict[str, Any] = {}
            self.workspace_dir = str(tmp_path / "workspace")
            self.calls: list[dict[str, Any]] = []

        def run_task(self, _task: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {
                "completion_summary": {"completed": True, "steps_taken": 1, "reason": "done"},
                "messages": [{"role": "assistant", "content": "done"}],
            }

    def run_with_mode_env(raw_mode: str | None) -> str | None:
        if raw_mode is None:
            monkeypatch.delenv("BREADBOARD_PRIMITIVES_MODE", raising=False)
        else:
            monkeypatch.setenv("BREADBOARD_PRIMITIVES_MODE", raw_mode)
        monkeypatch.setenv("BREADBOARD_EMIT_PRIMITIVES", "1")
        session = SessionRecord(
            session_id=f"mode-session-{raw_mode or 'unset'}",
            status=SessionStatus.RUNNING,
            metadata={"runtime_record_dir": str(tmp_path / f"runtime_records_{raw_mode or 'unset'}")},
        )
        runner = SessionRunner(
            session=session,
            registry=SessionRegistry(),
            request=SessionCreateRequest(config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml", task="probe"),
        )
        fake_agent = FakeAgent()
        runner._agent = fake_agent

        runner._execute_task("probe")

        return fake_agent.calls[0]["kernel_emitter_mode"]

    assert run_with_mode_env(None) == "strict"
    assert run_with_mode_env("not-a-mode") == "strict"


def test_session_state_add_message_dual_emits_kernel_event_v2_without_changing_legacy_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADBOARD_EMIT_PRIMITIVES", "1")
    legacy_events: list[tuple[str, dict[str, Any], int | None]] = []
    emitter = JsonlKernelEmitter(tmp_path, mode="strict", clock=lambda: "2026-07-03T00:00:00Z")
    state = SessionState(
        workspace="/tmp/workspace",
        image="test-image",
        event_emitter=lambda event_type, payload, turn=None: legacy_events.append((event_type, dict(payload), turn)),
        kernel_emitter=emitter,
        clock=lambda: "2026-07-03T00:00:00Z",
    )
    state.provider_metadata["session_id"] = "session-1"
    state.provider_metadata["run_id"] = "run-1"

    state.add_message({"role": "assistant", "content": "hello"}, to_provider=False)
    emitter.flush()

    assistant_legacy = [event for event in legacy_events if event[0] == "assistant_message"]
    assert assistant_legacy == [("assistant_message", {"message": {"role": "assistant", "content": "hello"}, "seq": 2}, None)]
    records = [
        json.loads(line)
        for line in (tmp_path / "records" / "bb.kernel_event.v2.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assistant_records = [record for record in records if record["kind"] == "message.assistant"]
    assert len(assistant_records) == 1
    assert assistant_records[0]["schema_version"] == "bb.kernel_event.v2"
    assert assistant_records[0]["payload_schema_version"] == "bb.payload.message.assistant.v1"
    assert assistant_records[0]["payload"] == {"message": {"role": "assistant", "content": "hello"}, "seq": 2}


def test_runtime_v2_schema_files_and_registry_files_are_registered() -> None:
    non_primitive_v2_schemas = {"bb.agent_config_surface.v2"}
    missing = []
    for schema_path in sorted(SCHEMA_DIR.glob("bb.*.v2.schema.json")):
        schema_version = schema_path.name.removesuffix(".schema.json")
        if schema_version in non_primitive_v2_schemas:
            continue
        if schema_version not in SPEC_REGISTRY:
            missing.append(schema_version)
    assert missing == []

    for registry_path in sorted(REGISTRY_DIR.glob("*.json")):
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "bb.registry.v1":
            continue
        finalized = finalize_record(get_spec("bb.registry.v1"), payload)
        assert finalized["schema_version"] == "bb.registry.v1"


def test_config_surface_fields_registry_matches_curated_inventory_contract() -> None:
    payload = json.loads((REGISTRY_DIR / "config_surface_fields.v1.json").read_text(encoding="utf-8"))

    finalized = finalize_record(get_spec("bb.registry.v1"), payload)
    entries = finalized["entries"]
    rows = {entry["id"]: entry for entry in entries}
    dossier_only_ids = {"profile", "tool_packs", "tool_bindings", "terminal_sessions"}
    expected_ids = {
        "completion",
        "concurrency",
        "enhanced_tools",
        "features",
        "guardrails",
        "long_running",
        "loop",
        "modes",
        "multi_agent",
        "permissions",
        "profile",
        "prompts",
        "provider_tools",
        "providers",
        "replay",
        "terminal_sessions",
        "tool_bindings",
        "tool_packs",
        "tools",
        "turn_strategy",
        "schema_version",
        "version",
        "workspace",
    }

    assert len(entries) == 23
    assert set(rows) == expected_ids

    for entry_id, row in rows.items():
        assert row["status"] == "active"
        assert row["description"].strip()
        note = row["metadata"]["note"]
        assert isinstance(note, str)
        assert note.strip()
        if entry_id in dossier_only_ids:
            assert row["metadata"]["classification"] == "dossier_only"
            assert row["metadata"]["consumer"] is None
        else:
            assert row["metadata"]["classification"] == "operational"
            consumer = row["metadata"]["consumer"]
            assert isinstance(consumer, str)
            assert consumer.strip()


def test_emitted_kernel_kinds_have_registry_rows_and_payload_schemas() -> None:
    registry = json.loads((REGISTRY_DIR / "kernel_event_kinds.v1.json").read_text(encoding="utf-8"))
    rows = {entry["id"]: entry for entry in registry["entries"]}
    failures: list[str] = []
    for event_type, meta in SessionState.event_family_registry().items():
        row = rows.get(event_type)
        if row is None:
            failures.append(f"{event_type}: missing registry row")
            continue
        row_meta = row.get("metadata", {})
        kind = row_meta.get("kernel_family") or row_meta.get("projection_family")
        if kind != meta["family"]:
            failures.append(f"{event_type}: registry kind {kind!r} != runtime family {meta['family']!r}")
            continue
        payload_schema_version = row_meta.get("payload_schema_version")
        expected = f"bb.payload.{kind}.v1"
        if payload_schema_version != expected:
            failures.append(f"{event_type}: payload schema marker {payload_schema_version!r} != {expected!r}")
        if not (PAYLOAD_SCHEMA_DIR / f"{expected}.schema.json").is_file():
            failures.append(f"{event_type}: missing payload schema {expected}")
    assert failures == []


def test_payload_schema_for_emitted_assistant_message_is_closed_and_validates_payload() -> None:
    schema_path = PAYLOAD_SCHEMA_DIR / "bb.payload.message.assistant.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    spec = {
        "message": {"role": "assistant", "content": "hello"},
        "seq": 1,
    }
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(spec)) == []
    invalid = dict(spec)
    invalid["unexpected"] = True
    assert list(validator.iter_errors(invalid))


def test_session_start_records_use_append_only_config_plane_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "agent.yaml"
    config_path.write_text("tools:\n  - name: echo\n    description: echo\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime_records"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(runtime_root))

    paths = emit_session_start_records(
        session_id="session-start-test",
        request=SessionCreateRequest(config_path=str(config_path), task="hello", stream=False),
        repo_root=ROOT,
        generated_at="2026-07-03T00:00:00Z",
    )
    paths_again = emit_session_start_records(
        session_id="session-start-test",
        request=SessionCreateRequest(config_path=str(config_path), task="hello", stream=False),
        repo_root=ROOT,
        generated_at="2026-07-03T00:00:01Z",
    )

    stream_path = runtime_root / "session-start-test" / "records" / "config_plane.jsonl"
    assert stream_path.is_file()
    envelopes = [json.loads(line) for line in stream_path.read_text(encoding="utf-8").splitlines()]
    assert len(envelopes) >= 12
    assert len(envelopes) % 2 == 0
    assert {entry["name"] for entry in envelopes[-(len(envelopes) // 2):]} >= {
        "effective_operation_policy",
        "effective_config_graph",
        "capability_registry",
        "effective_tool_surface",
        "work_item_queued",
        "work_item_running",
    }


def test_real_exec_func_emits_tool_call_outcome_render_and_transcript_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREADBOARD_EMIT_PRIMITIVES", "1")
    emitter = JsonlKernelEmitter(tmp_path, mode="strict", clock=lambda: "2026-07-03T00:00:00Z")
    state = SessionState(workspace="/tmp/workspace", image="test-image", kernel_emitter=emitter, clock=lambda: "2026-07-03T00:00:00Z")
    state.provider_metadata["session_id"] = "session-tool-test"
    state.provider_metadata["run_id"] = "run-tool-test"
    state.set_provider_metadata("current_turn_index", 3)

    class FakeConductor:
        workspace = "/tmp/workspace"
        hook_manager = None

        def _exec_raw(self, call: dict[str, Any]) -> dict[str, Any]:
            return {"stdout": f"ran {call['function']}", "exit_code": 0}

    exec_func = build_exec_func(FakeConductor(), state)  # type: ignore[arg-type]
    result = exec_func({"id": "call-1", "function": "echo", "arguments": {"text": "hi"}})
    state.add_transcript_entry({"tool": "echo", "result": result})
    state.emit_session_transcript_snapshot(reason="session_end")
    emitter.flush()

    def load_stream(schema_version: str) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in (tmp_path / "records" / f"{schema_version}.jsonl").read_text(encoding="utf-8").splitlines()
        ]

    tool_calls = load_stream("bb.tool_call.v2")
    assert [record["state"] for record in tool_calls] == ["declared", "executing", "completed"]
    assert {record["call_id"] for record in tool_calls} == {"call-1"}
    outcomes = load_stream("bb.tool_execution_outcome.v2")
    assert outcomes[0]["call_id"] == "call-1"
    assert outcomes[0]["terminal_state"] == "completed"
    renders = load_stream("bb.tool_model_render.v2")
    assert renders[0]["parts"][0]["content"] == "ran echo"
    transcripts = load_stream("bb.session_transcript.v2")
    assert transcripts[0]["session_id"] == "session-tool-test"
    assert transcripts[0]["items"][0]["content"] == {"tool": "echo", "result": {"stdout": "ran echo", "exit_code": 0}}
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["session_transcript_digest"].startswith("sha256:")


def test_validation_failure_emits_denied_tool_terminal_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREADBOARD_EMIT_PRIMITIVES", "1")
    emitter = JsonlKernelEmitter(tmp_path, mode="strict", clock=lambda: "2026-07-03T00:00:00Z")
    state = SessionState(workspace="/tmp/workspace", image="test-image", kernel_emitter=emitter, clock=lambda: "2026-07-03T00:00:00Z")
    state.provider_metadata["session_id"] = "session-validation-test"
    state.provider_metadata["run_id"] = "run-validation-test"
    call = SimpleNamespace(function="echo", arguments={"unexpected": True}, call_id="bad-call")

    class FakePermissionBroker:
        def ensure_allowed(self, session_state: SessionState, parsed_calls: list[Any]) -> None:
            return None

    class FakeAgentExecutor:
        allow_multiple_bash = False

        def canonical_tool_name(self, name: str) -> str:
            return name

        def execute_parsed_calls(self, parsed_calls: list[Any], exec_func: Any, transcript_callback: Any = None, policy_bypass: bool = False):
            return [], -1, {"error": "invalid tool arguments", "validation_failed": True}, {
                "total_calls": len(parsed_calls),
                "executed_calls": 0,
            }

    conductor = SimpleNamespace(agent_executor=FakeAgentExecutor(), permission_broker=FakePermissionBroker(), workspace=str(tmp_path))

    executed, failed_at, error, _meta = execute_agent_calls(conductor, [call], lambda _call: {}, state)
    emitter.flush()

    assert executed == []
    assert failed_at == -1
    assert error == {"error": "invalid tool arguments", "validation_failed": True}
    tool_calls = [
        json.loads(line)
        for line in (tmp_path / "records" / "bb.tool_call.v2.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [(record["call_id"], record["state"]) for record in tool_calls] == [("bad-call", "declared"), ("bad-call", "denied")]
    outcomes = [
        json.loads(line)
        for line in (tmp_path / "records" / "bb.tool_execution_outcome.v2.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert outcomes[0]["call_id"] == "bad-call"
    assert outcomes[0]["terminal_state"] == "denied"
    assert outcomes[0]["error"]["message"] == "invalid tool arguments"


def test_replay_session_from_records_round_trips_transcript_digest(tmp_path: Path) -> None:
    from scripts.replay_session_from_records import replay_session_from_records

    emitter = JsonlKernelEmitter(tmp_path, mode="strict", clock=lambda: "2026-07-03T00:00:00Z")
    event = emitter.emit(
        "bb.kernel_event.v2",
        _kernel_event(
            kind="message.user",
            payload={"message": {"role": "user", "content": "hello"}, "seq": 1},
            payload_schema_version="bb.payload.message.user.v1",
        ),
    )
    emitter.emit(
        "bb.session_transcript.v2",
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "event_cursor": 1,
            "items": [
                {
                    "kind": "transcript.entry",
                    "visibility": _visibility(),
                    "content": event["payload"]["message"],
                    "content_schema_version": None,
                    "seq": 0,
                    "provenance": {"source": "live"},
                }
            ],
            "metadata": {"snapshot_reason": "session_end"},
        },
    )
    emitter.close()

    result = replay_session_from_records(tmp_path)

    assert result["ok"] is True
    assert result["transcript_digest"] == result["final_transcript_digest"]
    assert result["manifest_transcript_digest"] == result["transcript_digest"]
