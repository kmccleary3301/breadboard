import os
import uuid
import ray
import pytest

from agent_session import OpenCodeAgent


@pytest.fixture(scope="module")
def ray_cluster():
    ray.init()
    yield
    ray.shutdown()


def test_agent_persist_messages(ray_cluster, tmp_path):
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    store = tmp_path / f"store-{uuid.uuid4()}"
    agent = OpenCodeAgent.options(name=f"ag-{uuid.uuid4()}").remote(workspace=str(ws))
    ray.get(agent.enable_storage.remote(str(store)))
    msg = ray.get(agent.run_message.remote([
        {"type": "tool_call", "name": "write_text", "args": {"path": "a.py", "content": "x=1\n"}},
    ]))
    # Persist and verify
    ray.get(agent.persist_message.remote(msg))
    # Check on disk
    found = list(store.rglob("*.json"))
    assert found, "Expected persisted message files"


