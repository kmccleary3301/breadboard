from __future__ import annotations

import json

from agentic_coder_prototype.longrun.queue import FeatureFileQueue, TodoStoreQueue, build_work_queue
from agentic_coder_prototype.todo.store import TodoDraft, TodoStore


def test_todo_store_queue_claim_and_complete(tmp_path) -> None:
    store = TodoStore(str(tmp_path), load_existing=False)
    created = store.create([TodoDraft(title="Implement parser"), TodoDraft(title="Write tests")])
    queue = TodoStoreQueue(store=store)

    first = queue.peek_next()
    assert first is not None
    assert first["id"] == created[0].id
    assert first["status"] == "todo"

    queue.claim(created[0].id, episode_id=3)
    claimed = store.get(created[0].id)
    assert claimed is not None
    assert claimed.status == "in_progress"
    assert claimed.metadata["longrun_last_claim_episode"] == 3

    queue.complete(created[0].id, {"completion_reason": "all_checks_passed"})
    done = store.get(created[0].id)
    assert done is not None
    assert done.status == "done"


def test_todo_store_queue_defer_marks_blocked(tmp_path) -> None:
    store = TodoStore(str(tmp_path), load_existing=False)
    created = store.create([TodoDraft(title="Investigate flaky test")])
    queue = TodoStoreQueue(store=store)

    queue.defer(created[0].id, reason="waiting_on_dependency")
    todo = store.get(created[0].id)
    assert todo is not None
    assert todo.status == "blocked"
    assert todo.metadata["longrun_defer_reason"] == "waiting_on_dependency"


def test_feature_file_queue_flow(tmp_path) -> None:
    path = tmp_path / "feature_queue.json"
    payload = {
        "version": 1,
        "items": [
            {"id": "f1", "title": "Task A", "status": "todo", "metadata": {}},
            {"id": "f2", "title": "Task B", "status": "blocked", "metadata": {}},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    queue = FeatureFileQueue(path)

    item = queue.peek_next()
    assert item is not None
    assert item["id"] == "f1"

    queue.claim("f1", episode_id=1)
    snapshot = queue.snapshot()
    first = snapshot["items"][0]
    assert first["status"] == "in_progress"
    assert first["metadata"]["longrun_last_claim_episode"] == 1

    queue.defer("f1", "verification_failed")
    snapshot = queue.snapshot()
    first = snapshot["items"][0]
    assert first["status"] == "blocked"
    assert first["metadata"]["longrun_defer_reason"] == "verification_failed"

    queue.complete("f1", {"completion_reason": "verified"})
    snapshot = queue.snapshot()
    first = snapshot["items"][0]
    assert first["status"] == "done"
    assert first["metadata"]["longrun_completion_reason"] == "verified"


def test_build_work_queue_selects_todo_store_backend(tmp_path) -> None:
    store = TodoStore(str(tmp_path), load_existing=False)
    store.create([TodoDraft(title="Queue build test")])
    manager = type("TodoManagerStub", (), {"store": store})()
    cfg = {"long_running": {"queue": {"backend": "todo_store"}}}
    queue = build_work_queue(cfg, workspace=str(tmp_path), todo_manager=manager)
    assert isinstance(queue, TodoStoreQueue)
    assert queue.peek_next() is not None


def test_build_work_queue_selects_feature_file_backend(tmp_path) -> None:
    cfg = {"long_running": {"queue": {"backend": "feature_file"}}}
    queue = build_work_queue(cfg, workspace=str(tmp_path))
    assert isinstance(queue, FeatureFileQueue)
    assert queue.snapshot()["backend"] == "feature_file"
