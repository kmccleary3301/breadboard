from __future__ import annotations

import hashlib
import json
import queue
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import ArtifactStore
from breadboard.product.runtime.events import rebuild
from breadboard.product.runtime.session import Session


class FixedClock:
    def __init__(self) -> None:
        self.tick = 0

    def now(self) -> str:
        self.tick += 1
        return f"2026-07-16T00:00:{self.tick:02d}Z"


class FailingSink:
    def append(self, event: object) -> None:
        if getattr(event, "sequence") > 1:
            raise OSError("durable sink unavailable")


def lock() -> EffectiveHarnessLock:
    return EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64})


def validate_schema(name: str, record: dict) -> None:
    path = Path(__file__).parents[3] / "contracts" / "public" / "schemas" / name
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(record)


def test_event_sequence_rebuilds_identical_read_model() -> None:
    session = Session.start(lock(), "ship it", session_id="s-1", clock=FixedClock())
    session.input("first")
    session.request_approval("approval-1", "shell")
    session.resolve_approval("approval-1", "once")
    expected = session.pause("operator").as_dict()

    restored = Session.restore(session.events, clock=FixedClock())
    assert (
        restored.read_model.as_dict() == expected == rebuild(session.events).as_dict()
    )
    with pytest.raises(TypeError):
        session.events[0].payload["task_hash"] = "changed"


def test_cancel_resume_and_terminal_error_paths_are_explicit() -> None:
    resumed = Session.start(lock(), "task", session_id="resume", clock=FixedClock())
    resumed.pause("handoff")
    resumed.resume()
    resumed.cancel("operator")
    assert resumed.read_model.status == "canceled"
    assert resumed.read_model.terminal_outcome == {"outcome": "canceled", "reason": "operator"}
    with pytest.raises(RuntimeError, match="cannot accept input"):
        resumed.input("late")

    failed = Session.start(lock(), "task", session_id="failed", clock=FixedClock())
    failed.fail("provider_unavailable", "provider returned 503")
    assert failed.read_model.terminal_outcome == {"outcome": "failed", "error": "provider_unavailable", "detail": "provider returned 503"}
    validate_schema("bb.session.v1.schema.json", failed.read_model.as_dict())


def test_start_rejects_mutable_config_instead_of_effective_lock() -> None:
    with pytest.raises(TypeError, match="EffectiveHarnessLock"):
        Session.start({"graph_hash": "sha256:" + "a" * 64}, "task")  # type: ignore[arg-type]


def test_sink_failure_does_not_advance_live_state() -> None:
    session = Session.start(lock(), "task", session_id="durable", clock=FixedClock(), sink=FailingSink())
    before = session.read_model
    with pytest.raises(OSError, match="durable sink unavailable"):
        session.input("must persist")
    assert session.events[0].kind == "session.started"
    assert session.read_model == before


def test_artifacts_are_content_addressed_verified_and_path_free(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "owner")
    first = store.put(b"proof", media_type="text/plain")
    assert store.put(b"proof", media_type="text/plain") == first
    assert store.read(first) == b"proof"
    manifest = store.manifest("s-1", {"proof.txt": first})
    validate_schema("bb.artifact_manifest.v1.schema.json", manifest)
    assert str(tmp_path) not in json.dumps(manifest)


@pytest.mark.asyncio
async def test_cli_bridge_adapts_lifecycle_behind_product_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentic_coder_prototype.api.cli_bridge import service as service_module
    from agentic_coder_prototype.api.cli_bridge.models import (
        SessionCommandRequest,
        SessionCreateRequest,
        SessionInputRequest,
        SessionStatus,
    )
    RuntimeRunner = service_module.SessionRunner
    monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "records"))

    class Runner:
        def __init__(self, session, registry, request) -> None:
            self.session, self.registry = session, registry
            self.request = request
            self.config = {"model": {"name": self.request.overrides["model.name"]}}
            self.inputs: list[str] = []

        def prepare_runtime_config(self) -> dict:
            return self.config

        def current_runtime_config(self) -> dict:
            return self.config
        async def start(self) -> None:
            await self.registry.update_status(self.session.session_id, SessionStatus.RUNNING)

        async def stop(self) -> None:
            return None

        async def enqueue_input(self, content: str, attachments: list[str]) -> str:
            self.inputs.append(content)
            return "sanitized"

        async def handle_command(self, command: str, payload: dict | None) -> dict:
            if command in {"permission_decision", "respond_permission", "permission_response"}:
                return {"status": "ok", "request_id": "permission-1", "decision": "once"}
            if command == "set_model":
                self.config = {"providers": {"default_model": payload["model"]}}
            return {"status": "ok"}

    monkeypatch.setattr(service_module, "SessionRunner", Runner)
    service = service_module.SessionService()
    request = SessionCreateRequest(
        config_path="fixture.yaml", task="ship", overrides={"model.name": "runtime-model"}
    )
    created = await service.create_session(request)
    record = await service.ensure_session(created.session_id)
    product_session = record.product_session
    assert record.product_artifacts == {}
    assert json.loads((tmp_path / "records" / created.session_id / "session_events.jsonl").read_text().splitlines()[0])["kind"] == "session.started"
    expected_graph = service_module.compile_effective_config_graph(
        graph_id=f"session:{created.session_id}",
        layers=[{"layer_id": "runtime:effective", "source_kind": "runtime", "scope": "session",
                 "precedence": 0, "values": {"model": {"name": "runtime-model"}},
                 "source_ref": "fixture.yaml"}],
    )
    assert product_session.read_model.effective_lock_hash == expected_graph["graph_hash"]
    artifact = ArtifactStore(tmp_path / "adapter").put(
        b"attachment", media_type="text/plain"
    )
    record.product_artifacts = {"attachment-1": artifact}

    await service.send_input(
        created.session_id,
        SessionInputRequest(content="next", attachments=["attachment-1"]),
    )
    assert dict(product_session.events[1].payload["attachments"][0]) == artifact.as_dict()
    assert product_session.events[1].payload["content_hash"] == "sha256:" + hashlib.sha256(b"sanitized").hexdigest()
    runtime_adapter = RuntimeRunner(session=record, registry=service.registry, request=request)
    runtime_adapter._update_pending_permissions(
        "permission_request",
        {"request_id": "permission-1", "tool": "write"},
    )
    runtime_adapter._permission_queue = queue.Queue()
    canonical = await runtime_adapter.handle_command(
        "permission_response",
        {"permissionId": "permission-1", "items": {"first": "once", "second": "always"}},
    )
    assert canonical["request_id"] == "permission-1"
    assert canonical["decision"] == "once"
    assert product_session.read_model.status == "awaiting_approval"
    with pytest.raises(service_module.HTTPException, match="awaiting_approval"):
        await service.send_input(
            created.session_id, SessionInputRequest(content="must not queue")
        )
    assert record.runner.inputs == ["next"]
    await service.execute_command(
        created.session_id,
        SessionCommandRequest(
            command="permission_decision",
            payload={"request_id": "permission-1", "decision": "allow-once"},
        ),
    )
    assert product_session.read_model.event_count == 4
    assert record.metadata["session_contract"] == product_session.read_model.as_dict()
    previous_lock = product_session.read_model.effective_lock_hash
    await service.execute_command(
        created.session_id,
        SessionCommandRequest(command="set_model", payload={"model": "changed-model"}),
    )
    assert product_session.events[-1].kind == "session.reconfigured"
    assert product_session.read_model.effective_lock_hash != previous_lock

    await service.stop_session(created.session_id)
    assert product_session.read_model.status == "canceled"
