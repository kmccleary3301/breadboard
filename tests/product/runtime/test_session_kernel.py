from __future__ import annotations
import asyncio, hashlib, json, os, stat, threading, multiprocessing, pytest
from pathlib import Path; from types import SimpleNamespace; from typing import Any
from jsonschema import Draft202012Validator
from fastapi import HTTPException
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime.events import KernelEvent, SessionView, rebuild
from breadboard.product.runtime.ports import JsonlEventSink
from breadboard.product.runtime import ports as runtime_ports
from breadboard.product.runtime.session import Session
from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.api.cli_bridge.models import SessionCommandRequest, SessionCreateRequest, SessionInputRequest
from agentic_coder_prototype.api.cli_bridge.events import EventType
from agentic_coder_prototype.api.cli_bridge.models import SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner, _canonical_permission_resolution
from agentic_coder_prototype.permissions import load_permission_rules, upsert_permission_rule, rules_store
from agentic_coder_prototype.permissions.broker import PermissionBroker
from agentic_coder_prototype.permissions.rules_store import RULES_REL_PATH, _locked_rules
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.session_runner import MAX_ATTACHMENT_BYTES
HASH, OTHER_HASH, PORTS, ARTIFACTS = "sha256:" + "a" * 64, "sha256:" + "b" * 64, "breadboard.product.runtime.ports.os.", "breadboard.product.runtime.artifacts.os."
def _lock(digest: str = HASH) -> EffectiveHarnessLock: return EffectiveHarnessLock._from_record({"graph_hash": digest})
_PAYLOADS = {
    "session.started": {"effective_lock_hash": HASH, "task_hash": HASH}, "input.accepted": {"content_hash": HASH, "attachments": []},
    "approval.requested": {"request_id": "r", "operation": "write"}, "approval.resolved": {"request_id": "r", "decision": "allow"},
    "session.reconfigured": {"effective_lock_hash": OTHER_HASH, "reason": ""}, "session.paused": {"reason": ""}, "session.resumed": {},
    "session.completed": {"outcome": "completed", "summary": ""}, "session.failed": {"outcome": "failed", "error": "error", "detail": "detail"}, "session.canceled": {"outcome": "canceled", "reason": ""}}
def _event(sequence: int = 1, kind: str = "session.started", payload: dict[str, Any] | None = None, session_id: str = "s-1") -> KernelEvent: return KernelEvent.create(session_id, sequence, kind, "2026-07-16T00:00:00Z", _PAYLOADS[kind] if payload is None else payload)
def test_nested_event_payload_is_immutable_and_replayable() -> None:
    payload = {**_PAYLOADS["session.started"], "nested": [{"value": 1}]}; event = _event(payload=payload); payload["nested"][0]["value"] = 2; serialized = event.as_dict(); assert serialized["payload"]["nested"] == [{"value": 1}]
    with pytest.raises(TypeError): event.payload["nested"][0]["value"] = 3
    assert rebuild([KernelEvent(**serialized)]).as_dict() == rebuild([event]).as_dict()
@pytest.mark.parametrize("patch", [{"schema_version": "bb.session_event.v2"}, {"session_id": 1}, {"sequence": True}, {"kind": 1}, {"kind": "test"}, {"occurred_at": []}, {"payload": {1: "coerced"}}, {"payload": {"effective_lock_hash": "sha256:" + "A" * 64, "task_hash": HASH}}, {"payload": {"effective_lock_hash": HASH + "\n", "task_hash": HASH}}, {"payload": {"effective_lock_hash": HASH, "task_hash": HASH + "\n"}}])
def test_malformed_persisted_events_cannot_rebuild(patch: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)): rebuild([KernelEvent(**{**_event().as_dict(), **patch})])  # type: ignore[arg-type]
@pytest.mark.parametrize(("kind", "payload"), [("input.accepted", {"content_hash": HASH, "attachments": [{"digest": HASH, "size_bytes": True, "media_type": "text/plain"}]}), ("approval.requested", {"request_id": "", "operation": "write"}), ("approval.resolved", {"request_id": "r", "decision": "maybe"}), ("session.reconfigured", {"effective_lock_hash": HASH, "reason": 1}), ("session.paused", {"reason": None}), ("session.resumed", {"extra": True}), ("session.completed", {"outcome": "completed"}), ("session.failed", {"outcome": "completed", "error": "x", "detail": "y"}), ("session.canceled", {"outcome": "canceled", "reason": 1})])
def test_event_specific_payloads_are_validated_on_reconstitution(kind: str, payload: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)): KernelEvent("s-1", 2, kind, "now", payload)
def test_session_view_deeply_freezes_terminal_outcome() -> None:
    outcome = {"outcome": "failed", "error": "code", "detail": "detail", "nested": {"code": 1}}; view = SessionView("s-1", "failed", HASH, HASH, 2, terminal_outcome=outcome); outcome["nested"]["code"] = 2
    with pytest.raises(TypeError): view.terminal_outcome["nested"]["code"] = 3  # type: ignore[index]
    assert view.as_dict()["terminal_outcome"]["nested"]["code"] == 1
    Changing = type("Changing", (dict,), {"items": lambda self: {"outcome": "failed", "summary": 7}.items()})
    with pytest.raises(ValueError): SessionView("s-1", "completed", HASH, HASH, 2, terminal_outcome=Changing(outcome="completed", summary="ok"))
_ACTIONS = {"input": lambda s: s.input("content"), "request": lambda s: s.request_approval("r", "write"), "resolve": lambda s: s.resolve_approval("r", "allow"), "reconfigure": lambda s: s.reconfigure(_lock(OTHER_HASH), ""), "pause": lambda s: s.pause(""), "resume": lambda s: s.resume(), "cancel": lambda s: s.cancel(""), "complete": lambda s: s.complete(""), "fail": lambda s: s.fail("error", "detail")}
_FACADE_ALLOWED = {"input": {"running"}, "request": {"running"}, "resolve": {"awaiting_approval"}, "reconfigure": {"running", "awaiting_approval", "paused"}, "pause": {"running"}, "resume": {"paused"}, "cancel": {"running", "awaiting_approval", "paused"}, "complete": {"running"}, "fail": {"running", "awaiting_approval", "paused"}}
def _session(status: str) -> Session:
    session = Session.start(_lock(), "task"); {"awaiting_approval": lambda: session.request_approval("r", "write"), "paused": lambda: session.pause(""), "completed": lambda: session.complete(""), "failed": lambda: session.fail("error", "detail"), "canceled": lambda: session.cancel("")}[status]() if status != "running" else None; return session
@pytest.mark.parametrize("status", ["running", "awaiting_approval", "paused", "completed", "failed", "canceled"])
@pytest.mark.parametrize("action", _ACTIONS)
def test_facade_transition_table_is_exhaustive(status: str, action: str) -> None:
    session = _session(status); before = session.events, session.read_model
    if status not in _FACADE_ALLOWED[action]:
        with pytest.raises(RuntimeError): _ACTIONS[action](session)
        assert (session.events, session.read_model) == before
        return
    view = _ACTIONS[action](session); expected = status if action in {"input", "reconfigure"} else {"request": "awaiting_approval", "resolve": "running", "pause": "paused", "resume": "running", "cancel": "canceled", "complete": "completed", "fail": "failed"}[action]
    assert view == session.read_model == rebuild(session.events) and view.status == expected and view.event_count == before[1].event_count + 1
    if expected in {"completed", "failed", "canceled"}: assert view.pending_approval is None
@pytest.mark.parametrize("status", ["awaiting_approval", "paused"])
def test_reconfigure_preserves_state_while_replay_updates_hash(status: str) -> None:
    session = _session(status); before = session.read_model; session.reconfigure(_lock(OTHER_HASH), ""); assert (session.read_model.status, session.read_model.pending_approval, session.read_model.task_hash, session.read_model.effective_lock_hash) == (status, before.pending_approval, before.task_hash, OTHER_HASH) and rebuild(session.events) == session.read_model
@pytest.mark.parametrize("events", [(_event(), _event(3, "input.accepted")), (_event(), _event(2, "input.accepted"), _event(2, "input.accepted")), (_event(), _event(2, "input.accepted", session_id="s-2")), (_event(), _event(2)), (_event(), _event(2, "session.completed"), _event(3, "input.accepted")), (_event(), _event(2, "approval.requested"), _event(3, "approval.resolved", {"request_id": "other", "decision": "allow"}))])
def test_replay_rejects_noncanonical_streams(events: tuple[KernelEvent, ...]) -> None:
    with pytest.raises(ValueError): rebuild(events)
class _FalsySink(list[KernelEvent]):
    def __bool__(self) -> bool: return False
_FalsyClock = type("_FalsyClock", (), {"__bool__": lambda self: False, "now": lambda self: "2026-07-16T00:00:00Z"})
_FalsyIds = type("_FalsyIds", (), {"__bool__": lambda self: False, "new_id": lambda self: "falsy-id"})
def test_falsy_ports_are_preserved_and_empty_explicit_id_is_rejected() -> None:
    sink = _FalsySink(); session = Session.start(_lock(), "task", clock=_FalsyClock(), ids=_FalsyIds(), sink=sink); session.input("content"); assert [(event.session_id, event.occurred_at) for event in sink] == [("falsy-id", "2026-07-16T00:00:00Z")] * 2
    with pytest.raises(ValueError, match="identity fields"): Session.start(_lock(), "task", session_id="")
def test_failed_session_append_is_unchanged_and_retry_is_exactly_once() -> None:
    sink, attempts = _FalsySink(), []
    def append(event: KernelEvent) -> None: attempts.append(event); (_ for _ in ()).throw(OSError("append failed")) if len(attempts) == 2 else list.append(sink, event)
    sink.append = append  # type: ignore[method-assign]
    session = Session.start(_lock(), "task", clock=_FalsyClock(), sink=sink); before = session.events, session.read_model
    with pytest.raises(OSError, match="append failed"): session.input("content")
    assert (session.events, session.read_model) == before; session.input("content"); assert attempts[-2] == attempts[-1] and list(session.events) == sink and [event.sequence for event in sink] == [1, 2]
class _ReentrantSink(_FalsySink):
    def __init__(self) -> None: super().__init__(); self.session: Session | None = None; self.observed: list[tuple[list[int], int]] = []
    def append(self, event: KernelEvent) -> None:
        if event.sequence == 2:
            assert self.session is not None; self.observed.append(([row.sequence for row in self.session.events], self.session.read_model.event_count))
            with pytest.raises(RuntimeError, match="append is in progress"): self.session.input("nested")
        list.append(self, event)
def test_reentrant_sink_can_observe_but_cannot_mutate_session() -> None:
    sink = _ReentrantSink(); session = Session.start(_lock(), "task", sink=sink); sink.session = session; session.input("outer"); assert sink.observed == [([1], 1)] and [event.sequence for event in sink] == [1, 2] and rebuild(sink) == rebuild(session.events) == session.read_model
@pytest.mark.parametrize("nested", [lambda session: session.input("nested"), lambda session: session.cancel("nested"), lambda session: session.complete("nested"), lambda session: session.fail("nested", "nested")], ids=["input", "cancel", "complete", "fail"])
def test_attachment_materialization_can_observe_but_not_mutate(nested: Any) -> None:
    session = Session.start(_lock(), "task"); before = session.events, session.read_model; observed = []
    def attachments() -> Any:
        observed.append((session.events, session.read_model))
        with pytest.raises(RuntimeError, match="append is in progress"): nested(session)
        assert (session.events, session.read_model) == before; yield ArtifactRef(HASH, 0, "text/plain")
    session.input("outer", attachments()); assert observed == [before] and len(session.events) == 2 and session.read_model.status == "running" and rebuild(session.events) == session.read_model
class _GatedSink:
    def __init__(self) -> None: self.events: list[KernelEvent] = []; self.entered, self.release = threading.Event(), threading.Event(); self.first: int | None = None
    def append(self, event: KernelEvent) -> None: (setattr(self, "first", threading.get_ident()), self.entered.set(), self.release.wait(1) or (_ for _ in ()).throw(AssertionError("release timeout"))) if event.sequence == 2 and not self.entered.is_set() else None; self.events.append(event)
def test_concurrent_transitions_are_contiguous_and_replayable() -> None:
    sink, barrier = _GatedSink(), threading.Barrier(3); session = Session.start(_lock(), "task", sink=sink)
    def transition(content: str) -> None: barrier.wait(); session.input(content)
    threads = [threading.Thread(target=transition, args=(content,)) for content in ("one", "two")]
    for thread in threads: thread.start()
    barrier.wait(); assert sink.entered.wait(1); next(thread for thread in threads if thread.ident != sink.first).join(.1); sink.release.set()
    for thread in threads: thread.join(1); assert not thread.is_alive()
    assert [event.sequence for event in sink.events] == [1, 2, 3] and rebuild(sink.events) == rebuild(session.events) == session.read_model
class _FaultyFile:
    def __init__(self, stream: Any, fault: str, fired: list[bool]) -> None: self.stream, self.fault, self.fired = stream, fault, fired
    def __getattr__(self, name: str) -> Any: return getattr(self.stream, name)
    def write(self, data: bytes) -> int:
        if self.fault == "write" and not self.fired[0]: self.fired[0] = True; return 0
        return self.stream.write(data)
    def close(self) -> None: self.stream.close(); (_ for _ in ()).throw(OSError("close failed")) if self.fault == "close" and not self.fired[0] and not self.fired.__setitem__(0, True) else None
def _crash_during_append(path: str) -> None:
    real_open = Path.open
    class CrashFile(_FaultyFile):
        def write(self, data: bytes) -> int: self.stream.write(data[:len(data) // 2]); self.stream.flush(); os.fsync(self.stream.fileno()); os._exit(17)
    Path.open = lambda target, *args, **kwargs: CrashFile(real_open(target, *args, **kwargs), "", []) if Path(target).resolve() == Path(path).resolve() and args and "a" in args[0] else real_open(target, *args, **kwargs)  # type: ignore[method-assign]
    JsonlEventSink(path).append(_event(2, "input.accepted"))
def test_sink_recovers_process_crash_partial_tail(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"; sink = JsonlEventSink(path); sink.append(_event()); process = multiprocessing.get_context("spawn").Process(target=_crash_during_append, args=(str(path),)); process.start(); process.join(10); assert process.exitcode == 17
    recovered = JsonlEventSink(path); assert [json.loads(row)["sequence"] for row in path.read_text().splitlines()] == [1]; recovered.append(_event(2, "input.accepted")); assert [json.loads(row)["sequence"] for row in path.read_text().splitlines()] == [1, 2]
@pytest.mark.parametrize("fault", ["write", "fsync", "close"])
def test_sink_write_sync_and_close_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str) -> None:
    path, fired, real_open, real_fsync = tmp_path / "events.jsonl", [False], Path.open, os.fsync; monkeypatch.setattr(Path, "open", lambda path, *a, **kw: _FaultyFile(real_open(path, *a, **kw), fault, fired))
    def fsync(fd: int) -> None: (_ for _ in ()).throw(OSError("fsync failed")) if fault == "fsync" and not fired[0] and not fired.__setitem__(0, True) else real_fsync(fd)
    monkeypatch.setattr(PORTS + "fsync", fsync); sink = JsonlEventSink(path)
    if fault == "close": sink.append(_event())
    else:
        with pytest.raises(OSError): sink.append(_event())
        sink.append(_event())
    monkeypatch.setattr(Path, "open", real_open); assert [json.loads(row)["sequence"] for row in path.read_text().splitlines()] == [1]
def test_failed_append_preserves_nonempty_jsonl_prefix_and_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path, fired, real_open = tmp_path / "events.jsonl", [False], Path.open; sink = JsonlEventSink(path); sink.append(_event()); prefix = path.read_bytes(); monkeypatch.setattr(Path, "open", lambda path, *a, **kw: _FaultyFile(real_open(path, *a, **kw), "write", fired))
    with pytest.raises(OSError, match="short event sink write"): sink.append(_event(2, "input.accepted"))
    monkeypatch.setattr(Path, "open", real_open); assert path.read_bytes() == prefix; sink.append(_event(2, "input.accepted")); assert [json.loads(row)["sequence"] for row in path.read_text().splitlines()] == [1, 2]
def test_nested_parent_sync_failure_is_not_acknowledged_and_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path, failed, opened, real_fsync, real_open = tmp_path / "nested" / "deep" / "events.jsonl", [False], [], os.fsync, os.open
    def fsync(fd: int) -> None: (_ for _ in ()).throw(OSError("parent fsync failed")) if fd == 999 and not failed[0] and not failed.__setitem__(0, True) else real_fsync(fd) if fd != 999 else None
    monkeypatch.setattr(PORTS + "name", "posix"); monkeypatch.setattr(PORTS + "open", lambda path, flags, *args: opened.append(Path(path)) or (999 if flags == os.O_RDONLY and Path(path) == tmp_path / "nested" else real_open(path, flags, *args))); monkeypatch.setattr(PORTS + "close", lambda _: None); monkeypatch.setattr(PORTS + "fsync", fsync)
    with pytest.raises(OSError, match="parent fsync failed"): JsonlEventSink(path)
    sink = JsonlEventSink(path); sink.append(_event()); assert tmp_path / "nested" in opened and json.loads(path.read_text())["sequence"] == 1; race, entered, release, done, original_sync = tmp_path / "race" / "events.jsonl", threading.Event(), threading.Event(), threading.Event(), JsonlEventSink._sync_parent
    def gated(self, parent): entered.set(); assert release.wait(1); original_sync(self, parent)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(JsonlEventSink, "_sync_parent", gated); first = threading.Thread(target=JsonlEventSink, args=(race,)); first.start(); assert entered.wait(1); second = threading.Thread(target=lambda: (JsonlEventSink(race), done.set())); second.start(); assert not done.wait(.05); release.set(); first.join(); second.join(); assert done.is_set()
def test_sink_poisoned_when_rollback_sync_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(PORTS + "fsync", lambda _: (_ for _ in ()).throw(OSError("fsync failed"))); sink = JsonlEventSink(tmp_path / "events.jsonl")
    with pytest.raises(OSError, match="fsync failed"): sink.append(_event())
    with pytest.raises(RuntimeError, match="poisoned"): sink.append(_event())
def test_same_path_sink_failure_cannot_rollback_overlapping_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path, real_open = (tmp_path / "events.jsonl").resolve(), Path.open; staging = next(candidate for index in range(4096) if hash(candidate := (tmp_path / f"staging-{index}.jsonl").resolve()) % 256 != hash(path) % 256); relocated = JsonlEventSink(staging); relocated.append(_event()); staging.replace(path); relocated.path = path; entered, competing, succeeded, errors = threading.Event(), threading.Event(), threading.Event(), {}
    class OverlapFile:
        def __init__(self, stream: Any) -> None: self.stream = stream
        def __getattr__(self, name: str) -> Any: return getattr(self.stream, name)
        def write(self, data: bytes) -> int:
            if threading.current_thread().name == "success": entered.set(); competing.wait(.2); return self.stream.write(data)
            competing.set(); assert succeeded.wait(1); return 0
    def opened(target: Path, *args: Any, **kwargs: Any) -> Any: stream = real_open(target, *args, **kwargs); return OverlapFile(stream) if threading.current_thread().name in {"success", "fault"} else stream
    monkeypatch.setattr(Path, "open", opened)
    def append(name: str, sink: JsonlEventSink, event: KernelEvent) -> None:
        try: sink.append(event)
        except BaseException as error: errors[name] = error
        finally: succeeded.set() if name == "success" else None
    success = threading.Thread(name="success", target=append, args=("success", JsonlEventSink(path), _event(2, "input.accepted"))); success.start(); assert entered.wait(1); fault = threading.Thread(name="fault", target=append, args=("fault", relocated, _event(3, "input.accepted"))); fault.start()
    for thread in (success, fault): thread.join(2); assert not thread.is_alive()
    assert "success" not in errors and isinstance(errors.get("fault"), OSError) and [json.loads(row)["sequence"] for row in path.read_text().splitlines()] == [1, 2]
@pytest.mark.parametrize("values", [("bad", 0, "text/plain"), (HASH + "\n", 0, "text/plain"), (HASH, -1, "text/plain"), (HASH, 1.5, "text/plain"), (HASH, 0, "")])
def test_invalid_artifact_refs(values: tuple[object, ...]) -> None:
    with pytest.raises(ValueError): ArtifactRef(*values)  # type: ignore[arg-type]
def _rollback_artifact(root: str, entered: Any, release: Any) -> None:
    store, created = ArtifactStore(root), set()
    with store.transaction():
        ref = store.put(b"proof", created=created); entered.set(); (_ for _ in ()).throw(RuntimeError("release timeout")) if not release.wait(5) else None; store.discard(ref)
def _publish_artifact(root: str, entered: Any, attempting: Any, returned: Any, result: Any) -> None:
    if not entered.wait(5): raise RuntimeError("owner timeout")
    attempting.set(); ref = ArtifactStore(root).put(b"proof"); result.put(ref.as_dict()); returned.set()
def _read_artifact(root: str, ref: ArtifactRef, entered: Any, attempting: Any, returned: Any, result: Any) -> None:
    if not entered.wait(5): raise RuntimeError("owner timeout")
    attempting.set()
    try: ArtifactStore(root).read(ref); outcome = "read"
    except FileNotFoundError: outcome = "missing"
    result.put(outcome); returned.set()
def test_cross_process_artifact_publication_survives_failed_owner(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn"); entered, release, attempting, returned, result = context.Event(), context.Event(), context.Event(), context.Event(), context.Queue()
    owner = context.Process(target=_rollback_artifact, args=(str(tmp_path), entered, release)); publisher = context.Process(target=_publish_artifact, args=(str(tmp_path), entered, attempting, returned, result)); owner.start(); publisher.start()
    try: assert entered.wait(5) and attempting.wait(5) and not returned.wait(.2)
    finally: release.set(); [(process.join(5), process.terminate() if process.is_alive() else None, process.join()) for process in (owner, publisher)]
    assert all(process.exitcode == 0 for process in (owner, publisher)) and returned.is_set()
    ref = ArtifactRef(**result.get(timeout=1)); assert ArtifactStore(tmp_path).read(ref) == b"proof"
def test_cross_process_read_cannot_observe_provisional_artifact(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn"); entered, release, attempting, returned, result = context.Event(), context.Event(), context.Event(), context.Event(), context.Queue()
    ref = ArtifactRef("sha256:" + hashlib.sha256(b"proof").hexdigest(), 5, "application/octet-stream")
    owner = context.Process(target=_rollback_artifact, args=(str(tmp_path), entered, release)); reader = context.Process(target=_read_artifact, args=(str(tmp_path), ref, entered, attempting, returned, result)); owner.start(); reader.start()
    try: assert entered.wait(5) and attempting.wait(5) and not returned.wait(.2)
    finally: release.set(); [(process.join(5), process.terminate() if process.is_alive() else None, process.join()) for process in (owner, reader)]
    assert all(process.exitcode == 0 for process in (owner, reader)) and returned.is_set() and result.get(timeout=1) == "missing"
def test_concurrent_identical_artifact_puts_share_verified_ref_and_clean_temps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store, refs, errors = ArtifactStore(tmp_path), [], []
    def put() -> None:
        try: refs.append(store.put(b"proof"))
        except BaseException as error: errors.append(error)
    threads = [threading.Thread(target=put) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(1); assert not thread.is_alive()
    assert not errors and refs[0] == refs[1] and store.read(refs[0]) == b"proof" and not list(tmp_path.rglob("*.tmp"))
@pytest.mark.parametrize("fail_at", [1, 2])
def test_artifact_put_sync_fault_is_unacknowledged_and_retry_is_readable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_at: int) -> None:
    store, calls, real_fsync = ArtifactStore(tmp_path), [0], os.fsync
    def fsync(fd: int) -> None: calls.__setitem__(0, calls[0] + 1); (_ for _ in ()).throw(OSError("sync failed")) if calls[0] == fail_at else real_fsync(fd)
    monkeypatch.setattr(ARTIFACTS + "fsync", fsync)
    with pytest.raises(OSError, match="sync failed"): store.put(b"proof")
    files = [path for path in tmp_path.rglob("*") if path.is_file() and "sha256" in path.parts]; assert [path.read_bytes() for path in files] in ([], [b"proof"]); ref = store.put(b"proof"); assert store.read(ref) == b"proof" and calls[0] >= 6 and not list(tmp_path.rglob("*.tmp"))
def test_artifact_read_validates_content_and_manifest_parent_is_synced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace, synced = tmp_path / "workspace", []; root = workspace / ".breadboard" / "artifacts"; store = ArtifactStore(root); monkeypatch.setattr("breadboard.product.runtime.artifacts._sync_directory", lambda path: synced.append(path)); ref = store.put(b"proof"); assert synced[-1] == workspace; synced.clear(); store.materialize(ref, root / "manifests" / "manifest.json"); assert synced == [root / "manifests", root]; next(root.rglob(ref.digest[7:])).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="verification failed"): store.read(ref)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_json_artifacts_reject_nonfinite_numbers_before_write(tmp_path: Path, value: float) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError): store.put_json({"value": value})
    assert not list(tmp_path.rglob("*"))
@pytest.mark.parametrize("session_id", [True, 42, ""])
def test_manifest_rejects_schema_invalid_session_ids(tmp_path: Path, session_id: object) -> None:
    with pytest.raises(ValueError, match="session_id"): ArtifactStore(tmp_path).manifest(session_id, {})  # type: ignore[arg-type]
@pytest.mark.parametrize("name", [".", "..", "CON", "con.txt", "a:b", "trail.", "trail ", " lead", "a/b", "a\\b", "nul\x00", "café", "summary.json\n", "a" * 256])
def test_manifest_runtime_and_schema_share_portable_name_policy(tmp_path: Path, name: str) -> None:
    schema_path = Path(__file__).resolve().parents[3] / "contracts/public/schemas/bb.artifact_manifest.v1.schema.json"; validator = Draft202012Validator(json.loads(schema_path.read_text())); store = ArtifactStore(tmp_path); ref = store.put(b"proof"); valid = store.manifest("s-1", {"summary.json": ref}); validator.validate(valid)
    for invalid in ({**valid, "manifest_id": valid["manifest_id"] + "\n"}, {**valid, "artifacts": [{**valid["artifacts"][0], "digest": HASH + "\n"}]}): assert list(validator.iter_errors(invalid))
    with pytest.raises(ValueError, match="portable basename"): store.manifest("s-1", {name: ref})
    invalid = json.loads(json.dumps(valid)); invalid["artifacts"][0]["name"] = name; assert list(validator.iter_errors(invalid))
def test_public_session_schema_matches_projection_invariants() -> None:
    validator = Draft202012Validator(json.loads((Path(__file__).resolve().parents[3] / "contracts/public/schemas/bb.session.v1.schema.json").read_text())); valid = _session("running").read_model.as_dict(); validator.validate(valid)
    for patch in ({"effective_lock_hash": HASH + "\n"}, {"task_hash": valid["task_hash"] + "\n"}, {"pending_approval": "r"}, {"status": "awaiting_approval"}, {"status": "completed", "event_count": 1, "terminal_outcome": {"outcome": "completed", "summary": ""}}, {"status": "completed", "event_count": 2}, {"status": "completed", "event_count": 2, "terminal_outcome": {"outcome": "failed", "error": "x", "detail": "y"}}): assert list(validator.iter_errors({**valid, **patch})), patch
_EVENT_KINDS = {"input": "input.accepted", "request": "approval.requested", "resolve": "approval.resolved", "reconfigure": "session.reconfigured", "pause": "session.paused", "resume": "session.resumed", "cancel": "session.canceled", "complete": "session.completed", "fail": "session.failed"}
_REPLAY_DENIED = [(status, action) for status in ("running", "awaiting_approval", "paused", "completed", "failed", "canceled") for action in _ACTIONS if status not in _FACADE_ALLOWED[action]]
@pytest.mark.parametrize(("status", "action"), _REPLAY_DENIED)
def test_replay_transition_table_rejects_every_disallowed_pair(status: str, action: str) -> None:
    events = list(_session(status).events); events.append(_event(len(events) + 1, _EVENT_KINDS[action]))
    with pytest.raises(ValueError): rebuild(events)
CONFIG = "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml"
RUNNER = "agentic_coder_prototype.api.cli_bridge.session_runner.SessionRunner.start"
SERVICE = "agentic_coder_prototype.api.cli_bridge.service."
class _Failing:
    def append(self, _event) -> None: raise OSError("sink unavailable")  # type: ignore[no-untyped-def]
    def put_nowait(self, _item) -> None: raise RuntimeError("broker unavailable")  # type: ignore[no-untyped-def]
class _Upload:
    filename, content_type, data = "proof.txt", "text/plain", b"proof"
    async def read(self, size: int = -1) -> bytes: data = self.data if size < 0 else self.data[:size]; self.data = self.data[len(data):]; return data
async def _stop(record) -> None:  # type: ignore[no-untyped-def]
    if record.dispatcher_task and not record.dispatcher_task.done(): await record.event_queue.put(None); await record.dispatcher_task
async def _create(monkeypatch, tmp_path, *, service=None, task="Say hi", **fields):  # type: ignore[no-untyped-def]
    async def start(_runner) -> None: return None  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER, start); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "events"))
    service = service or SessionService()
    response = await service.create_session(SessionCreateRequest(config_path=CONFIG, task=task, **fields))
    return service, response, await service.ensure_session(response.session_id)
@pytest.mark.asyncio
async def test_input_and_approval_are_durable_before_delivery(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path); sink, record.product_session._sink = record.product_session._sink, _Failing()
    with pytest.raises(OSError, match="sink unavailable"): await service.send_input(response.session_id, SessionInputRequest(content="next"))
    assert record.runner._input_queue.empty(); assert [event.kind for event in record.product_session.events] == ["session.started"]
    record.product_session._sink = sink; record.runner._rehydrate_pending_permissions("permission_request", {"request_id": "perm-1", "category": "shell"}); record.runner._permission_queue = _Failing(); persisted = []
    monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.session_runner.upsert_permission_rule", lambda *_args, **_kwargs: persisted.append(True) or True)
    request = SessionCommandRequest(command="permission_decision", payload={"request_id": "perm-1", "decision": "always", "rule": "*.sh"})
    with pytest.raises(HTTPException) as error: await service.execute_command(response.session_id, request)
    assert error.value.status_code == 409; assert [event.kind for event in record.product_session.events][-2:] == ["approval.resolved", "session.failed"]; assert record.status.value == "failed"
    assert persisted == [True] and record.metadata["permission_rules"][0]["rule"] == "*.sh"
    await service.stop_session(response.session_id); await service.stop_session(response.session_id); assert type(record.product_session).restore(record.product_session.events).read_model.status == "failed"; await _stop(record)
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_initial_durable_start_failure_has_no_published_lifecycle(monkeypatch, tmp_path, failure) -> None:
    records_root, events_root, started = tmp_path / "records", tmp_path / "events", []; entered, released = threading.Event(), threading.Event()
    async def start(runner) -> None: started.append(runner)  # type: ignore[no-untyped-def]
    def emit(*, session_id, request, output_root, **_): path = output_root / session_id / "start.json"; path.parent.mkdir(parents=True); path.write_text("emitted", encoding="utf-8"); entered.set(); assert released.wait(2); return {"start": str(path)}  # type: ignore[no-untyped-def]
    def fail_sync(_stream) -> None: raise failure("initial append failed")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER, start); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: True); monkeypatch.setattr(SERVICE + "emit_session_start_records", emit)
    monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "durable-start-failure"); monkeypatch.setattr(runtime_ports, "_sync", fail_sync)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root))
    service = SessionService(); request = SessionCreateRequest(config_path=CONFIG, task="task")
    real_write, owners = Path.write_text, [0]
    def owner_write(path, *args, **kwargs): return (_ for _ in ()).throw(OSError("owner write failed")) if path.name == ".start.owner" and (owners.__setitem__(0, owners[0] + 1) or owners[0] == 2) else real_write(path, *args, **kwargs)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(Path, "write_text", owner_write)
    with pytest.raises(OSError, match="owner write failed"): await service.create_session(request)
    assert all(not root.exists() or not any(root.iterdir()) for root in (records_root, events_root)); monkeypatch.setattr(Path, "write_text", real_write)
    pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(service.create_session(request))))
    assert await asyncio.to_thread(entered.wait, 2); assert await service.registry.get("durable-start-failure") is None
    assert not (records_root / "durable-start-failure").exists() and not (events_root / "durable-start-failure").exists()
    released.set()
    with pytest.raises(failure, match="initial append failed"): await pending
    assert await service.registry.get("durable-start-failure") is None and started == []; assert not any(records_root.iterdir()) and not any(events_root.iterdir())
@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["records", "events", "commit", "authority"])
@pytest.mark.parametrize("shared_root", [False, True])
@pytest.mark.parametrize("primitives", [False, True])
async def test_start_publication_boundaries_are_invisible_and_retryable(monkeypatch, tmp_path, boundary, shared_root, primitives) -> None:
    records_root = tmp_path / "records"; events_root = records_root if shared_root else tmp_path / "events"; entered, released = threading.Event(), threading.Event()
    async def start(_runner) -> None: return None  # type: ignore[no-untyped-def]
    monkeypatch.setattr(RUNNER, start); monkeypatch.setattr(SERVICE + "uuid.uuid4", lambda: "publication-failure"); monkeypatch.setattr(SERVICE + "primitive_emission_enabled", lambda: primitives)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(records_root)); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(events_root)); service, armed = SessionService(), True
    def failpoint(name) -> None:  # type: ignore[no-untyped-def]
        nonlocal armed
        if armed and name == boundary: entered.set(); assert released.wait(2); armed = False; raise OSError(f"{name} publication failed")
    monkeypatch.setattr(service, "_publication_boundary", failpoint); request = SessionCreateRequest(config_path=CONFIG, task="task")
    pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(service.create_session(request)))); assert await asyncio.to_thread(entered.wait, 2); session_id = "publication-failure"; authority = (records_root if primitives else events_root) / session_id; assert session_id not in service.registry._records
    active_paths = [path for path in (records_root / f".{session_id}.records.starting", events_root / f".{session_id}.events.starting", authority) if path.exists()]; SessionService(); assert active_paths and all(path.exists() for path in active_paths)
    if boundary == "authority":
        hidden_event = events_root / f".{session_id}.events.starting" / "session_events.jsonl"; assert (authority / ".start.committed").is_file() and ((events_root / session_id / "session_events.jsonl").is_file() or hidden_event.is_file())
    else: assert not (records_root / session_id).exists() and not (events_root / session_id).exists()
    released.set()
    with pytest.raises(OSError, match=f"{boundary} publication failed"): await pending
    if boundary == "authority":
        SessionService(); assert (authority / ".start.committed").is_file() and (events_root / session_id / "session_events.jsonl").is_file(); return
    assert all(not root.exists() or not any(root.iterdir()) for root in {records_root, events_root})
    response = await service.create_session(request); record = await service.ensure_session(response.session_id); authority = (records_root if primitives else events_root) / response.session_id
    assert (authority / ".start.committed").is_file() and (events_root / response.session_id / "session_events.jsonl").is_file(); await _stop(record)
@pytest.mark.asyncio
async def test_attachment_manifest_survives_delete_and_unknown_ids_are_rejected(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"; service, response, record = await _create(monkeypatch, tmp_path, workspace=str(workspace))
    upload = _Upload(); upload.filename = "résumé.txt"; uploaded = await service.upload_attachments(response.session_id, [upload]); attachment_id = uploaded.attachments[0].id
    digest = record.metadata["artifact_manifest_ref"]["digest"].removeprefix("sha256:"); manifest_path = workspace / ".breadboard" / "artifacts" / "manifests" / f"{response.session_id}.{digest}.json"; manifest = json.loads(manifest_path.read_text()); assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == digest; empty = _Upload(); empty.data = b""; attachment_root = workspace / ".breadboard" / "attachments"; before = (manifest_path.read_bytes(), dict(record.product_artifacts), dict(record.metadata), {path.name for path in attachment_root.iterdir()})
    attachment_path = next((attachment_root / attachment_id).iterdir()); attachment_path.write_bytes(b"tampered")
    helper = record.runner._format_attachment_helper([attachment_id, attachment_id]); attachment_path.write_bytes(b"raced"); uri = f"attachment://{record.product_artifacts[attachment_id].digest}"
    conductor_class = OpenAIConductor.__ray_metadata__.modified_class; conductor = object.__new__(conductor_class); conductor.config, conductor.workspace = {}, str(workspace); conductor._active_session_state = SimpleNamespace(get_provider_metadata=lambda key, default=None: record.runner._active_attachment_capabilities if key == "attachment_capabilities" else default)
    read_result = conductor._exec_raw({"function": "read_file", "arguments": {"path": uri}}); denied_result = conductor._exec_raw({"function": "read_file", "arguments": {"path": "attachment://sha256:" + "0" * 64}})
    assert uri in helper and "content=" not in helper and read_result["content"] == "proof" and "not authorized" in denied_result["error"] and attachment_path.read_bytes() == b"raced" and helper.count("Attachment ") == 1
    empty_error, missing_error = await asyncio.gather(service.upload_attachments(response.session_id, [empty]), service.send_input(response.session_id, SessionInputRequest(content="use it", attachments=["missing"])), return_exceptions=True)
    assert isinstance(empty_error, HTTPException) and empty_error.status_code == 400 and isinstance(missing_error, HTTPException) and missing_error.status_code == 400 and record.runner._input_queue.empty(); assert before == (manifest_path.read_bytes(), record.product_artifacts, record.metadata, {path.name for path in attachment_root.iterdir()}) and manifest["schema_version"] == "bb.artifact_manifest.v1"
    cas_root = workspace / ".breadboard" / "artifacts" / "sha256"; cas_before = {path.relative_to(cas_root): path.read_bytes() for path in cas_root.rglob("*") if path.is_file()}
    real_put, calls = ArtifactStore.put, []; monkeypatch.setattr(ArtifactStore, "put", lambda store, *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")) if (calls.append(1) or len(calls) == 2) else real_put(store, *args, **kwargs))
    with pytest.raises(OSError, match="write failed"): await service.upload_attachments(response.session_id, [_Upload(), _Upload()])
    assert before == (manifest_path.read_bytes(), record.product_artifacts, record.metadata, {path.name for path in attachment_root.iterdir()})
    assert cas_before == {path.relative_to(cas_root): path.read_bytes() for path in cas_root.rglob("*") if path.is_file()}
    if os.name != "nt":
        outside, attachment_dir = tmp_path / "outside-helper", attachment_path.parent; outside.mkdir(); attachment_path.unlink(); attachment_dir.rmdir(); attachment_dir.symlink_to(outside, target_is_directory=True)
        assert uri in record.runner._format_attachment_helper([attachment_id]) and not list(outside.iterdir())
    monkeypatch.setattr(ArtifactStore, "put", real_put); entered, release = asyncio.Event(), asyncio.Event()
    class BlockingUpload(_Upload):
        async def read(self, size: int = -1) -> bytes: entered.set(); await release.wait(); return await super().read(size)
    upload_task = asyncio.create_task(service.upload_attachments(response.session_id, [BlockingUpload()])); await entered.wait()
    delete_task = asyncio.create_task(service.delete_session(response.session_id)); await asyncio.sleep(0); assert not delete_task.done()
    release.set(); raced_upload = await upload_task; await delete_task
    assert raced_upload.attachments and await service.registry.get(response.session_id) is None and manifest_path.is_file() and record.dispatcher_task.done()
@pytest.mark.asyncio
async def test_attachment_size_limit_is_rejected_before_durable_input(monkeypatch, tmp_path) -> None:
    service, response, record = await _create(monkeypatch, tmp_path, workspace=str(tmp_path / "workspace"))
    class Oversized(_Upload):
        async def read(self, size: int = -1) -> bytes: assert 0 < size <= MAX_ATTACHMENT_BYTES + 1; return b"x" * size
    before = (record.product_session.events, dict(record.product_artifacts), record.runner._input_queue.qsize())
    with pytest.raises(HTTPException) as upload_error: await service.upload_attachments(response.session_id, [Oversized()])
    assert upload_error.value.status_code == 413 and before == (record.product_session.events, record.product_artifacts, record.runner._input_queue.qsize())
    first = _Upload(); first.data = b"a" * (MAX_ATTACHMENT_BYTES // 2 + 1); second = _Upload(); second.data = b"b" * (MAX_ATTACHMENT_BYTES // 2 + 1)
    first_id = (await service.upload_attachments(response.session_id, [first])).attachments[0].id; second_id = (await service.upload_attachments(response.session_id, [second])).attachments[0].id; events = record.product_session.events; metadata = json.loads(json.dumps(record.metadata))
    with pytest.raises(HTTPException) as selection_error: await service.send_input(response.session_id, SessionInputRequest(content="Say hi inspect", attachments=[first_id, second_id]))
    assert selection_error.value.status_code == 400 and record.product_session.events == events and record.runner._input_queue.empty() and record.metadata == metadata; await _stop(record)
@pytest.mark.asyncio
async def test_attachment_storage_rejects_workspace_symlink_escape(monkeypatch, tmp_path) -> None:
    if os.name == "nt": pytest.skip("symlink privilege is not portable on Windows")
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"; workspace.mkdir(); outside.mkdir(); (workspace / ".breadboard").symlink_to(outside, target_is_directory=True); service, response, record = await _create(monkeypatch, tmp_path, workspace=str(workspace))
    with pytest.raises(HTTPException) as error: await service.upload_attachments(response.session_id, [_Upload()])
    assert error.value.status_code == 400 and not list(outside.iterdir())
    async def fail_stop() -> None: raise OSError("sink unavailable")
    monkeypatch.setattr(record.runner, "stop", fail_stop)
    with pytest.raises(OSError, match="sink unavailable"): await service.stop_session(response.session_id)
    assert record.dispatcher_task.done(); await service.registry.delete(response.session_id)
    (workspace / ".breadboard").unlink(); (workspace / ".breadboard").mkdir(); service, response, record = await _create(monkeypatch, tmp_path, service=service, workspace=str(workspace)); original, swapped = ArtifactStore.put, [False]
    def swap(store, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not swapped[0]: (workspace / ".breadboard").rename(workspace / ".breadboard-old"); (workspace / ".breadboard").symlink_to(outside, target_is_directory=True); swapped[0] = True
        return original(store, *args, **kwargs)
    monkeypatch.setattr(ArtifactStore, "put", swap)
    with pytest.raises(HTTPException, match="metadata path changed"): await service.upload_attachments(response.session_id, [_Upload()])
    assert not list(outside.iterdir()) and not list((workspace / ".breadboard-old" / "attachments").iterdir())
    (workspace / ".breadboard").unlink(); (workspace / ".breadboard-old").rename(workspace / ".breadboard"); await service.delete_session(response.session_id)
def _runner(session_id: str = "session") -> SessionRunner: return SessionRunner(session=SessionRecord(session_id=session_id, status=SessionStatus.RUNNING), registry=SessionRegistry(), request=SessionCreateRequest(config_path="dummy.yml", task="task", stream=False))
def _product_runner(session_id: str) -> tuple[SessionRunner, Session]: runner = _runner(session_id); session = Session.start(_lock(), "task", session_id=session_id); runner.session.product_session = session; return runner, session
async def _initialized() -> None: pass
def _upsert_process(workspace: str, pattern: str) -> None: assert upsert_permission_rule(Path(workspace), category="shell", pattern=pattern, decision="allow")
def _hold_rule_lock(workspace: str, entered, release) -> None: lock = _locked_rules(Path(workspace) / RULES_REL_PATH); lock.__enter__(); entered.set(); release.wait(10); lock.__exit__(None, None, None)  # type: ignore[no-untyped-def]
def test_pending_permissions_rehydrate_and_remain_scoped() -> None:
    runner = _runner(); runner._rehydrate_pending_permissions("permission_request", {"request_id": "session", "items": []}); task = {"kind": "permission_request", "sessionId": "task-1", "subagent_type": "general", "payload": {"request_id": "task", "items": []}}; runner._rehydrate_pending_permissions("task_event", task); assert [(item["source"], item.get("task_session_id"), item["request_id"]) for item in runner.session.metadata["pending_permissions"]] == [("session", None, "session"), ("task", "task-1", "task")]
    task.update(kind="permission_response", payload={"request_id": "task", "responses": {"default": "once"}}); runner._rehydrate_pending_permissions("task_event", task); runner._rehydrate_pending_permissions("permission_response", {"request_id": "session", "responses": {"default": "once"}}); runner._rehydrate_pending_permissions("task_event", task); assert "pending_permissions" not in runner.session.metadata
def test_attachment_reads_enter_permission_broker_without_changing_workspace_reads() -> None: uri = "attachment://sha256:" + "a" * 64; attachment = type("Call", (), {"function": "read_file", "arguments": {"path": uri}})(); workspace = type("Call", (), {"function": "read_file", "arguments": {"path": "README.md"}})(); assert PermissionBroker({"read": {"default": "ask"}}).decide(attachment) == "ask" and PermissionBroker({"read": {"default": "deny"}}).decide(attachment) == "deny" and PermissionBroker().decide(workspace) is None
@pytest.mark.asyncio
async def test_failing_approval_sink_suppresses_pending_and_bridge_event() -> None:
    runner, session = _product_runner("sink-failure"); session._sink = _Failing()
    with pytest.raises(OSError, match="sink unavailable"): await runner._emit_debug_permission_request({"request_id": "undurable"})
    assert "pending_permissions" not in runner.session.metadata; assert runner.session.event_queue.empty()
@pytest.mark.asyncio
async def test_durable_rule_remains_authoritative_when_metadata_projection_fails(monkeypatch, tmp_path) -> None:
    runner, session = _product_runner("always"); runner._workspace_path = tmp_path; runner._rehydrate_pending_permissions("permission_request", {"request_id": "permission-1", "category": "shell"}); runner._permission_queue = asyncio.Queue(); fsyncs, real_fsync = [], os.fsync; monkeypatch.setattr(os, "fsync", lambda fd: fsyncs.append(fd) or real_fsync(fd)); assert upsert_permission_rule(tmp_path, category="shell", pattern="safe.sh", decision="allow")
    async def fail(*_a, **_kw): raise OSError("metadata unavailable")  # type: ignore[no-untyped-def]
    monkeypatch.setattr(runner.registry, "update_metadata", fail); result = await runner.handle_command("permission_decision", {"request_id": "permission-1", "decision": "always", "rule": "*.sh"})
    assert fsyncs and result["decision"] == "always" and [(rule.pattern, rule.decision) for rule in load_permission_rules(tmp_path)] == [("safe.sh", "allow"), ("*.sh", "allow")] and runner._permission_queue.qsize() == 1 and session.read_model.status == "running"
    workers = [threading.Thread(target=upsert_permission_rule, kwargs={"workspace_dir": tmp_path, "category": "shell", "pattern": f"parallel-{index}.sh", "decision": "allow"}) for index in range(4)]; [worker.start() for worker in workers]; [worker.join() for worker in workers]; assert {rule.pattern for rule in load_permission_rules(tmp_path)}.issuperset({f"parallel-{index}.sh" for index in range(4)})
    processes = [multiprocessing.get_context("spawn").Process(target=_upsert_process, args=(str(tmp_path), f"process-{index}.sh")) for index in range(4)]; [process.start() for process in processes]; [process.join(10) for process in processes]; assert all(process.exitcode == 0 for process in processes)
    context = multiprocessing.get_context("spawn"); entered, release = context.Event(), context.Event(); holder = context.Process(target=_hold_rule_lock, args=(str(tmp_path), entered, release)); holder.start(); assert entered.wait(5); writer = context.Process(target=_upsert_process, args=(str(tmp_path), "blocked.sh")); writer.start(); writer.join(.2); assert writer.is_alive(); release.set(); holder.join(10); writer.join(10); assert holder.exitcode == writer.exitcode == 0
@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires privileges on Windows")
def test_permission_rules_reject_workspace_metadata_symlink(monkeypatch, tmp_path) -> None:
    outside = tmp_path / "outside"; outside.mkdir(); metadata = tmp_path / ".breadboard"; metadata.symlink_to(outside, target_is_directory=True)
    assert load_permission_rules(tmp_path) == []; pytest.raises(OSError, upsert_permission_rule, tmp_path, category="shell", pattern="escape.sh", decision="allow"); assert not list(outside.iterdir()); metadata.unlink(); metadata.mkdir(); lock_target = outside / "lock"; lock_path = metadata / "permission_rules.json.lock"; lock_path.symlink_to(lock_target); pytest.raises(OSError, upsert_permission_rule, tmp_path, category="shell", pattern="lock.sh", decision="allow"); assert not lock_target.exists(); lock_path.unlink()
    real_write, old = rules_store._write_at, tmp_path / ".breadboard-old"
    def swap_write(*args, **kwargs): metadata.rename(old); metadata.symlink_to(outside, target_is_directory=True); return real_write(*args, **kwargs)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(rules_store, "_write_at", swap_write); assert upsert_permission_rule(tmp_path, category="shell", pattern="anchored.sh", decision="allow"); assert not list(outside.iterdir()) and b"anchored.sh" in (old / "permission_rules.json").read_bytes()
@pytest.mark.asyncio
async def test_directory_fsync_failure_never_releases_always_decision(monkeypatch, tmp_path) -> None:
    runner, session = _product_runner("fsync"); runner._workspace_path = tmp_path; runner._rehydrate_pending_permissions("permission_request", {"request_id": "permission-1", "category": "shell"}); runner._permission_queue = asyncio.Queue(); real_fsync = os.fsync; monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("directory fsync failed")) if stat.S_ISDIR(os.fstat(fd).st_mode) else real_fsync(fd))
    with pytest.raises(RuntimeError, match="failed to commit permission decision"): await runner.handle_command("permission_decision", {"request_id": "permission-1", "decision": "always", "rule": "*.sh"})
    assert runner._permission_queue.empty() and session.read_model.status == "failed"
@pytest.mark.asyncio
async def test_only_active_permission_can_commit_and_concurrent_duplicates_deliver_once() -> None:
    runner, _ = _product_runner("active"); runner._rehydrate_pending_permissions("permission_request", {"request_id": "first"}); runner._rehydrate_pending_permissions("permission_request", {"request_id": "second"}); runner._permission_queue = asyncio.Queue()
    with pytest.raises(ValueError, match="not active"): await runner.handle_command("permission_decision", {"request_id": "second", "decision": "once"})
    results = await asyncio.gather(*(runner.handle_command("permission_decision", {"request_id": "first", "decision": "once"}) for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1 and sum(isinstance(item, ValueError) for item in results) == 1 and runner._permission_queue.qsize() == 1
@pytest.mark.asyncio
async def test_pause_blocks_dequeued_work_until_resume_and_stop_signals_active_agent(monkeypatch) -> None:
    runner, session = _product_runner("control"); runner.session.metadata["cli_session_kind"] = "oneshot"; await runner.registry.create(runner.session); started, paused, stopped = threading.Event(), threading.Event(), threading.Event()
    def run_task(*_a, **kw): started.set(); assert kw["control_queue"].get(timeout=1) == {"kind": "pause"}; paused.set(); assert kw["control_queue"].get(timeout=1) == {"kind": "resume"}; stopped.set() if kw["control_queue"].get(timeout=1) == {"kind": "stop"} else None; return {"completion_summary": {"completed": True}}  # type: ignore[no-untyped-def]
    runner._agent = type("Agent", (), {"_local_mode": True, "config": {}, "run_task": run_task, "apply_runtime_overrides": lambda *_: True})(); monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {}); monkeypatch.setattr(runner, "_ensure_agent_initialized", _initialized)
    await runner.handle_command("pause", {}); running = asyncio.create_task(runner._run()); await asyncio.sleep(0.05); assert not started.is_set() and session.read_model.status == "paused"
    await runner.handle_command("resume", {}); assert await asyncio.to_thread(started.wait, 1); await runner.handle_command("pause", {}); assert await asyncio.to_thread(paused.wait, 1); await runner.handle_command("resume", {}); runner._request_stop("operator"); await running; assert stopped.is_set() and session.read_model.status == "canceled"
@pytest.mark.asyncio
async def test_oneshot_success_is_hidden_until_durable_complete(monkeypatch) -> None:
    runner, session = _product_runner("completion"); runner.session.metadata["cli_session_kind"] = "oneshot"; await runner.registry.create(runner.session); monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {}); runner._agent = type("Agent", (), {"_local_mode": True, "config": {}, "run_task": lambda *_a, **kw: (kw["event_emitter"]("completion", {"summary": {}}), kw["event_emitter"]("run_finished", {"completed": True}), {"completion_summary": {"completed": True}})[-1]})(); monkeypatch.setattr(runner, "_ensure_agent_initialized", _initialized); session._sink = _Failing()
    with pytest.raises(OSError, match="sink unavailable"): await runner._run()
    visible = {event.type for event in runner.session.event_queue._queue if event is not None}; assert not visible.intersection({EventType.COMPLETION, EventType.RUN_FINISHED}) and runner.session.completion_summary is None
@pytest.mark.asyncio
@pytest.mark.parametrize("one_shot", [False, True])
async def test_provider_terminal_events_are_payload_faithful_and_exactly_once(monkeypatch, one_shot: bool) -> None:
    runner, _ = _product_runner("terminal"); completion, finished = {"summary": {"provider": True}, "opaque": "completion"}, {"completed": True, "opaque": "finished"}; barrier = threading.Barrier(2)
    def run_task(*_a, **kw): emit = lambda: (barrier.wait(timeout=1), kw["event_emitter"]("completion", completion, turn=7), barrier.wait(timeout=1), kw["event_emitter"]("run_finished", finished, turn=8)); threads = [threading.Thread(target=emit, daemon=True) for _ in range(2)]; [thread.start() for thread in threads]; [thread.join(timeout=2) for thread in threads]; assert all(not thread.is_alive() for thread in threads); return {"completion_summary": {"completed": True}}  # type: ignore[no-untyped-def]
    runner._agent = type("Agent", (), {"_local_mode": True, "config": {}, "run_task": run_task})(); runner.session.metadata.update({"cli_session_kind": "oneshot"} if one_shot else {}); monkeypatch.setattr(runner, "prepare_runtime_config", lambda: {}); monkeypatch.setattr(runner, "_ensure_agent_initialized", _initialized); _ = await runner.registry.create(runner.session) if one_shot else runner._execute_task("task"); await runner._run() if one_shot else None
    assert [(event.type, event.payload, event.turn) for event in runner.session.event_queue._queue if event and event.type in {EventType.COMPLETION, EventType.RUN_FINISHED}] == [(EventType.COMPLETION, completion, 7), (EventType.RUN_FINISHED, finished, 8)]
@pytest.mark.asyncio
async def test_mutating_loader_cannot_split_frozen_start_artifacts(monkeypatch, tmp_path: Path) -> None:
    first = {"tools": {"registry": {"include": ["first_tool"]}}, "policies": {"tools": {"allow": ["first_tool"], "deny": ["blocked_tool"]}, "models": {"deny": ["blocked-model"]}, "approvals": {"mode": "always_required"}}, "provider_auth_runtime": {"openai": {"api_key": "runtime-secret"}}, "provider_auth_runtime.openai.api_key": "flat-secret", "wrapper": {"provider_auth_runtime": {"openai": {"api_key": "nested-secret"}}}}; source, calls = json.loads(json.dumps(first)), []
    def load(path): calls.append(path); return source  # type: ignore[no-untyped-def]
    def enable_primitives() -> bool: source["tools"]["registry"]["include"] = ["second_tool"]; return True
    async def start(_runner) -> None: return None  # type: ignore[no-untyped-def]
    monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.session_runner.load_agent_config", load); monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.runtime_emission.load_agent_config", lambda _: pytest.fail("emitter reloaded mutable source")); monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.service.primitive_emission_enabled", enable_primitives); monkeypatch.setattr(RUNNER, start)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "records")); monkeypatch.setenv("BREADBOARD_SESSION_EVENT_ROOT", str(tmp_path / "events")); service = SessionService(); response = await service.create_session(SessionCreateRequest(config_path="mutating.json", task="test")); record = await service.ensure_session(response.session_id); root = tmp_path / "records" / response.session_id
    payloads = {name: json.loads((root / f"{name}.json").read_text()) for name in ("effective_config_graph", "capability_registry", "effective_tool_surface", "effective_operation_policy")}; graph, policy = payloads["effective_config_graph"], payloads["effective_operation_policy"]; tool_ids = {item["capability_id"] for item in payloads["capability_registry"]["capabilities"] if item["capability_type"] == "tool"}
    assert calls == ["mutating.json"] and graph["graph_hash"] == record.product_session.events[0].payload["effective_lock_hash"] and tool_ids == {"tool.first_tool"} == set(payloads["effective_tool_surface"]["tool_ids"]) and [(rule["decision"], rule["match"]["pattern"]) for rule in policy["tool_policy"]["rules"]] == [("allow", "first_tool"), ("deny", "blocked_tool")]
    serialized = json.dumps(payloads) + "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file()); assert policy["approvals"]["mode"] == "always_required" and all(secret not in serialized for secret in ("provider_auth_runtime", "runtime-secret", "flat-secret", "nested-secret", "second_tool"))
    captured = {}; Agent = type("Agent", (), {"workspace_dir": str(tmp_path / "workspace"), "initialize": lambda self: None}); factory = lambda path, _workspace, _overrides: (captured.update(config=json.loads(Path(path).read_text()), path=path), Agent())[1]  # type: ignore[assignment]
    record.runner.agent_factory = factory; record.runner.get_skill_catalog(); await record.runner._ensure_agent_initialized(); assert calls == ["mutating.json"] and captured["config"] == record.runner.current_runtime_config() and captured["config"]["provider_auth_runtime"]["openai"]["api_key"] == "runtime-secret" and not Path(captured["path"]).exists()
    with pytest.raises(ValueError, match="denied by policy"): await record.runner.handle_command("set_model", {"model": "blocked-model"})
    await service.stop_session(response.session_id); await _stop(record)
@pytest.mark.parametrize(("expected", "aliases"), [("once", "once allow approve approved ok okay yes y allow-once allow_once"), ("always", "allow-always allow_always always"), ("reject", "reject deny denied no n deny-once deny_once deny-always deny_always")])
def test_permission_aliases_and_unknowns(expected: str, aliases: str) -> None:
    for alias in aliases.split(): assert _canonical_permission_resolution(alias, None) == expected
    assert _canonical_permission_resolution("arbitrary", None) == "reject"
def test_permission_replay_uses_configured_default_for_sparse_items() -> None:
    assert _canonical_permission_resolution(None, {"items": {"a": "always"}}, ["a", "b"], "once") == "once"
    assert _canonical_permission_resolution(None, {"items": {"a": "always"}, "default": "always"}, ["a", "b"], "once") == "always"
@pytest.mark.asyncio
async def test_mode_and_reconfigure_follow_runtime_state_machine() -> None:
    runner, session = _product_runner("mode"); await runner.registry.create(runner.session); await runner.handle_command("pause", {}); result = await runner.handle_command("set_mode", {"mode": "plan"}); assert result["mode"] == "plan" and session.read_model.status == "paused" and runner.session.metadata["mode"] == "plan"
    await runner.handle_command("resume", {}); await runner.handle_command("set_mode", {"mode": "ask"}); assert session.read_model.status == "running" and runner.session.metadata["mode"] == "ask"
@pytest.mark.asyncio
async def test_stop_waits_for_task_and_dispatcher_shutdown() -> None:
    runner, _ = _product_runner("stop-wait"); await runner.registry.create(runner.session); released = asyncio.Event()
    async def hold(): await released.wait()  # type: ignore[no-untyped-def]
    runner._task = asyncio.create_task(hold()); runner._dispatcher_task = asyncio.create_task(hold()); stopping = asyncio.create_task(runner.stop()); await asyncio.sleep(0); assert not stopping.done(); released.set(); await stopping; assert runner._task.done() and runner._dispatcher_task.done()
