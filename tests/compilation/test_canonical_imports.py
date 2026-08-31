from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_COMPILER_FILES = (
    ROOT / "breadboard_engine/compilation/bundle.py",
    ROOT / "breadboard_engine/compilation/server_compiler.py",
)


@pytest.mark.parametrize("path", CANONICAL_COMPILER_FILES, ids=lambda path: path.name)
def test_canonical_compiler_does_not_import_through_legacy_namespace(
    path: Path,
) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module == "agentic_coder_prototype"
        or module.startswith("agentic_coder_prototype.")
        for module in imported_modules
    )


IDENTITY_PROBE = textwrap.dedent(
    """
    import importlib
    import json
    import sys

    order = sys.argv[1]
    canonical_names = (
        "breadboard_engine.compilation.contracts",
        "breadboard_engine.compilation.bundle",
        "breadboard_engine.compilation.server_compiler",
    )
    legacy_names = (
        "agentic_coder_prototype.compilation.contracts",
        "agentic_coder_prototype.compilation.bundle",
        "agentic_coder_prototype.compilation.server_compiler",
    )
    first, second = (
        (canonical_names, legacy_names)
        if order == "canonical-first"
        else (legacy_names, canonical_names)
    )
    for name in first:
        importlib.import_module(name)
    for name in second:
        importlib.import_module(name)

    canonical_contracts, canonical_bundle, canonical_compiler = map(
        importlib.import_module, canonical_names
    )
    legacy_contracts, legacy_bundle, legacy_compiler = map(
        importlib.import_module, legacy_names
    )
    checks = {
        "contracts_module": canonical_contracts is legacy_contracts,
        "bundle_module": canonical_bundle is legacy_bundle,
        "compiler_module": canonical_compiler is legacy_compiler,
        "manifest_reader": canonical_bundle.ManifestReader is legacy_bundle.ManifestReader,
        "bundle_limits": canonical_contracts.BundleLimits is legacy_contracts.BundleLimits,
        "compile_error_code": (
            canonical_contracts.CompileErrorCode is legacy_contracts.CompileErrorCode
        ),
        "mapping_table": (
            canonical_compiler.V1_MAPPING_TABLE is legacy_compiler.V1_MAPPING_TABLE
        ),
        "canonical_module_names": (
            canonical_contracts.__name__
            == "breadboard_engine.compilation.contracts"
            and canonical_bundle.__name__ == "breadboard_engine.compilation.bundle"
            and canonical_compiler.__name__
            == "breadboard_engine.compilation.server_compiler"
        ),
        "representative_type_modules": (
            canonical_contracts.BundleLimits.__module__
            == "breadboard_engine.compilation.contracts"
            and canonical_contracts.CompileErrorCode.__module__
            == "breadboard_engine.compilation.contracts"
        ),
    }
    print(json.dumps(checks, sort_keys=True))
    """
)


@pytest.mark.parametrize("order", ("canonical-first", "legacy-first"))
def test_canonical_and_legacy_compiler_import_order_preserves_identity(
    order: str,
) -> None:
    environment = dict(os.environ)
    environment.pop("BREADBOARD_LEGACY_IMPORTS", None)
    completed = subprocess.run(
        [sys.executable, "-c", IDENTITY_PROBE, order],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    checks = json.loads(completed.stdout)
    assert checks and all(checks.values()), checks
