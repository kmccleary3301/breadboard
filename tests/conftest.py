import pytest


def pytest_configure(config):
    # Register custom marks used by some tests to silence warnings
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )

import sys, os

# Ensure project root is on sys.path so legacy test imports work
ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(ROOT, os.pardir))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
if ROOT not in sys.path:
    # tests/ itself: several suites import sibling helper modules top-level
    # (e.g. atp_hilbert_fixture_utils) despite tests/ being a package.
    sys.path.insert(1, ROOT)

# Note: keep project root on sys.path so tests can import the `breadboard` package.

# Ensure default docker runtime is standard runc during tests unless overridden
os.environ.setdefault("RAY_DOCKER_RUNTIME", "runc")

# Force tests to start a local Ray cluster on uncommon ports to avoid collisions
# with other projects that might be running a global cluster on this machine.
os.environ.setdefault("RAY_ADDRESS", "local")
os.environ.setdefault("RAY_DASHBOARD_PORT", "8299")

def pytest_ignore_collect(collection_path, config):
    """Completely ignore industry reference test suites by default.
    Set INCLUDE_INDUSTRY_REFS_TESTS=1 to include them.
    """
    include_industry = os.environ.get("INCLUDE_INDUSTRY_REFS_TESTS", "0") == "1"
    p = str(collection_path)
    if not include_industry and "/industry_coder_refs/" in p:
        return True
    return None

# Skip LSP-heavy tests when flagged (avoids long waits and external tool deps)
_SKIP_LSP = os.environ.get("RAY_SCE_SKIP_LSP", "0") == "1"
_LSP_FILES = {
    "tests/test_lsp_integration.py",
    "tests/test_lsp_manager_v2.py",
    "tests/test_lsp_symbols.py",
    "tests/test_sandbox_lsp_integration.py",
    # Agent session/storage tests rely on LSP actors; skip under LSP skip flag
    "tests/test_agent_session.py",
    "tests/test_agent_storage.py",
    # LSP deployment suite depends on sandbox_lsp_integration; skip under flag
    "tests/test_lsp_deployment.py",
}

def pytest_collection_modifyitems(session, config, items):
    if not _SKIP_LSP:
        # Still allow other global collection filters below
        pass
    # Skip LSP-heavy tests when flagged
    if _SKIP_LSP:
        skip_marker = pytest.mark.skip(reason="Skipping LSP tests (RAY_SCE_SKIP_LSP=1)")
        for item in items:
            fpath = str(item.fspath)
            for lsp in _LSP_FILES:
                if fpath.endswith(lsp):
                    item.add_marker(skip_marker)
                    break
    # By default, skip industry reference suites unless explicitly included
    include_industry = os.environ.get("INCLUDE_INDUSTRY_REFS_TESTS", "0") == "1"
    if not include_industry:
        skip_industry = pytest.mark.skip(reason="Skipping industry_coder_refs tests by default (set INCLUDE_INDUSTRY_REFS_TESTS=1 to enable)")
        for item in items:
            if "/industry_coder_refs/" in str(item.fspath):
                item.add_marker(skip_industry)


@pytest.fixture
def installed_wheel_python(tmp_path):
    """Run consumers in an explicitly supplied, isolated wheel installation."""
    import subprocess
    import textwrap
    from pathlib import Path

    interpreter = os.environ.get("BREADBOARD_INSTALLED_WHEEL_PYTHON")
    if not interpreter:
        pytest.skip("Set BREADBOARD_INSTALLED_WHEEL_PYTHON to an isolated wheel venv Python")
    interpreter = str(Path(interpreter).absolute())
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for helper in ("PATCH", "SEARCH", "IMAGE"):
        environment.pop(f"BREADBOARD_CODEX_{helper}_BINARY", None)
    environment["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    provenance = textwrap.dedent(f"""
        import json
        import sys
        from pathlib import Path
        from importlib import metadata
        import breadboard
        import breadboard_engine.e4_targets as target_loader
        from breadboard_engine.execution import codex_native

        checkout = Path({PROJ!r}).resolve()
        prefix = Path(sys.prefix).resolve()
        assert sys.prefix != sys.base_prefix, "Wheel proof requires a venv"
        venv_config = (prefix / "pyvenv.cfg").read_text().lower()
        assert "include-system-site-packages = false" in venv_config
        assert not Path.cwd().resolve().is_relative_to(checkout)
        distribution = metadata.distribution("breadboard-harness-cli")
        direct_url = json.loads(distribution.read_text("direct_url.json") or "{{}}")
        assert not direct_url.get("dir_info", {{}}).get("editable", False)
        assert direct_url.get("archive_info") is not None, "Install the candidate wheel artifact directly"
        assert direct_url.get("url", "").endswith(".whl"), direct_url
        for module in (breadboard, target_loader, codex_native):
            origin = Path(module.__file__).resolve()
            assert origin.is_relative_to(prefix), (module.__name__, origin, prefix)
            assert not origin.is_relative_to(checkout), (module.__name__, origin)
        assert Path(distribution.locate_file("breadboard_engine/e4_targets.py")).resolve() == Path(target_loader.__file__).resolve()
    """)

    def run(script):
        result = subprocess.run(
            [interpreter, "-I", "-c", provenance + "\n" + textwrap.dedent(script)],
            cwd=outside,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    return run
