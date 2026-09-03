from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_ROOT = REPO_ROOT / "breadboard_engine" / "provider"
CONDUCTOR_ROOT = REPO_ROOT / "breadboard_engine" / "conductor"


def _provider_module_name(path: Path) -> str:
    relative = path.relative_to(PROVIDER_ROOT)
    if relative.name == "__init__.py":
        return "breadboard_engine.provider"
    return "breadboard_engine.provider." + ".".join(relative.with_suffix("").parts)


def _resolve_provider_import(owner: Path, node: ast.ImportFrom) -> str | None:
    owner_parts = _provider_module_name(owner).split(".")
    base_parts = owner_parts if owner.name == "__init__.py" else owner_parts[:-1]
    if node.level == 0:
        return node.module if node.module and node.module.startswith("breadboard_engine.provider") else None
    if node.level > len(base_parts):
        return None
    base = base_parts[: len(base_parts) - node.level + 1]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def test_provider_layers_only_import_downward_and_facade_is_last() -> None:
    ranks = {
        "breadboard_engine.provider.contracts": 0,
        "breadboard_engine.provider.registry": 1,
        "breadboard_engine.provider.sdk_bindings": 1,
        "breadboard_engine.provider.runtimes": 2,
        "breadboard_engine.provider.runtimes.openai": 2,
        "breadboard_engine.provider.runtimes.anthropic": 2,
        "breadboard_engine.provider.runtimes.testing": 2,
        "breadboard_engine.provider.runtime_replay": 2,
        "breadboard_engine.provider.runtime_codex": 2,
        "breadboard_engine.provider.builtins": 3,
        "breadboard_engine.provider.runtime": 4,
    }
    violations: list[str] = []
    for path in PROVIDER_ROOT.rglob("*.py"):
        owner = _provider_module_name(path)
        owner_rank = ranks.get(owner)
        if owner_rank is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                imported = _resolve_provider_import(path, node)
                if imported is None:
                    continue
                if imported == "breadboard_engine.provider.runtime" and owner != imported:
                    violations.append(f"{path}: imports facade {imported}")
                imported_rank = ranks.get(imported)
                if imported_rank is not None and imported_rank > owner_rank:
                    violations.append(f"{path}: layer {owner_rank} imports higher layer {imported} ({imported_rank})")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"breadboard_engine.provider.runtime", "breadboard_engine.provider.runtime"} and owner != "breadboard_engine.provider.runtime":
                        violations.append(f"{path}: imports facade {alias.name}")
    assert violations == []


def test_conductor_imports_contracts_or_registry_not_provider_facade() -> None:
    violations: list[str] = []
    contract_imports = 0
    for path in CONDUCTOR_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"breadboard_engine.provider.runtime", "breadboard_engine.provider.runtime"}:
                        violations.append(f"{path}: imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.endswith("provider.runtime") or module.endswith("provider_runtime"):
                    violations.append(f"{path}: imports facade {module}")
                if module.endswith("provider.contracts"):
                    contract_imports += 1
    assert contract_imports > 0
    assert violations == []
