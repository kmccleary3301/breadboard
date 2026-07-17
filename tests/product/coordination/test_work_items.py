import hashlib, json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from operator import setitem
from pathlib import Path
from threading import Barrier, Event, current_thread
from types import SimpleNamespace
from typing import Any
import pytest
from agentic_coder_prototype.compilation.primitive_records import PrimitiveCompileError, finalize_record, get_spec, validate_record
from breadboard.product.coordination.placement import WorkPlacement
from breadboard.product.coordination.views import CoordinationProjector
from breadboard.product.coordination.work_items import Budget, CancellationPolicy, ResumePolicy, RetryPolicy, WorkItem, WorkItemEvent, WorkItemRepository, WorkItemSnapshot, rebuild_work_item
CLOCK = SimpleNamespace(now=lambda: "2026-07-17T00:00:04Z")
def _new(**policies: Any) -> WorkItem:
    return WorkItem.create("ship packet", work_item_id="work-1", clock=CLOCK, **policies)
def _run(item: WorkItem, number: int = 1) -> None:
    item.acquire_lease(f"worker-{number}", lease_id=f"lease-{number}")
    item.start_attempt(f"session-{number}", attempt_id=f"attempt-{number}")
def _record(snapshot: WorkItemSnapshot) -> dict[str, Any]:
    record = json.loads(json.dumps(asdict(snapshot)))
    placements = record.pop("placements")
    record["budget"] = {"limits": record.pop("budget"), "usage": record.pop("budget_usage")}
    record["placement_refs"] = [row["placement_id"] for row in placements]
    return {"schema_version": "bb.work_item.v2", **record}
def _work_item(status: str = "running", retry: bool = False) -> dict[str, Any]:
    item = _new(dependency_refs=("dependency-1",) if status == "blocked" else (), retry_policy=RetryPolicy(2, True, ("transient",)), resume_policy=ResumePolicy("restart"))
    if status in {"blocked", "ready"}: return _record(item.read_model)
    item.acquire_lease("worker-1", lease_id="lease-1")
    if status == "leased": return _record(item.read_model)
    item.start_attempt("session-1", attempt_id="attempt-1")
    if retry:
        item.fail_attempt("transient", retryable=True); _run(item, 2)
    elif status == "waiting": item.wait(("signal-1",), "waiting")
    elif status == "paused": item.pause("paused")
    elif status == "completed": item.complete("completed")
    elif status == "failed": item.fail_attempt("failed", retryable=False)
    elif status == "canceled": item.cancel("operator", "canceled")
    return _record(item.read_model)
def _semantic_error(record: dict[str, Any], pointer: str, message: str) -> None:
    for operation in (validate_record, finalize_record):
        with pytest.raises(PrimitiveCompileError) as caught:
            operation(get_spec("bb.work_item.v2"), record)
        assert (pointer, message) in caught.value.errors
def _fails(error: type[BaseException], action: Any, match: str | None = None) -> None:
    with pytest.raises(error, match=match): action()
class DelayedProjector(CoordinationProjector):
    def __init__(self) -> None:
        self.published, self.release = Event(), Event(); super().__init__()
    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)
        if name == "_view" and value is not None and current_thread().name.startswith("delayed"): self.published.set()
    def __getattribute__(self, name: str) -> object:
        if name == "_view" and current_thread().name.startswith("delayed") and object.__getattribute__(self, "published").is_set():
            assert object.__getattribute__(self, "release").wait(5)
        return object.__getattribute__(self, name)
def test_v2_contracts_validate_and_finalize_reducer_states() -> None:
    for status in ("blocked", "ready", "leased", "running", "waiting", "paused", "completed", "failed", "canceled"):
        record = _work_item(status)
        assert validate_record(get_spec("bb.work_item.v2"), record) == record
        assert finalize_record(get_spec("bb.work_item.v2"), record) == record
    retry_record = _work_item(retry=True)
    assert validate_record(get_spec("bb.work_item.v2"), retry_record) == retry_record
    assert finalize_record(get_spec("bb.work_item.v2"), retry_record) == retry_record
    validate_record(get_spec("bb.work_placement.v1"), {"schema_version": "bb.work_placement.v1", **WorkPlacement("placement-1", "work-1", "attempt-1", "worker-1", "session-1", "target-a", "2026-07-17T00:00:04Z").as_dict()})
    validate_record(get_spec("bb.coordination_view.v1"), {"schema_version": "bb.coordination_view.v1", "view_id": "view-1", "projected_at": "2026-07-17T00:00:04Z", "source_event_count": 1, "items": [], "delegation_edges": [], "placements": []})
@pytest.mark.parametrize("status", ["completed", "failed", "waiting", "paused"])
def test_v2_rejects_empty_or_mismatched_closed_attempts(status: str) -> None:
    empty = _work_item(status); empty["attempts"] = []; _semantic_error(empty, "/attempts", f"{status} Work Item requires a matching closed current attempt")
    mismatch = _work_item(status); mismatch["attempts"][-1]["status"] = "failed" if status != "failed" else "completed"; _semantic_error(mismatch, "/attempts", f"{status} Work Item requires a matching closed current attempt")
    if status in {"completed", "failed"}: reason = _work_item(status); reason["terminal_reason"] = "other"; _semantic_error(reason, "/terminal_reason", f"must match the current {status} attempt reason")
@pytest.mark.parametrize(("status", "retry", "mutate", "pointer", "message"), [
    ("running", False, lambda r: r["active_lease"].__setitem__("worker_id", "other"), "/active_lease/worker_id", "must match the current running attempt worker_id"),
    ("running", False, lambda r: r["active_lease"].__setitem__("expires_at", r["active_lease"]["acquired_at"]), "/active_lease/expires_at", "must be later than active_lease.acquired_at"),
    ("running", True, lambda r: r.__setitem__("used_lease_ids", list(reversed(r["used_lease_ids"]))), "/active_lease/lease_id", "must be the last used_lease_ids entry"),
    ("running", True, lambda r: r.__setitem__("used_lease_ids", [r["active_lease"]["lease_id"]]), "/used_lease_ids", "must contain at least one acquired lease ID per attempt"),
    ("running", True, lambda r: (r["attempts"][0].update(status="running"), r["attempts"][1].update(status="failed", ended_at="later", reason="failed")), "/attempts", "running Work Item requires the running attempt to be current"),
    ("running", True, lambda r: r["attempts"][0].__setitem__("number", 2), "/attempts/0/number", "attempt number must be 1 at this position"),
    ("running", True, lambda r: r["attempts"][1].__setitem__("attempt_id", r["attempts"][0]["attempt_id"]), "/attempts/1/attempt_id", "attempt_id must be unique across attempts"),
    ("running", True, lambda r: r["attempts"][1].__setitem__("session_ref", r["attempts"][0]["session_ref"]), "/attempts/1/session_ref", "session_ref must be unique across attempts"),
    ("blocked", False, lambda r: r.__setitem__("satisfied_dependency_refs", ["other"]), "/satisfied_dependency_refs/0", "must also appear in dependency_refs"),
    ("blocked", False, lambda r: r.__setitem__("satisfied_dependency_refs", r["dependency_refs"]), "/status", "blocked Work Item requires at least one unsatisfied dependency"),
    ("running", False, lambda r: r.__setitem__("dependency_refs", ["dependency-1"]), "/satisfied_dependency_refs", "all dependencies must be satisfied while status is running"),
    ("running", False, lambda r: (r["budget"]["limits"].__setitem__("token_limit", 10), r["budget"]["usage"].__setitem__("tokens", 11)), "/budget/usage/tokens", "must not exceed budget.limits.token_limit"),
    ("canceled", False, lambda r: r.__setitem__("terminal_reason", "other"), "/terminal_reason", "must match the current canceled attempt reason"),
    ("running", False, lambda r: r.__setitem__("parent_work_item_id", "work-1"), "/parent_work_item_id", "must not equal work_item_id"),
    ("running", False, lambda r: r.__setitem__("child_work_item_ids", ["work-1"]), "/child_work_item_ids/0", "must not equal work_item_id"),
    ("waiting", False, lambda r: r["resume_policy"].__setitem__("mode", "never"), "/resume_policy/mode", "waiting Work Item cannot use never resume policy"),
    ("paused", False, lambda r: r["resume_policy"].__setitem__("mode", "never"), "/resume_policy/mode", "paused Work Item cannot use never resume policy"),
    ("waiting", False, lambda r: r["resume_policy"].__setitem__("mode", "checkpoint"), "/attempts/0/checkpoint_ref", "checkpoint resume policy requires the current attempt checkpoint_ref"),
    ("paused", False, lambda r: r["resume_policy"].__setitem__("mode", "checkpoint"), "/attempts/0/checkpoint_ref", "checkpoint resume policy requires the current attempt checkpoint_ref"),
    ("running", False, lambda r: r["active_lease"].__setitem__("expires_at", r["attempts"][-1]["started_at"]), "/attempts/0/started_at", "must be earlier than active_lease.expires_at"),
])
def test_v2_semantic_invariants(status: str, retry: bool, mutate: Any, pointer: str, message: str) -> None:
    record = _work_item(status, retry); mutate(record); _semantic_error(record, pointer, message)
def test_v1_schema_and_examples_remain_frozen() -> None:
    expected = {"bb.work_item.v1.schema.json": "70c7c9f2e47a7a35149d14abdcccac3e47e603093256ec4189570fe25ca40664", "work_item_minimal.json": "641221983eba12f404d9d7a6414dfe2abcc55d75549424ca049caf27dff689f1", "work_item_with_execution_refs.json": "3a58aafc5c1171a4622793ace1fa51d5a8c1fee60c2b0ab73b7b320487f720bd"}
    for name, digest in expected.items():
        directory = "schemas" if name.endswith("schema.json") else "examples"; path = Path(__file__).resolve().parents[3] / "contracts/kernel" / directory / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if directory == "examples":
            record = json.loads(path.read_text())
            assert validate_record(get_spec("bb.work_item.v1"), record) == record
            _fails(PrimitiveCompileError, lambda: finalize_record(get_spec("bb.work_item.v1"), record), "validation-only")
def test_projection_lifecycle_correlation_reciprocity_and_ordering() -> None:
    parent = _new(); _run(parent)
    assert parent.attach_placement(WorkPlacement("p-z", "work-1", "attempt-1", "worker-1", "session-1", "target-a", "2026-07-17T00:00:04Z")).status == "running"
    left = parent.delegate("left", child_work_item_id="left", resume_policy=ResumePolicy("restart")); _run(left)
    left.attach_placement(WorkPlacement("p-a", "left", "attempt-1", "worker-1", "session-1", "target-b", "2026-07-17T00:00:04Z"))
    assert left.wait(("wake",), "waiting").status == "waiting" and left.wake("wake").status == "ready"
    _run(left, 2)
    assert [(row.attempt_id, row.status) for row in left.read_model.attempts] == [("attempt-1", "waiting"), ("attempt-2", "running")]
    right = parent.delegate("right", child_work_item_id="right")
    assert right.add_dependency("dep").status == "blocked" and right.satisfy_dependency("dep").status == "ready"
    authority = parent.events, left.events, right.events
    _fails(ValueError, lambda: CoordinationProjector().rebuild((parent,)), "reciprocal")
    assert (parent.events, left.events, right.events) == authority
    _fails(ValueError, lambda: parent.attach_placement(WorkPlacement("wrong", "other", "attempt-1", "worker-1", "session-1", "target", "now")), "does not match")
    assert parent.read_model.status == "running" and parent.events == authority[0]
    projector = CoordinationProjector(); view = projector.rebuild((right, parent, left))
    assert [row.work_item_id for row in view.items] == ["left", "right", "work-1"]
    assert [(edge.parent_work_item_id, edge.child_work_item_id) for edge in view.delegation_edges] == [("work-1", "left"), ("work-1", "right")]
    assert [row.placement_id for row in view.placements] == ["p-a", "p-z"]
    _fails(ValueError, lambda: projector.rebuild(()), "at least one")
@pytest.mark.parametrize("replacement", [None, "second"])
def test_clear_and_rebuild_interleavings_return_local_view(replacement: str | None) -> None:
    projector = DelayedProjector()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="delayed") as pool:
        future = pool.submit(projector.rebuild, (_new(),)); assert projector.published.wait(5)
        if replacement is None: projector.clear(); expected = None
        else: expected = projector.rebuild((WorkItem.create(replacement, work_item_id=replacement),))
        projector.release.set(); first = future.result(5)
    assert [row.work_item_id for row in first.items] == ["work-1"] and projector.view is expected
def test_concurrent_lease_acquisition_has_one_repository_winner() -> None:
    repository, barrier = WorkItemRepository(), Barrier(2)
    WorkItem.create("shared", work_item_id="shared", repository=repository)
    workers = [WorkItem.restore(repository, "shared") for _ in range(2)]
    def acquire(index: int) -> bool:
        barrier.wait()
        try: workers[index].acquire_lease(f"worker-{index}", lease_id=f"lease-{index}")
        except RuntimeError: return False
        return True
    with ThreadPoolExecutor(max_workers=2) as pool: outcomes = list(pool.map(acquire, range(2)))
    winner = outcomes.index(True); snapshot = WorkItem.restore(repository, "shared").read_model
    assert sorted(outcomes) == [False, True] and len(repository.read("shared")) == 2 and snapshot.active_lease.worker_id == f"worker-{winner}"
@pytest.mark.parametrize("command", [lambda item: item.checkpoint("stale"), lambda item: item.release_lease("lease-2")])
def test_stale_worker_handles_are_fenced(command: Any) -> None:
    repository = WorkItemRepository()
    current = WorkItem.create("shared", work_item_id="shared", retry_policy=RetryPolicy(2, True), repository=repository, clock=CLOCK); _run(current)
    assert current.events[-1].payload["lease_id"] == "lease-1"
    stale = WorkItem.restore(repository, "shared", clock=CLOCK)
    current.fail_attempt("retry", retryable=True); _run(current, 2); before = repository.read("shared")
    _fails(RuntimeError, lambda: command(stale), "stale Work Item")
    assert repository.read("shared") == before
def test_expiry_replay_tampering_and_lease_aba_are_rejected() -> None:
    times = iter(("2026-07-17T00:00:01Z", "2026-07-17T00:00:02Z", "2026-07-17T00:00:04Z")); item = WorkItem.create("ship packet", work_item_id="work-1", clock=SimpleNamespace(now=lambda: next(times)))
    item.acquire_lease("worker-1", lease_id="lease-1", expires_at="2026-07-17T00:00:03Z"); leased = item.events; _fails(RuntimeError, lambda: item.start_attempt("session-1", attempt_id="attempt-1"), "lease expiry")
    assert (item.events[-1].kind, item.events[-1].occurred_at) == ("lease.expired", "2026-07-17T00:00:04Z") and rebuild_work_item(item.events) == item.read_model
    late = WorkItemEvent("work-1", len(leased) + 1, "attempt.started", "2026-07-17T00:00:04Z", {"attempt_id": "late", "lease_id": "lease-1", "session_ref": "late"}); _fails(ValueError, lambda: rebuild_work_item((*leased, late)), "at or after lease expiry")
    reusable = _new(); reusable.acquire_lease("worker-1", lease_id="reused"); reusable.release_lease("reused"); _fails(ValueError, lambda: reusable.acquire_lease("worker-2", lease_id="reused"), "previously used lease"); assert _record(reusable.read_model)["used_lease_ids"] == ["reused"]
    for command in (lambda x: x.checkpoint("late"), lambda x: x.consume_budget(tokens=1), lambda x: x.delegate("late", child_work_item_id="late"), lambda x: x.complete("late")):
        ticks = iter(("2026-07-17T00:00:01Z", "2026-07-17T00:00:02Z", "2026-07-17T00:00:02Z", "2026-07-17T00:00:04Z", "2026-07-17T00:00:05Z", "2026-07-17T00:00:06Z")); running = WorkItem.create("running", work_item_id="running", retry_policy=RetryPolicy(2, True), clock=SimpleNamespace(now=lambda: next(ticks))); running.acquire_lease("worker-1", lease_id="lease-1", expires_at="2026-07-17T00:00:03Z"); running.start_attempt("session-1", attempt_id="attempt-1"); active = running.events
        _fails(RuntimeError, lambda: command(running), "lease expiry"); expired = running.read_model; assert running.events[-1].kind == "attempt.expired" and expired.status == "ready" and expired.attempts[-1].reason == "lease expired" and rebuild_work_item(running.events) == expired; forged = WorkItemEvent("running", len(active) + 1, "placement.attached", "2026-07-17T00:00:04Z", {"placement": WorkPlacement("late", "running", "attempt-1", "worker-1", "session-1", "target", "2026-07-17T00:00:04Z").as_dict()}); _fails(ValueError, lambda: rebuild_work_item((*active, forged)), "placement occurred at or after lease expiry")
        running.acquire_lease("worker-2", lease_id="lease-2"); running.start_attempt("session-2", attempt_id="attempt-2"); assert running.read_model.status == "running" and running.read_model.current_attempt.attempt_id == "attempt-2"
    ticks = iter(("2026-07-17T00:00:01Z", "2026-07-17T00:00:02Z", "2026-07-17T00:00:02Z", "2026-07-17T00:00:03Z", "2026-07-17T00:00:04Z", "2026-07-17T00:00:04Z")); boundary = WorkItem.create("boundary", work_item_id="boundary", clock=SimpleNamespace(now=lambda: next(ticks))); boundary.acquire_lease("worker-1", lease_id="lease-1", expires_at="2026-07-17T00:00:04Z"); boundary.start_attempt("session-1", attempt_id="attempt-1"); child = boundary.delegate("child", child_work_item_id="child"); assert boundary.events[-1].occurred_at == child.events[0].occurred_at == "2026-07-17T00:00:03Z"
def test_policy_event_repository_immutability_and_replay_tampering() -> None:
    actors = ["operator"]; policy = CancellationPolicy(cancellable_by=actors); actors.append("later")
    repository = WorkItemRepository(); WorkItem.create("immutable", work_item_id="immutable", repository=repository, clock=CLOCK)
    before = repository.read("immutable"); event = before[0]; serialized = event.as_dict(); serialized["payload"]["retry_policy"]["max_attempts"] = 99
    _fails(TypeError, lambda: setitem(event.payload["retry_policy"], "max_attempts", 2))
    assert policy.cancellable_by == ("operator",) and repository.read("immutable") == before and event.payload["retry_policy"]["max_attempts"] == 1
    tampered = WorkItemEvent("immutable", 3, "dependency.added", "later", {"dependency_ref": "dep"})
    _fails(ValueError, lambda: rebuild_work_item((event, tampered)), "contiguous")
def test_policies_use_current_checkpoints_and_make_terminals_immutable() -> None:
    resume = _new(resume_policy=ResumePolicy("checkpoint", requires_approval=True)); _run(resume)
    resume.checkpoint("old"); resume.pause("pause"); resume.resume(approved=True); _run(resume, 2)
    _fails(ValueError, lambda: resume.pause("no current checkpoint"), "resume policy")
    repository = WorkItemRepository(); cancel = WorkItem.create("cancel", work_item_id="cancel", retry_policy=RetryPolicy(2, True), cancellation_policy=CancellationPolicy("immediate", ("operator",), True, "checkpoint_then_stop"), repository=repository, clock=CLOCK)
    _run(cancel); stale = WorkItem.restore(repository, "cancel"); existing = WorkItem.create("existing", work_item_id="child", repository=repository)
    before = cancel.events, existing.events
    _fails(RuntimeError, lambda: cancel.delegate("collision", child_work_item_id="child"), "stale Work Item revision")
    assert (cancel.events, existing.events) == before
    child = cancel.delegate("child", child_work_item_id="new-child")
    assert stale.read_model.child_work_item_ids == ("new-child",) and WorkItem.restore(repository, "new-child").read_model.parent_work_item_id == "cancel"
    cancel.checkpoint("old"); cancel.fail_attempt("retry", retryable=True); _run(cancel, 2)
    _fails(ValueError, lambda: cancel.cancel("operator"), "current checkpoint")
    cancel.checkpoint("current"); canceled = cancel.cancel("operator")
    assert canceled.status == "canceled" and cancel.events[-1].payload["child_work_item_ids"] == ("new-child",) and child.read_model.status == "ready"
    limited = _new(retry_policy=RetryPolicy(2, True), budget=Budget(token_limit=1)); _run(limited); limited.consume_budget(tokens=1)
    final = limited.fail_attempt("retry", retryable=True)
    assert final.status == "failed" and final.budget_usage.tokens == 1
    before = limited.events; _fails(RuntimeError, lambda: limited.cancel("operator")); assert limited.events == before
