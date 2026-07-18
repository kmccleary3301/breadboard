from __future__ import annotations
import json, os, threading, multiprocessing, pytest
from pathlib import Path; from typing import Any
from jsonschema import Draft202012Validator
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime.events import KernelEvent, SessionView, rebuild
from breadboard.product.runtime.ports import JsonlEventSink
from breadboard.product.runtime.session import Session
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
@pytest.mark.parametrize(("kind", "payload"), [
    ("input.accepted", {"content_hash": HASH, "attachments": [{"digest": HASH, "size_bytes": True, "media_type": "text/plain"}]}), ("approval.requested", {"request_id": "", "operation": "write"}),
    ("approval.resolved", {"request_id": "r", "decision": "maybe"}), ("session.reconfigured", {"effective_lock_hash": HASH, "reason": 1}), ("session.paused", {"reason": None}),
    ("session.resumed", {"extra": True}), ("session.completed", {"outcome": "completed"}), ("session.failed", {"outcome": "completed", "error": "x", "detail": "y"}), ("session.canceled", {"outcome": "canceled", "reason": 1})])
def test_event_specific_payloads_are_validated_on_reconstitution(kind: str, payload: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)): KernelEvent("s-1", 2, kind, "now", payload)
def test_session_view_deeply_freezes_terminal_outcome() -> None:
    outcome = {"outcome": "failed", "error": "code", "detail": "detail", "nested": {"code": 1}}; view = SessionView("s-1", "failed", HASH, HASH, 2, terminal_outcome=outcome); outcome["nested"]["code"] = 2
    with pytest.raises(TypeError): view.terminal_outcome["nested"]["code"] = 3  # type: ignore[index]
    assert view.as_dict()["terminal_outcome"]["nested"]["code"] == 1
    class Changing(dict[str, Any]):
        def items(self) -> Any: return {"outcome": "failed", "summary": 7}.items()
    with pytest.raises(ValueError): SessionView("s-1", "completed", HASH, HASH, 2, terminal_outcome=Changing(outcome="completed", summary="ok"))
_ACTIONS = {"input": lambda s: s.input("content"), "request": lambda s: s.request_approval("r", "write"), "resolve": lambda s: s.resolve_approval("r", "allow"), "reconfigure": lambda s: s.reconfigure(_lock(OTHER_HASH), ""), "pause": lambda s: s.pause(""), "resume": lambda s: s.resume(), "cancel": lambda s: s.cancel(""), "complete": lambda s: s.complete(""), "fail": lambda s: s.fail("error", "detail")}
_FACADE_ALLOWED = {"input": {"running"}, "request": {"running"}, "resolve": {"awaiting_approval"}, "reconfigure": {"running", "awaiting_approval", "paused"}, "pause": {"running"}, "resume": {"paused"}, "cancel": {"running", "awaiting_approval", "paused"}, "complete": {"running"}, "fail": {"running", "awaiting_approval", "paused"}}
def _session(status: str) -> Session:
    session = Session.start(_lock(), "task")
    if status != "running": {"awaiting_approval": lambda: session.request_approval("r", "write"), "paused": lambda: session.pause(""), "completed": lambda: session.complete(""), "failed": lambda: session.fail("error", "detail"), "canceled": lambda: session.cancel("")}[status]()
    return session
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
class _FalsyIds:
    def __bool__(self) -> bool: return False
    def new_id(self) -> str: return "falsy-id"
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
    def append(self, event: KernelEvent) -> None:
        if event.sequence == 2 and not self.entered.is_set(): self.first = threading.get_ident(); self.entered.set(); assert self.release.wait(1)
        self.events.append(event)
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
def test_concurrent_identical_artifact_puts_share_verified_ref_and_clean_temps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store, barrier, real_replace, refs, errors = ArtifactStore(tmp_path), threading.Barrier(2), os.replace, [], []; monkeypatch.setattr("breadboard.product.runtime.artifacts.os.replace", lambda source, target: (barrier.wait(), real_replace(source, target))[-1])
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
    files = [path for path in tmp_path.rglob("*") if path.is_file()]; assert [path.read_bytes() for path in files] in ([], [b"proof"]); ref = store.put(b"proof"); assert store.read(ref) == b"proof" and calls[0] >= 6 and not list(tmp_path.rglob("*.tmp"))
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
_EVENT_KINDS = {"input": "input.accepted", "request": "approval.requested", "resolve": "approval.resolved", "reconfigure": "session.reconfigured", "pause": "session.paused", "resume": "session.resumed", "cancel": "session.canceled", "complete": "session.completed", "fail": "session.failed"}
_REPLAY_DENIED = [(status, action) for status in ("running", "awaiting_approval", "paused", "completed", "failed", "canceled") for action in _ACTIONS if status not in _FACADE_ALLOWED[action]]
@pytest.mark.parametrize(("status", "action"), _REPLAY_DENIED)
def test_replay_transition_table_rejects_every_disallowed_pair(status: str, action: str) -> None:
    events = list(_session(status).events); events.append(_event(len(events) + 1, _EVENT_KINDS[action]))
    with pytest.raises(ValueError): rebuild(events)
def test_public_session_schema_matches_projection_invariants() -> None:
    validator = Draft202012Validator(json.loads((Path(__file__).resolve().parents[3] / "contracts/public/schemas/bb.session.v1.schema.json").read_text())); valid = _session("running").read_model.as_dict(); validator.validate(valid)
    for patch in ({"effective_lock_hash": HASH + "\n"}, {"task_hash": valid["task_hash"] + "\n"}, {"pending_approval": "r"}, {"status": "awaiting_approval"}, {"status": "completed", "event_count": 1, "terminal_outcome": {"outcome": "completed", "summary": ""}}, {"status": "completed", "event_count": 2}, {"status": "completed", "event_count": 2, "terminal_outcome": {"outcome": "failed", "error": "x", "detail": "y"}}): assert list(validator.iter_errors({**valid, **patch})), patch
