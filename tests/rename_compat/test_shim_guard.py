"""R-0C6: post-rename shim allowlist guard (activates at R-1).

Once the engine package is renamed, ``agentic_coder_prototype/`` may contain
only the explicit compatibility allowlist. Pre-rename (identity epoch) the
guard is armed but dormant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_coder_prototype.compat import legacy_names as ln

REPO_ROOT = Path(__file__).resolve().parents[2]

SHIM_ALLOWLIST = {
    "__init__.py",
    "compat",  # legacy_names.py / alias_import.py live on in the shim
    "py.typed",
}


def test_shim_directory_restricted_to_allowlist():
    if ln.CANONICAL_PACKAGE == "agentic_coder_prototype":
        pytest.skip("pre-rename identity epoch; guard activates at R-1")
    shim_dir = REPO_ROOT / "agentic_coder_prototype"
    assert shim_dir.is_dir(), "post-rename shim package missing"
    entries = {
        p.name
        for p in shim_dir.iterdir()
        if p.name != "__pycache__" and not p.name.startswith(".")
    }
    unexpected = entries - SHIM_ALLOWLIST
    assert not unexpected, f"non-allowlisted files in shim package: {sorted(unexpected)}"


def test_shim_template_shape():
    """The checked-in R-1 shim template must enforce root policy, install the
    prefix-wide finder, and self-replace with the canonical package."""
    template = REPO_ROOT / "scripts/dev/shim_package_init.template.py"
    text = template.read_text(encoding="utf-8")
    assert 'announce_root_import("agentic_coder_prototype")' in text
    assert 'install("agentic_coder_prototype", "breadboard_engine")' in text
    assert "sys.modules[__name__] = _canonical" in text


def test_installed_shim_matches_template():
    if ln.CANONICAL_PACKAGE == "agentic_coder_prototype":
        pytest.skip("pre-rename identity epoch; shim installed at R-1")
    template = REPO_ROOT / "scripts/dev/shim_package_init.template.py"
    installed = REPO_ROOT / "agentic_coder_prototype/__init__.py"
    assert installed.read_bytes() == template.read_bytes(), (
        "installed shim drifted from scripts/dev/shim_package_init.template.py"
    )
