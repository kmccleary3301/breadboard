from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _imports(relative_path: str) -> set[str]:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    ("relative_path", "forbidden_prefixes"),
    [
        (
            "breadboard/product/operations/model.py",
            ("breadboard.product.cli", "breadboard_engine.api", "fastapi"),
        ),
        (
            "breadboard/product/operations/system.py",
            ("breadboard.product.cli", "breadboard_engine.api", "fastapi"),
        ),
        (
            "breadboard/product/cli/system.py",
            ("breadboard_engine.api", "fastapi"),
        ),
    ],
)
def test_system_operation_layers_do_not_import_presentations(
    relative_path: str,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    imported_modules = _imports(relative_path)
    violations = sorted(
        module for module in imported_modules if module.startswith(forbidden_prefixes)
    )
    assert violations == []


def test_public_describe_adapter_does_not_invoke_cli_adapter() -> None:
    relative_path = "breadboard_engine/api/public/system.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    describe = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "describe"
    )
    referenced_names = {
        node.id for node in ast.walk(describe) if isinstance(node, ast.Name)
    }

    assert "describe_system" in referenced_names
    assert "legacy_system_operations" not in referenced_names
