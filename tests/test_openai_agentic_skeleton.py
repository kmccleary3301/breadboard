import os
import uuid
import shutil
import pytest
import ray

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


@pytest.fixture(scope="module")
def ray_cluster():
    ray.init()
    yield
    ray.shutdown()


def test_skeleton_requires_env(ray_cluster, tmp_path):
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    agent = OpenAIConductor.options(name=f"oa-{uuid.uuid4()}").remote(workspace=str(ws))
    # Will skip if no openai installed or no key
    if shutil.which("python") is None:
        pytest.skip("env not ready")
    if os.getenv("OPENAI_API_KEY") is None:
        with pytest.raises(RuntimeError):
            ray.get(agent.run_agentic_loop.remote("sys", "usr", model="gpt-4o-mini"))


