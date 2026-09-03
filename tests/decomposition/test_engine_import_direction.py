from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPO_ROOT / "breadboard_engine"
PRODUCT_ROOT = REPO_ROOT / "breadboard" / "product"



def _module_name(root: Path, path: Path, package: str) -> str:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    return ".".join((package, *parts))


def _resolve_import(owner: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    owner_parts = owner.split(".")
    package_parts = (
        owner_parts if owner_parts[-1] == "__init__" else owner_parts[:-1]
    )
    if node.level > len(package_parts):
        return ""
    base = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)

def _imports(tree: ast.AST, owner: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import(owner, node)
            imported.add(module)
            if module:
                imported.update(f"{module}.{alias.name}" for alias in node.names)
    return imported


def _looks_like_root_wrapper(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in tree.body):
        return False
    imports = _imports(tree, _module_name(ENGINE_ROOT, path, "breadboard_engine"))
    nested_import = any(
        name.startswith("breadboard_engine.") and name != "breadboard_engine"
        for name in imports
    )
    dynamic_alias = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "import_module"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        for node in ast.walk(tree)
    )
    return nested_import or dynamic_alias


def test_non_api_engine_modules_do_not_import_fastapi() -> None:
    violations: list[str] = []
    for path in ENGINE_ROOT.rglob("*.py"):
        if "api" in path.relative_to(ENGINE_ROOT).parts:
            continue
        owner = _module_name(ENGINE_ROOT, path, "breadboard_engine")
        for imported in _imports(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), owner):
            if imported == "fastapi" or imported.startswith("fastapi."):
                violations.append(f"{path}: imports {imported}")
    assert violations == []


def test_product_modules_do_not_import_research_roots() -> None:
    research_roots = {
        "breadboard.rl",
        "breadboard.search",
        "breadboard.optimize",
        "breadboard_engine.rl",
        "breadboard_engine.search",
        "breadboard_engine.optimize",
    }
    violations: list[str] = []
    for path in PRODUCT_ROOT.rglob("*.py"):
        owner = _module_name(PRODUCT_ROOT, path, "breadboard.product")
        for imported in _imports(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), owner):
            if any(imported == root or imported.startswith(root + ".") for root in research_roots):
                violations.append(f"{path}: imports {imported}")
    assert violations == []


def test_active_engine_modules_do_not_import_legacy() -> None:
    violations: list[str] = []
    for path in ENGINE_ROOT.rglob("*.py"):
        if "legacy" in path.relative_to(ENGINE_ROOT).parts:
            continue
        owner = _module_name(ENGINE_ROOT, path, "breadboard_engine")
        for imported in _imports(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), owner):
            if imported == "breadboard_engine.legacy" or imported.startswith("breadboard_engine.legacy."):
                violations.append(f"{path}: imports {imported}")
    assert violations == []


def test_engine_root_contains_no_compatibility_wrappers() -> None:
    observed = {
        path.name
        for path in ENGINE_ROOT.glob("*.py")
        if path.name != "__init__.py" and _looks_like_root_wrapper(path)
    }
    assert observed == set()


def test_retired_engine_alias_is_absent() -> None:
    assert not (REPO_ROOT / "agentic_coder_prototype").exists()
    violations: list[str] = []
    for root in (REPO_ROOT / "breadboard", ENGINE_ROOT):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            owner = _module_name(root, path, root.name)
            for imported in _imports(tree, owner):
                if imported == "agentic_coder_prototype" or imported.startswith(
                    "agentic_coder_prototype."
                ):
                    violations.append(f"{path}: imports {imported}")
    assert violations == []
