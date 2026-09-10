from __future__ import annotations

from pathlib import Path
from threading import Barrier, Thread

import pytest

from breadboard.product.harness.compile import compile_harness_definition
from breadboard.product.runtime.generations import (
    GenerationLifecycle,
    GenerationLifecycleError,
)


class SentinelPreparer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.created: list[str] = []
        self.failed: set[str] = set()

    def prepare(self, preparation, record_resource):
        sentinel = self.root / f"{preparation.generation_id}.sentinel"
        sentinel.write_text(preparation.source_ref, encoding="utf-8")
        self.created.append(preparation.generation_id)
        record_resource(str(sentinel))
        if preparation.source_ref in self.failed:
            raise RuntimeError("controlled prepare failure")
        return str(sentinel)

    def observe(self, resource_ref):
        return "ready" if Path(resource_ref).exists() else "absent"

    def dispose(self, resource_ref):
        path = Path(resource_ref)
        path.unlink(missing_ok=True)
        return "confirmed_absent"


class CrashAfterResource(BaseException):
    pass


class CrashPreparer(SentinelPreparer):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.crash_before: set[str] = set()
        self.crash_after: set[str] = set()

    def prepare(self, preparation, record_resource):
        if preparation.source_ref in self.crash_before:
            raise CrashAfterResource()
        sentinel = super().prepare(preparation, record_resource)
        if preparation.source_ref in self.crash_after:
            raise CrashAfterResource()
        return sentinel


class UnknownCleanupPreparer(SentinelPreparer):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.unknown: set[str] = set()

    def observe(self, resource_ref):
        if resource_ref in self.unknown:
            return "unknown"
        return super().observe(resource_ref)

    def dispose(self, resource_ref):
        self.unknown.add(resource_ref)
        return "unknown"

def _lock(name: str):
    return compile_harness_definition({"name": name}, source_ref=f"{name}.yaml").lock


def test_publication_admission_fence_retains_old_generation_and_rejects_stale_owner(tmp_path):
    preparer = SentinelPreparer(tmp_path / "resources")
    preparer.root.mkdir()
    lifecycle = GenerationLifecycle(tmp_path, preparer)
    lock_a, lock_b, lock_c = (_lock("a"), _lock("b"), _lock("c"))

    publication_a = lifecycle.prepare_and_publish("main", lock_a, "a.yaml", 0, "publish-a")
    admission_a = lifecycle.reserve_target_admission("main", "session-a", "input-a")
    replay = lifecycle.reserve_target_admission("main", "session-a", "input-a")
    assert replay == admission_a
    lifecycle.mark_materialized(admission_a.admission_id)
    publication_b = lifecycle.prepare_and_publish(
        "main", lock_b, "b.yaml", publication_a.revision, "publish-b"
    )
    assert lifecycle.current("main") == publication_b
    assert admission_a.generation_id == lock_a.generation_id
    lifecycle.require_dispatch(
        admission_a.admission_id,
        admission_a.generation_id,
        admission_a.work_id,
        admission_a.attempt_id,
        admission_a.controller_epoch,
        admission_a.grant_epoch,
    )

    with pytest.raises(GenerationLifecycleError) as pressure:
        lifecycle.prepare_and_publish("main", lock_c, "c.yaml", publication_b.revision, "publish-c")
    assert pressure.value.code == "capacity_pressure"
    assert (preparer.root / f"{lock_c.generation_id}.sentinel").exists() is False

    lifecycle.release(admission_a.admission_id, cleanup_confirmed=True)
    retry = lifecycle.reserve_target_admission("main", "session-a", "input-a")
    assert retry.admission_id != admission_a.admission_id
    assert retry.work_id != admission_a.work_id
    lifecycle.release(retry.admission_id, cleanup_confirmed=True)
    publication_a_again = lifecycle.prepare_and_publish(
        "main", lock_a, "a.yaml", publication_b.revision, "publish-a-again"
    )
    assert publication_a_again.generation_id == lock_a.generation_id
    with pytest.raises(GenerationLifecycleError) as stale:
        lifecycle.require_dispatch(
            admission_a.admission_id,
            admission_a.generation_id,
            admission_a.work_id,
            admission_a.attempt_id,
            admission_a.controller_epoch,
            admission_a.grant_epoch,
        )
    assert stale.value.code == "stale_dispatch"


def test_failed_prepare_keeps_previous_route_and_cleans_external_sentinel(tmp_path):
    preparer = SentinelPreparer(tmp_path / "resources")
    preparer.root.mkdir()
    lifecycle = GenerationLifecycle(tmp_path, preparer)
    lock_a, lock_b = _lock("a"), _lock("b")
    published = lifecycle.prepare_and_publish("main", lock_a, "a.yaml", 0, "publish-a")
    preparer.failed.add("b.yaml")

    with pytest.raises(GenerationLifecycleError) as failed:
        lifecycle.prepare_and_publish("main", lock_b, "b.yaml", published.revision, "publish-b")
    assert failed.value.code == "prepare_failed"
    assert lifecycle.current("main") == published
    assert (preparer.root / f"{lock_b.generation_id}.sentinel").exists() is False


def test_request_replay_and_competing_cas_are_durable_and_idempotent(tmp_path):
    preparer = SentinelPreparer(tmp_path / "resources")
    preparer.root.mkdir()
    first = GenerationLifecycle(tmp_path, preparer)
    lock_a, lock_b, lock_c = _lock("a"), _lock("b"), _lock("c")
    committed = first.prepare_and_publish("main", lock_a, "a.yaml", 0, "publish-a")
    recreated = GenerationLifecycle(tmp_path, preparer)
    assert recreated.prepare_and_publish("main", lock_a, "a.yaml", 0, "publish-a") == committed

    barrier = Barrier(2)
    outcomes: list[object] = []

    def publish(lock, request_id):
        barrier.wait()
        try:
            outcomes.append(recreated.prepare_and_publish("main", lock, f"{request_id}.yaml", 1, request_id))
        except GenerationLifecycleError as exc:
            outcomes.append(exc)

    threads = [Thread(target=publish, args=(lock_b, "publish-b")), Thread(target=publish, args=(lock_c, "publish-c"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(not isinstance(outcome, GenerationLifecycleError) for outcome in outcomes) == 1
    assert recreated.current("main").revision == 2


def test_reconcile_converges_crashes_before_and_after_resource_identity(tmp_path):
    preparer = CrashPreparer(tmp_path / "resources")
    preparer.root.mkdir()
    lock_a, lock_b = _lock("a"), _lock("b")
    preparer.crash_before.add("a.yaml")
    with pytest.raises(CrashAfterResource):
        GenerationLifecycle(tmp_path, preparer).prepare_and_publish(
            "main", lock_a, "a.yaml", 0, "publish-a"
        )

    lifecycle = GenerationLifecycle(tmp_path, preparer)
    reconciled = {item.request_id: item for item in lifecycle.reconcile()}
    assert reconciled["publish-a"].status == "failed"
    with pytest.raises(GenerationLifecycleError) as interrupted:
        lifecycle.prepare_and_publish("main", lock_a, "a.yaml", 0, "publish-a")
    assert interrupted.value.code == "prepare_interrupted"

    preparer.crash_after.add("b.yaml")
    with pytest.raises(CrashAfterResource):
        lifecycle.prepare_and_publish("main", lock_b, "b.yaml", 0, "publish-b")
    assert (preparer.root / f"{lock_b.generation_id}.sentinel").is_file()
    reconciled = {item.request_id: item for item in lifecycle.reconcile()}
    assert reconciled["publish-b"].status == "ready"
    assert lifecycle.prepare_and_publish(
        "main", lock_b, "b.yaml", 0, "publish-b"
    ).generation_id == lock_b.generation_id


def test_unknown_cleanup_retains_capacity_pin(tmp_path):
    preparer = UnknownCleanupPreparer(tmp_path / "resources")
    preparer.root.mkdir()
    lifecycle = GenerationLifecycle(tmp_path, preparer)
    lock_a, lock_b, lock_c = (_lock("a"), _lock("b"), _lock("c"))
    publication_a = lifecycle.prepare_and_publish(
        "main", lock_a, "a.yaml", 0, "publish-a"
    )
    admission_a = lifecycle.mark_materialized(
        lifecycle.reserve_target_admission(
            "main", "session-a", "input-a"
        ).admission_id
    )
    publication_b = lifecycle.prepare_and_publish(
        "main", lock_b, "b.yaml", publication_a.revision, "publish-b"
    )
    lifecycle.release(admission_a.admission_id, cleanup_confirmed=True)

    with pytest.raises(GenerationLifecycleError) as pressure:
        lifecycle.prepare_and_publish(
            "main", lock_c, "c.yaml", publication_b.revision, "publish-c"
        )
    assert pressure.value.code == "capacity_pressure"
    assert (preparer.root / f"{lock_a.generation_id}.sentinel").is_file()
    assert (preparer.root / f"{lock_b.generation_id}.sentinel").is_file()
    assert not (preparer.root / f"{lock_c.generation_id}.sentinel").exists()
