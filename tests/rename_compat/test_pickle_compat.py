"""R-0B6: trusted pre-rename pickle fixtures load under compatibility mode.

The by-reference fixtures (`class_ref.pkl`, `function_ref.pkl`) embed the
dotted module path inside the pickle stream. Post-rename they only load if
the shim/alias layer keeps the legacy module names importable, which is
exactly the contract this suite locks.
"""

from __future__ import annotations

import hashlib
import io
import json
import pickle
from pathlib import Path

import pytest

from agentic_coder_prototype.compat import legacy_names as ln

FIXTURES = Path(__file__).resolve().parent / "fixtures"
META = json.loads((FIXTURES / "pickle_fixtures.json").read_text(encoding="utf-8"))


class _CompatUnpickler(pickle.Unpickler):
    """Unpickler that routes legacy module names through the alias layer."""

    def find_class(self, module: str, name: str):
        return super().find_class(ln.canonical_dotted(module), name)


def compat_load(payload: bytes):
    return _CompatUnpickler(io.BytesIO(payload)).load()


@pytest.mark.parametrize("name", sorted(META))
def test_fixture_bytes_unchanged(name):
    payload = (FIXTURES / name).read_bytes()
    assert "sha256:" + hashlib.sha256(payload).hexdigest() == META[name]["sha256"]


def test_instance_fixture_loads(self=None):
    obj = compat_load((FIXTURES / "redaction_problem.pkl").read_bytes())
    dotted = f"{type(obj).__module__}.{type(obj).__qualname__}"
    assert ln.canonical_dotted(META["redaction_problem.pkl"]["type"]) == ln.canonical_dotted(dotted)


def test_class_ref_fixture_loads_canonical_class():
    cls = compat_load((FIXTURES / "class_ref.pkl").read_bytes())
    assert isinstance(cls, type)
    assert cls.__module__.split(".")[0] == ln.CANONICAL_PACKAGE
    from agentic_coder_prototype.compilation.primitive_records import PrimitiveCompileError

    assert cls is PrimitiveCompileError


def test_function_ref_fixture_loads_canonical_function():
    fn = compat_load((FIXTURES / "function_ref.pkl").read_bytes())
    assert callable(fn)
    from agentic_coder_prototype.compilation.effective_config_graph import graph_content_hash

    assert fn is graph_content_hash
