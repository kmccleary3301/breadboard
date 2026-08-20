"""R-0C2/R-0C3/R-0C4: prefix-wide alias finder compatibility matrix.

The finder is exercised against a synthetic package pair
(``bb_alias_demo_old`` -> ``bb_alias_demo_new``) because the real engine
epoch is an identity mapping until R-1. Post-rename the same machinery is
installed by the ``agentic_coder_prototype`` shim for the real pair, and this
matrix keeps proving the behaviors.
"""

from __future__ import annotations

import importlib
import importlib.resources
import importlib.util
import os
import pickle
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from agentic_coder_prototype.compat import alias_import
from agentic_coder_prototype.compat import legacy_names as ln

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD = "bb_alias_demo_old"
NEW = "bb_alias_demo_new"

DEMO_FILES = {
    "__init__.py": "VERSION = 'demo'\n",
    "__main__.py": "print('DEMO_MAIN_OK', __name__)\n",
    "core.py": textwrap.dedent(
        """
        class Widget:
            def __init__(self, tag):
                self.tag = tag

        def make(tag):
            return Widget(tag)
        """
    ),
    "registry.py": "REGISTRY = {}\n\ndef register(key, value):\n    REGISTRY[key] = value\n",
    "wrapper.py": textwrap.dedent(
        """
        # Replicates the provider_runtime.py self-replacing wrapper pattern.
        from importlib import import_module
        import sys

        _module = import_module('.core', __package__)
        sys.modules[__name__] = _module
        """
    ),
    "cyc_a.py": textwrap.dedent(
        """
        from . import cyc_b

        A = 'a'

        def combo():
            return cyc_b.B + A
        """
    ),
    "cyc_b.py": textwrap.dedent(
        """
        from . import cyc_a  # circular: cyc_a is mid-initialization here

        B = 'b'

        def get_a():
            return cyc_a.A
        """
    ),
    "data.txt": "alias-resource-sentinel\n",
    "deep/__init__.py": "",
    "deep/inner.py": "class DeepThing:\n    pass\n",
}

BOOTSTRAP = textwrap.dedent(
    f"""
    import os, sys

    def ensure():
        here = os.path.dirname(os.path.abspath(__file__))
        for entry in (here, {str(REPO_ROOT)!r}):
            if entry not in sys.path:
                sys.path.insert(0, entry)
        from agentic_coder_prototype.compat import alias_import
        return alias_import.install({OLD!r}, {NEW!r})

    def spawn_probe(queue):
        ensure()
        import {OLD}.core as old_core
        import {NEW}.core as new_core
        queue.put((old_core is new_core, type(old_core.make('t')).__module__))
    """
)


@pytest.fixture(autouse=True)
def _default_allow_policy(monkeypatch):
    """Matrix tests exercise legacy imports by design; pin the default policy
    so an ambient BREADBOARD_LEGACY_IMPORTS=error run doesn't reject them.
    Policy-behavior tests override this via their own monkeypatch."""
    monkeypatch.delenv(alias_import.POLICY_ENV, raising=False)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("alias_demo")
    pkg = root / NEW
    for rel, content in DEMO_FILES.items():
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / "_bb_alias_bootstrap.py").write_text(BOOTSTRAP, encoding="utf-8")
    sys.path.insert(0, str(root))
    finder = alias_import.install(OLD, NEW)
    try:
        yield {"root": root, "finder": finder}
    finally:
        alias_import.uninstall(finder)
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m.split(".")[0] in {OLD, NEW}]:
            del sys.modules[name]


def subprocess_env(demo) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(demo["root"]), str(REPO_ROOT)])
    env.pop(alias_import.POLICY_ENV, None)
    return env


def run_py(demo, code: str, *, timeout=120, env_extra=None) -> subprocess.CompletedProcess:
    env = subprocess_env(demo)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=demo["root"],
    )


class TestIdentity:
    def test_old_first_identity(self, demo):
        proc = run_py(
            demo,
            "import _bb_alias_bootstrap as b; b.ensure();"
            f"import {OLD}.core as o; import {NEW}.core as n;"
            "assert o is n, 'old-first identity broken'; print('OK')",
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout

    def test_new_first_identity(self, demo):
        proc = run_py(
            demo,
            "import _bb_alias_bootstrap as b; b.ensure();"
            f"import {NEW}.core as n; import {OLD}.core as o;"
            "assert o is n, 'new-first identity broken'; print('OK')",
        )
        assert proc.returncode == 0, proc.stderr

    def test_deep_submodule_identity(self, demo):
        old_mod = importlib.import_module(f"{OLD}.deep.inner")
        new_mod = importlib.import_module(f"{NEW}.deep.inner")
        assert old_mod is new_mod
        assert old_mod.DeepThing is new_mod.DeepThing

    def test_class_identity(self, demo):
        old_core = importlib.import_module(f"{OLD}.core")
        new_core = importlib.import_module(f"{NEW}.core")
        assert old_core.Widget is new_core.Widget
        assert isinstance(old_core.make("t"), new_core.Widget)

    def test_registry_singleton_identity(self, demo):
        old_reg = importlib.import_module(f"{OLD}.registry")
        new_reg = importlib.import_module(f"{NEW}.registry")
        old_reg.register("k", "v")
        assert new_reg.REGISTRY["k"] == "v"
        assert old_reg.REGISTRY is new_reg.REGISTRY

    def test_canonical_metadata_preserved(self, demo):
        mod = importlib.import_module(f"{OLD}.core")
        assert mod.__name__ == f"{NEW}.core"
        assert mod.__spec__.name == f"{NEW}.core"
        assert sys.modules[f"{OLD}.core"] is sys.modules[f"{NEW}.core"]


class TestMachinery:
    def test_reload_keeps_identity(self, demo):
        mod = importlib.import_module(f"{OLD}.registry")
        reloaded = importlib.reload(mod)
        assert reloaded is sys.modules[f"{NEW}.registry"]
        assert sys.modules[f"{OLD}.registry"] is reloaded

    def test_concurrent_import_single_object(self, demo):
        sys.modules.pop(f"{OLD}.cyc_b", None)
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(importlib.import_module(f"{OLD}.cyc_b"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len({id(m) for m in results}) == 1
        assert results[0] is sys.modules[f"{NEW}.cyc_b"]

    def test_circular_import_fixture(self, demo):
        cyc_a = importlib.import_module(f"{OLD}.cyc_a")
        assert cyc_a.combo() == "ba"
        assert cyc_a is sys.modules[f"{NEW}.cyc_a"]

    def test_find_spec_both_prefixes(self, demo):
        old_spec = importlib.util.find_spec(f"{OLD}.deep.inner")
        new_spec = importlib.util.find_spec(f"{NEW}.deep.inner")
        assert old_spec is not None and new_spec is not None
        assert old_spec.origin == new_spec.origin

    def test_package_resources(self, demo):
        text = (
            importlib.resources.files(OLD).joinpath("data.txt").read_text(encoding="utf-8")
        )
        assert text.strip() == "alias-resource-sentinel"

    def test_runpy_module_both_paths(self, demo):
        for name in (OLD, NEW):
            proc = run_py(
                demo,
                "import _bb_alias_bootstrap as b; b.ensure(); import runpy;"
                f"runpy.run_module({name!r}, run_name='__main__')",
            )
            assert proc.returncode == 0, (name, proc.stderr)
            assert "DEMO_MAIN_OK" in proc.stdout, name

    def test_pickle_legacy_global_opcode(self, demo):
        # Simulates a pre-rename pickle: GLOBAL opcode names the legacy module.
        cls = pickle.loads(f"c{OLD}.core\nWidget\n.".encode("ascii"))
        new_core = importlib.import_module(f"{NEW}.core")
        assert cls is new_core.Widget

    def test_pickle_round_trip(self, demo):
        old_core = importlib.import_module(f"{OLD}.core")
        obj = pickle.loads(pickle.dumps(old_core.make("t7"), protocol=4))
        assert obj.tag == "t7"
        assert type(obj).__module__ == f"{NEW}.core"

    def test_self_replacing_wrapper_composition(self, demo):
        wrapper = importlib.import_module(f"{OLD}.wrapper")
        core = importlib.import_module(f"{NEW}.core")
        assert wrapper is core
        assert sys.modules[f"{OLD}.wrapper"] is core
        assert sys.modules[f"{NEW}.wrapper"] is core

    def test_engine_root_wrapper_composition(self, demo):
        # R-0C4 on the real engine surface (epoch-aware): the self-replacing
        # wrapper parks the canonical module under the canonical key, and the
        # legacy dotted path resolves to the same object in every epoch.
        import agentic_coder_prototype.provider_runtime as pr

        assert pr is sys.modules[f"{ln.CANONICAL_PACKAGE}.provider.runtime"]
        via_old = importlib.import_module("agentic_coder_prototype.provider.runtime")
        assert via_old is pr


class TestPolicy:
    def test_default_allow_is_silent(self, demo, recwarn):
        sys.modules.pop(f"{OLD}.deep", None)
        importlib.import_module(f"{OLD}.deep")
        assert not [w for w in recwarn.list if issubclass(w.category, DeprecationWarning)]

    def test_warn_warns_once_per_process(self, demo, monkeypatch):
        monkeypatch.setenv(alias_import.POLICY_ENV, "warn")
        alias_import._warned_processes.discard(OLD)
        sys.modules.pop(f"{OLD}.cyc_b", None)
        with pytest.warns(DeprecationWarning):
            importlib.import_module(f"{OLD}.cyc_b")
        sys.modules.pop(f"{OLD}.cyc_b", None)
        import warnings as _w

        with _w.catch_warnings(record=True) as second:
            _w.simplefilter("always")
            importlib.import_module(f"{OLD}.cyc_b")
        assert not [w for w in second if issubclass(w.category, DeprecationWarning)]

    def test_error_rejects_legacy_import(self, demo, monkeypatch):
        monkeypatch.setenv(alias_import.POLICY_ENV, "error")
        sys.modules.pop(f"{OLD}.deep", None)
        sys.modules.pop(f"{OLD}.deep.inner", None)
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"{OLD}.deep.inner")

    def test_error_leaves_canonical_untouched(self, demo, monkeypatch):
        monkeypatch.setenv(alias_import.POLICY_ENV, "error")
        importlib.import_module(f"{NEW}.core")

    def test_invalid_policy_rejected(self, demo, monkeypatch):
        monkeypatch.setenv(alias_import.POLICY_ENV, "bogus")
        with pytest.raises(ValueError):
            alias_import.current_policy()


class TestProcessModels:
    def test_multiprocessing_spawn(self, demo):
        proc = run_py(
            demo,
            textwrap.dedent(
                """
                import _bb_alias_bootstrap as b
                b.ensure()
                import multiprocessing as mp
                ctx = mp.get_context('spawn')
                q = ctx.Queue()
                p = ctx.Process(target=b.spawn_probe, args=(q,))
                p.start()
                identity, module = q.get(timeout=60)
                p.join(30)
                assert identity, 'spawn child lost alias identity'
                assert module == '%s.core', module
                print('SPAWN_OK')
                """
                % NEW
            ),
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        assert "SPAWN_OK" in proc.stdout

    def test_ray_serialization_smoke(self, demo):
        proc = run_py(
            demo,
            textwrap.dedent(
                """
                import _bb_alias_bootstrap as b
                b.ensure()
                import ray
                import bb_alias_demo_old.core as old_core
                ray.init(num_cpus=1, include_dashboard=False, log_to_driver=False,
                         ignore_reinit_error=True)
                try:
                    obj = old_core.make('ray-tag')
                    got = ray.get(ray.put(obj))
                    assert got.tag == 'ray-tag'
                    assert type(got).__module__ == 'bb_alias_demo_new.core', type(got).__module__

                    @ray.remote
                    def probe(widget):
                        return type(widget).__module__, widget.tag

                    module, tag = ray.get(probe.remote(obj))
                    assert module == 'bb_alias_demo_new.core' and tag == 'ray-tag'
                    print('RAY_OK')
                finally:
                    ray.shutdown()
                """
            ),
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr
        assert "RAY_OK" in proc.stdout
