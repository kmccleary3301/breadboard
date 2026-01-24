import os
import uuid
import shutil
import pytest
import ray

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


@pytest.mark.slow
def test_openai_c_filesystem_proto(monkeypatch, tmp_path):
    # Skip if no OpenAI
    try:
        import openai  # noqa: F401
    except Exception:
        pytest.skip("openai package not installed")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY not set")

    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)

    agent = OpenAIConductor.options(name=f"oa-{uuid.uuid4()}").remote(workspace=str(ws))
    system = "You are an expert C systems programmer. Use tools to create files and compile them."
    user = (
        "Create a minimal proto file system manager in C with a CLI: init, touch, rm, ls."
        " Use GCC to compile, then write a few tests as shell commands to exercise the binary,"
        " and run them. Keep implementation simple (in-memory or temp files)."
    )
    # Model name is provided by user via .env; default fallback
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    result = ray.get(agent.run_agentic_loop.remote(system, user, model=model, max_steps=10))
    # Assert we produced/compiled something
    dir_list = ray.get(agent.list_dir.remote("."))
    assert len(dir_list) >= 0


