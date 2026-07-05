from __future__ import annotations

from breadboard.rl.runtime.pool import ASSIGNED, QUARANTINED, READY, RuntimePool, WorkerRecord


def test_pool_routes_exact_ready_signature() -> None:
    pool = RuntimePool()
    pool.register(WorkerRecord(worker_id="w1", signature_digest="sig-a"))
    pool.register(WorkerRecord(worker_id="w2", signature_digest="sig-b"))

    worker = pool.route("sig-a")

    assert worker is not None
    assert worker.worker_id == "w1"
    assert worker.state == ASSIGNED
    pool.release("w1")
    assert pool.workers["w1"].state == READY


def test_pool_does_not_route_quarantined_worker() -> None:
    pool = RuntimePool()
    pool.register(WorkerRecord(worker_id="w1", signature_digest="sig-a"))
    pool.quarantine("w1", "poisoned")

    assert pool.route("sig-a") is None
    assert pool.workers["w1"].state == QUARANTINED
