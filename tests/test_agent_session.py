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


def test_agent_write_and_diagnostics(ray_cluster, tmp_path):
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)

    agent = OpenCodeAgent.options(name=f"ag-{uuid.uuid4()}").remote(workspace=str(ws))
    # write a file with syntax error
    msg = ray.get(
        agent.run_message.remote(
            [
                {"type": "tool_call", "name": "write_text", "args": {"path": "bad.py", "content": "def x(:\n pass\n"}},
            ]
        )
    )
    parts = msg["response"]
    assert parts and parts[0]["type"] == "tool_result"
    diags = parts[0]["metadata"]["diagnostics"]
    assert any(p.endswith("bad.py") for p in diags.keys())


def test_agent_patch_flow(ray_cluster, tmp_path):
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)

    agent = OpenCodeAgent.options(name=f"ag-{uuid.uuid4()}").remote(workspace=str(ws))
    # seed file
    ray.get(
        agent.run_message.remote([
            {"type": "tool_call", "name": "write_text", "args": {"path": "a.txt", "content": "hello\n"}},
        ])
    )
    # create a diff locally and apply via agent
    before = (ws / "a.txt").read_text()
    (ws / "a.txt").write_text("hello world\n")
    # naive diff
    patch = f"""--- a/a.txt\n+++ b/a.txt\n@@\n-{before}+hello world\n"""
    msg = ray.get(agent.run_message.remote([
        {"type": "tool_call", "name": "apply_patch", "args": {"patch": patch, "three_way": True}},
    ]))
    parts = msg["response"]
    assert parts and parts[0]["type"] == "tool_result"
    out = parts[0]["output"]
    assert out["action"] == "apply_patch"


