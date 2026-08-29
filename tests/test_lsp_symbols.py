import uuid
import ray
import pytest

from breadboard.lsp_manager import LSPManager


@pytest.fixture(scope="module")
def ray_cluster():
    ray.init()
    yield
    ray.shutdown()

@pytest.fixture(scope="module")
def lsp_manager(ray_cluster):
    actor = LSPManager.remote()
    yield actor
    ray.get(actor.shutdown.remote())


def test_workspace_and_document_symbols(ray_cluster, lsp_manager, tmp_path):
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "m.py"
    p.write_text("""
class Foo:
    def a(self):
        pass

def bar():
    pass
""")

    ray.get(lsp_manager.register_root.remote(str(ws)))
    # workspace symbols
    syms = ray.get(lsp_manager.workspace_symbol.remote("ba"))
    assert any(s["name"] == "bar" for s in syms)
    # document symbols
    ds = ray.get(lsp_manager.document_symbol.remote(str(p)))
    names = {s["name"] for s in ds}
    assert {"Foo", "bar"}.issubset(names)


def test_hover_basic(ray_cluster, lsp_manager, tmp_path):
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "m.py"
    p.write_text("""
def fn():
    return 1
""")
    ray.get(lsp_manager.register_root.remote(str(ws)))
    # hover on 'fn'
    hv = ray.get(lsp_manager.hover.remote(str(p), 2, 6))
    assert hv and hv.get("contents")

