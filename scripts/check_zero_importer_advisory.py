#!/usr/bin/env python3
"""Advisory zero-importer check for deletion-policy reviews.

The check is intentionally non-blocking by default: it reports Python modules with
no static inbound imports and highlights modules that were not present in a
baseline deletion audit. It does not prove deletion safety; it is an early warning
that a new module needs an owner, an entry point, or audit coverage.
"""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SCAN_ROOTS = (
    "agentic_coder_prototype",
    "breadboard",
    "breadboard_sdk",
    "scripts",
    "tool_calling",
)
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv_linux_import_20260618",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "docs_tmp",
    "logging",
    "node_modules",
    "tmp",
}


@dataclass(frozen=True)
class ModuleFile:
    path: Path
    rel_path: str
    module: str
    package: str


def _repo_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def _module_name(path: Path, repo_root: Path) -> str:
    rel = path.relative_to(repo_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_name(path: Path, module: str) -> str:
    if path.name == "__init__.py":
        return module
    return module.rpartition(".")[0]


def discover_modules(repo_root: Path, scan_roots: Iterable[str]) -> dict[str, ModuleFile]:
    modules: dict[str, ModuleFile] = {}
    for root_name in scan_roots:
        root = _repo_path(repo_root, root_name)
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel_parts = path.relative_to(repo_root).parts
            if any(part in EXCLUDED_PARTS for part in rel_parts):
                continue
            module = _module_name(path, repo_root)
            if not module:
                continue
            rel_path = path.relative_to(repo_root).as_posix()
            modules[module] = ModuleFile(
                path=path,
                rel_path=rel_path,
                module=module,
                package=_package_name(path, module),
            )
    return modules


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    parts = package.split(".") if package else []
    if level:
        keep = max(len(parts) - level + 1, 0)
        parts = parts[:keep]
    if module:
        parts.extend(part for part in module.split(".") if part)
    return ".".join(parts)


def _record_import(target: str, known_modules: set[str], inbound: dict[str, set[str]], importer: str) -> None:
    if not target:
        return
    candidates = [target]
    parts = target.split(".")
    candidates.extend(".".join(parts[:idx]) for idx in range(len(parts) - 1, 0, -1))
    for candidate in candidates:
        if candidate in known_modules and candidate != importer:
            inbound[candidate].add(importer)


def collect_static_inbound(modules: dict[str, ModuleFile]) -> dict[str, set[str]]:
    inbound = {name: set() for name in modules}
    known = set(modules)
    for importer, info in modules.items():
        try:
            tree = ast.parse(info.path.read_text(encoding="utf-8"), filename=info.rel_path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _record_import(alias.name, known, inbound, importer)
            elif isinstance(node, ast.ImportFrom):
                base = (
                    _resolve_relative(info.package, node.level, node.module)
                    if node.level
                    else (node.module or "")
                )
                _record_import(base, known, inbound, importer)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    _record_import(f"{base}.{alias.name}" if base else alias.name, known, inbound, importer)
    return inbound


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def load_baseline_paths(path: Path | None) -> set[str]:
    if path is None:
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    paths: set[str] = set()
    for item in _walk_json(data):
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            verdict = item.get("verdict") or item.get("current_verdict")
            reason = item.get("reason") or ""
            if verdict in {"keep", "quarantine", "delete"} or "zero" in str(reason).lower():
                paths.add(item["path"])
        elif isinstance(item, str) and item.endswith(".py"):
            paths.add(item)
    return paths


def load_changed_paths(repo_root: Path, path: Path | None) -> set[str]:
    if path is None:
        return set()
    changed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or not value.endswith(".py"):
            continue
        rel = _repo_path(repo_root, value)
        try:
            changed.add(rel.resolve().relative_to(repo_root).as_posix())
        except ValueError:
            changed.add(value)
    return changed


def _candidate_zero_importers(zero_importers: list[str], baseline_paths: set[str], changed_paths: set[str]) -> list[str]:
    candidates = [path for path in zero_importers if path not in baseline_paths]
    if changed_paths:
        candidates = [path for path in candidates if path in changed_paths]
    return candidates


def build_report(
    repo_root: Path,
    scan_roots: Iterable[str],
    baseline: Path | None,
    changed_files: Path | None = None,
) -> dict[str, Any]:
    modules = discover_modules(repo_root, scan_roots)
    inbound = collect_static_inbound(modules)
    zero_importers = sorted(
        info.rel_path
        for name, info in modules.items()
        if not inbound[name] and info.path.name != "__init__.py"
    )
    baseline_paths = load_baseline_paths(baseline)
    changed_paths = load_changed_paths(repo_root, changed_files)
    new_zero_importers = _candidate_zero_importers(zero_importers, baseline_paths, changed_paths)
    return {
        "ok": True,
        "advisory": True,
        "module_count": len(modules),
        "zero_importer_count": len(zero_importers),
        "baseline_path_count": len(baseline_paths),
        "changed_python_path_count": len(changed_paths),
        "new_zero_importer_count": len(new_zero_importers),
        "new_zero_importers": new_zero_importers,
        "scan_roots": list(scan_roots),
    }





def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="repository root to scan")
    parser.add_argument(
        "--baseline-json",
        help="optional H1 deletion-audit JSON; paths already present there are not reported as new",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        dest="scan_roots",
        help="relative package/directory root to scan; may be repeated",
    )
    parser.add_argument(
        "--changed-files-file",
        help="optional newline-delimited changed-file list; limits new zero-importer reporting to changed Python files",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    parser.add_argument("--json-out", help="write the report JSON to this path")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    baseline = _repo_path(repo_root, args.baseline_json).resolve() if args.baseline_json else None
    changed_files = _repo_path(repo_root, args.changed_files_file).resolve() if args.changed_files_file else None
    report = build_report(repo_root, args.scan_roots or DEFAULT_SCAN_ROOTS, baseline, changed_files)

    if args.json_out:
        out_path = _repo_path(repo_root, args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "zero-importer advisory: "
            f"{report['new_zero_importer_count']} new / {report['zero_importer_count']} total "
            f"across {report['module_count']} modules"
        )
        for path in report["new_zero_importers"][:50]:
            print(f"NEW {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
