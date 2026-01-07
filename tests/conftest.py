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

# Note: keep project root on sys.path so tests can import the `breadboard` package.

# Ensure default docker runtime is standard runc during tests unless overridden
os.environ.setdefault("RAY_DOCKER_RUNTIME", "runc")

# Force tests to start a local Ray cluster on uncommon ports to avoid collisions
# with other projects that might be running a global cluster on this machine.
os.environ.setdefault("RAY_ADDRESS", "local")
os.environ.setdefault("RAY_DASHBOARD_PORT", "8299")

def pytest_ignore_collect(path, config):
    """Completely ignore industry reference test suites by default.
    Set INCLUDE_INDUSTRY_REFS_TESTS=1 to include them.
    """
    include_industry = os.environ.get("INCLUDE_INDUSTRY_REFS_TESTS", "0") == "1"
    p = str(path)
    if not include_industry and "/industry_coder_refs/" in p:
        return True
    return False

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
