from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

I2_READ_VERIFY_OPERATION_IDS = frozenset(
    {
        "artifact.get",
        "artifact.list",
        "artifact.verify",
        "harness.explain",
        "harness.get",
        "harness.list",
        "harness.validate",
        "harness_lock.get",
        "integration.get",
        "integration.list",
        "session.artifacts",
        "session.events",
        "session.get",
        "session.list",
        "system.describe",
        "system.health",
        "system.schemas",
    }
)


def test_i2_inventory_matches_read_and_verify_catalog() -> None:
    catalog = json.loads(
        (ROOT / "contracts/public/operations.v2.json").read_text(encoding="utf-8")
    )
    observed = {
        operation["operation_id"]
        for operation in catalog["operations"]
        if operation["effects"] in {"read", "verify"}
    }

    assert observed == I2_READ_VERIFY_OPERATION_IDS


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


@pytest.mark.parametrize(
    "relative_path",
    [
        "breadboard/product/operations/artifact.py",
        "breadboard/product/operations/harness.py",
        "breadboard/product/operations/integration.py",
        "breadboard/product/operations/session.py",
        "breadboard/product/operations/system.py",
    ],
)
def test_read_operations_do_not_import_presentations(relative_path: str) -> None:
    imported_modules = _imports(relative_path)
    violations = sorted(
        module
        for module in imported_modules
        if module.startswith(
            ("breadboard.product.cli", "breadboard_engine.api", "fastapi")
        )
    )
    assert violations == []


@pytest.mark.parametrize(
    ("relative_path", "function_names"),
    [
        (
            "breadboard_engine/api/public/artifact.py",
            ("list_artifacts", "verify", "get"),
        ),
        (
            "breadboard_engine/api/public/harness.py",
            ("list_harnesses_route", "validate", "explain", "get_lock", "get"),
        ),
        (
            "breadboard_engine/api/public/integration.py",
            ("list_integrations", "get"),
        ),
        (
            "breadboard_engine/api/public/session.py",
            ("list_sessions", "events", "artifacts", "get"),
        ),
        (
            "breadboard_engine/api/public/system.py",
            ("describe", "health", "schemas"),
        ),
    ],
)
def test_public_read_adapters_do_not_reach_cli(
    relative_path: str,
    function_names: tuple[str, ...],
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    cli_bindings: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            cli_bindings.update(
                alias.asname or alias.name.split(".", maxsplit=1)[0]
                for alias in node.names
                if alias.name.startswith("breadboard.product.cli")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("breadboard.product.cli")
        ):
            cli_bindings.update(alias.asname or alias.name for alias in node.names)

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert set(function_names) <= functions.keys()
    for function_name in function_names:
        referenced_names = {
            node.id
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Name)
        }
        assert referenced_names.isdisjoint(cli_bindings)
        assert "SimpleNamespace" not in referenced_names
