import uuid
import ray
import pytest

from sandbox_v2 import new_dev_sandbox_v2
from lsp_manager import LSPManager


@pytest.fixture(scope="module")
def ray_cluster():
    ray.init()
    yield
    ray.shutdown()


def test_python_syntax_diagnostics(ray_cluster, tmp_path):
    # Local workspace fallback
    import os
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"

    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)

    lsp = LSPManager.remote()
    ray.get(lsp.register_root.remote(str(ws)))

    # Pass LSP actor to sandbox
    sb = new_dev_sandbox_v2("python-dev:latest", str(ws), name=f"sb-{uuid.uuid4()}")

    # Monkeypatch: attach lsp actor to existing actor (simulate ctor param)
    # In real code we would pass lsp in ctor; Ray doesn’t easily support after-construction attribute set,
    # but we can call private method by executing python one-liner in run(). Here we re-create actor with lsp.
    # For test simplicity, just create a new actor with lsp param via low-level API.

    # Low-level replacement: kill prior actor and create new with lsp
    ray.kill(sb)
    from sandbox_v2 import DevSandboxV2
    sb = DevSandboxV2.options(name=f"sb-{uuid.uuid4()}").remote(image="python-dev:latest", workspace=str(ws), lsp_actor=lsp)

    # Write a file with a syntax error
    ray.get(sb.write_text.remote("bad.py", "def f(:\n  pass\n"))
    diags = ray.get(lsp.diagnostics.remote())
    # Expect an error entry for bad.py
    bad_path = str(ws / "bad.py")
    assert bad_path in diags and diags[bad_path], diags


def test_pyright_cli_if_available(ray_cluster, tmp_path):
    import shutil
    if shutil.which("pyright") is None and shutil.which("pyright-langserver") is None:
        pytest.skip("pyright not available")

    os = __import__("os")
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "pyproject.toml").write_text("[tool.pyright]\n")
    bad = ws / "b.py"
    bad.write_text("def g(:\n  pass\n")

    lsp = LSPManager.remote()
    ray.get(lsp.register_root.remote(str(ws)))
    ray.get(lsp.touch_file.remote(str(bad), True))
    diags = ray.get(lsp.diagnostics.remote())
    assert str(bad) in diags and diags[str(bad)]


