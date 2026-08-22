from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path("breadboard/rl/harness/composition.py")
CLI = Path("breadboard/rl/harness/__main__.py")


def test_composition_has_no_ambient_authority_or_test_imports() -> None:
    tree = ast.parse(SOURCE.read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = [alias.name for node in imports for alias in node.names]
    assert all(not name.startswith("tests") for name in names)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    forbidden = {"getenv", "getcwd", "expanduser"}
    assert not [node for node in calls if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden]
    assert not [node for node in calls if isinstance(node.func, ast.Name) and node.func.id in {"__import__", "eval", "exec"}]


def test_cli_has_only_explicit_composition_and_secret_inputs() -> None:
    source = CLI.read_text()
    assert "--composition-ref" in source
    assert "--secret-file" in source
    for forbidden in ("--factory", "--profile", "--family", "--config", "os.environ", "getenv"):
        assert forbidden not in source


def test_composed_manifest_has_no_secret_value_fields() -> None:
    tree = ast.parse(SOURCE.read_text())
    composed = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ComposedHarnessManifestV1")
    fields = {node.target.id for node in composed.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    forbidden = {name for name in fields if any(part in name for part in ("token", "secret_path", "secret_digest", "secret_length"))}
    assert forbidden == set()
    assert "secret_handle_ids" in fields
