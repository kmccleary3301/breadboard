import json
from pathlib import Path

from agentic_coder_prototype.todo import TodoDraft, TodoManager, TodoPatch, TodoStore


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def load_persisted(workspace: Path):
    data_path = workspace / TodoStore.FILENAME
    assert data_path.exists()
    return json.loads(data_path.read_text())


def test_create_and_persist(tmp_path):
    workspace = make_workspace(tmp_path)
    store = TodoStore(str(workspace))
    created = store.create([TodoDraft(title="Add unit tests")])
    assert created[0].title == "Add unit tests"
    assert store.list_items()[0].status == "todo"
    persisted = load_persisted(workspace)
    assert persisted["todos"][0]["title"] == "Add unit tests"


def test_update_and_events(tmp_path):
    workspace = make_workspace(tmp_path)
    store = TodoStore(str(workspace))
    todo = store.create([TodoDraft(title="Implement feature")])[0]
    store.update(todo.id, TodoPatch(status="in_progress"))
    store.complete(todo.id, summary="All good")
    events = store.recent_events(3)
    types = [event.type for event in events]
    assert "todo.updated" in types
    assert "todo.completed" in types
    snapshot = store.snapshot()
    assert snapshot["todos"][0]["status"] == "done"


def test_reload_existing_state(tmp_path):
    workspace = make_workspace(tmp_path)
    store = TodoStore(str(workspace))
    todo = store.create([TodoDraft(title="Plan work")])[0]
    store.update(todo.id, TodoPatch(priority="P1"))
    reloaded = TodoStore(str(workspace))
    todos = reloaded.list_items()
    assert todos[0].priority == "P1"


def test_todo_write_board_sync(tmp_path):
    workspace = make_workspace(tmp_path)
    store = TodoStore(str(workspace))
    manager = TodoManager(store, lambda event: None)

    manager.handle_write_board({
        "todos": [
            {"content": "Design header", "status": "pending"},
            {"content": "Implement core", "status": "in_progress", "activeForm": "Implementing core"},
        ]
    })
    todos = store.list_items()
    assert [todo.title for todo in todos] == ["Design header", "Implement core"]
    assert todos[1].status == "in_progress"
    assert todos[1].metadata.get("active_form") == "Implementing core"

    manager.handle_write_board({
        "todos": [
            {"content": "Implement core", "status": "completed"},
        ]
    })
    todos = store.list_items()
    assert len(todos) == 1
    assert todos[0].title == "Implement core"
    assert todos[0].status == "done"
