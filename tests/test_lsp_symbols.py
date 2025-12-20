import uuid
import ray
import pytest

from lsp_manager import LSPManager


@pytest.fixture(scope="module")
def ray_cluster():
    ray.init()
    yield
    ray.shutdown()


def test_workspace_and_document_symbols(ray_cluster, tmp_path):
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

    lsp = LSPManager.remote()
    ray.get(lsp.register_root.remote(str(ws)))
    # workspace symbols
    syms = ray.get(lsp.workspace_symbol.remote("ba"))
    assert any(s["name"] == "bar" for s in syms)
    # document symbols
    ds = ray.get(lsp.document_symbol.remote(str(p)))
    names = {s["name"] for s in ds}
    assert {"Foo", "bar"}.issubset(names)


def test_hover_basic(ray_cluster, tmp_path):
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "m.py"
    p.write_text("""
def fn():
    return 1
""")
    lsp = LSPManager.remote()
    # hover on 'fn'
    hv = ray.get(lsp.hover.remote(str(p), 2, 6))
    assert hv and hv.get("contents")


