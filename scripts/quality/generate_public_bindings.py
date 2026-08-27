#!/usr/bin/env python3
"""Generate public client bindings from the immutable operation catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
CATALOG_RELATIVE: Final = Path("contracts/public/operations.v2.json")
GENERATOR_PATH: Final = "scripts/quality/generate_public_bindings.py"
GENERATOR_VERSION: Final = "1"
SCHEMA_VERSION: Final = "bb.public_client_binding_manifest.v1"
NORMALIZED_FIELDS: Final = (
    "operation_id",
    "status",
    "http_method",
    "path",
    "cli_command",
    "python_client",
    "python_method",
    "typescript_client",
    "typescript_method",
    "action_id",
    "action_kind",
)
_HTTP_METHODS: Final = frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class CatalogError(ValueError):
    """Raised when the catalog cannot be normalized into public bindings."""


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON bytes used for catalog hashing and manifests."""

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _require_string(value: Any, field: str, operation_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{operation_id}: {field} must be a non-empty string")
    return value


def _binding(
    row: Mapping[str, Any], surface: str, operation_id: str
) -> Mapping[str, Any]:
    bindings = row.get("bindings")
    if not isinstance(bindings, Mapping):
        raise CatalogError(f"{operation_id}: bindings must be an object")
    value = bindings.get(surface)
    if not isinstance(value, Mapping):
        raise CatalogError(f"{operation_id}: missing {surface} binding")
    return value


def _normalize_catalog(catalog: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(catalog, Mapping):
        raise CatalogError("catalog root must be an object")
    if catalog.get("contract_id") != "bb.public_operation_catalog.v2":
        raise CatalogError("catalog contract_id must be bb.public_operation_catalog.v2")
    if catalog.get("version") != 2 or catalog.get("status") != "current":
        raise CatalogError("catalog version/status must be 2/current")
    operations = catalog.get("operations")
    if not isinstance(operations, list):
        raise CatalogError("catalog operations must be an array")
    if len(operations) != 26:
        raise CatalogError(
            f"catalog must contain exactly 26 operations, got {len(operations)}"
        )

    normalized: list[dict[str, str]] = []
    seen: dict[str, dict[tuple[str, ...], str]] = {
        "http_method_path": {},
        "cli_command": {},
        "python_method": {},
        "typescript_method": {},
        "action_id": {},
    }
    seen_operation_ids: set[str] = set()
    for index, row in enumerate(operations):
        if not isinstance(row, Mapping):
            raise CatalogError(f"operations[{index}] must be an object")
        operation_id = _require_string(
            row.get("operation_id"), "operation_id", f"operations[{index}]"
        )
        if not _OPERATION_ID.fullmatch(operation_id):
            raise CatalogError(f"{operation_id}: malformed operation_id")
        if operation_id in seen_operation_ids:
            raise CatalogError(f"duplicate operation_id: {operation_id}")
        seen_operation_ids.add(operation_id)
        status = _require_string(row.get("status"), "status", operation_id)
        if status != "candidate":
            raise CatalogError(f"{operation_id}: status must be candidate")

        openapi = _binding(row, "openapi", operation_id)
        method = _require_string(openapi.get("method"), "openapi.method", operation_id)
        if method not in _HTTP_METHODS:
            raise CatalogError(f"{operation_id}: malformed HTTP method {method!r}")
        path = _require_string(openapi.get("path"), "openapi.path", operation_id)
        if not path.startswith("/v1/"):
            raise CatalogError(f"{operation_id}: malformed HTTP path {path!r}")
        if openapi.get("operation_id") != operation_id:
            raise CatalogError(
                f"{operation_id}: openapi.operation_id must match operation_id"
            )

        bbh = _binding(row, "bbh", operation_id)
        cli_command = _require_string(bbh.get("command"), "bbh.command", operation_id)
        if not cli_command.startswith("bbh "):
            raise CatalogError(f"{operation_id}: malformed CLI command {cli_command!r}")

        python = _binding(row, "python_sdk", operation_id)
        python_client = _require_string(
            python.get("client"), "python_sdk.client", operation_id
        )
        python_method = _require_string(
            python.get("method"), "python_sdk.method", operation_id
        )
        if python_client != "BreadBoardClient" or not _IDENTIFIER.fullmatch(
            python_method
        ):
            raise CatalogError(f"{operation_id}: malformed Python SDK identity")

        typescript = _binding(row, "typescript_sdk", operation_id)
        typescript_client = _require_string(
            typescript.get("client"), "typescript_sdk.client", operation_id
        )
        typescript_method = _require_string(
            typescript.get("method"), "typescript_sdk.method", operation_id
        )
        if typescript_client != "BreadBoardClient" or not _IDENTIFIER.fullmatch(
            typescript_method
        ):
            raise CatalogError(f"{operation_id}: malformed TypeScript SDK identity")

        tui = _binding(row, "tui", operation_id)
        action_id = _require_string(tui.get("action_id"), "tui.action_id", operation_id)
        action_kind = _require_string(tui.get("kind"), "tui.kind", operation_id)
        if not action_id.startswith("public.") or action_kind not in {"action", "view"}:
            raise CatalogError(f"{operation_id}: malformed TUI identity")

        values = {
            "operation_id": operation_id,
            "status": status,
            "http_method": method,
            "path": path,
            "cli_command": cli_command,
            "python_client": python_client,
            "python_method": python_method,
            "typescript_client": typescript_client,
            "typescript_method": typescript_method,
            "action_id": action_id,
            "action_kind": action_kind,
        }
        identities = {
            "http_method_path": (method, path),
            "cli_command": (cli_command,),
            "python_method": (python_method,),
            "typescript_method": (typescript_method,),
            "action_id": (action_id,),
        }
        for name, identity in identities.items():
            previous = seen[name].get(identity)
            if previous is not None:
                raise CatalogError(
                    f"duplicate {name} identity for {previous} and {operation_id}: {identity}"
                )
            seen[name][identity] = operation_id
        normalized.append(values)

    normalized.sort(key=lambda item: item["operation_id"])
    return tuple(normalized)


def _load_catalog(root: Path) -> Any:
    path = root / CATALOG_RELATIVE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"unable to load catalog {path}: {exc}") from exc


def _canonical_catalog_bytes(catalog: Any) -> bytes:
    if isinstance(catalog, Mapping) and isinstance(catalog.get("operations"), list):
        operations = catalog["operations"]
        if all(
            isinstance(row, Mapping) and isinstance(row.get("operation_id"), str)
            for row in operations
        ):
            catalog = dict(catalog)
            catalog["operations"] = sorted(
                operations, key=lambda row: row["operation_id"]
            )
    return canonical_bytes(catalog)


def _py_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_python_module(
    rows: Sequence[Mapping[str, str]], catalog_id: str, catalog_sha256: str
) -> bytes:
    out = [
        "# GENERATED FILE - do not edit by hand.",
        f"# generator: {GENERATOR_PATH}",
        f"# generator-version: {GENERATOR_VERSION}",
        f"# catalog-id: {catalog_id}",
        f"# catalog-sha256: {catalog_sha256}",
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from types import MappingProxyType",
        "from typing import Final, Mapping",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class PublicOperationBinding:",
    ]
    for field in NORMALIZED_FIELDS:
        out.append(f"    {field}: str")
    out.extend(
        [
            "",
            "",
            "PUBLIC_OPERATION_BINDINGS: Final[tuple[PublicOperationBinding, ...]] = (",
        ]
    )
    for row in rows:
        out.append("    PublicOperationBinding(")
        for field in NORMALIZED_FIELDS:
            out.append(f"        {field}={_py_string(row[field])},")
        out.append("    ),")
    out.extend(
        [
            ")",
            "",
            "PUBLIC_BINDINGS_BY_OPERATION_ID: Final[Mapping[str, PublicOperationBinding]] = (",
            "    MappingProxyType(",
            "        {",
        ]
    )
    for index, row in enumerate(rows):
        operation_id = row["operation_id"]
        out.append(
            f"            {_py_string(operation_id)}: PUBLIC_OPERATION_BINDINGS[{index}],"
        )
    out.extend(
        [
            "        }",
            "    )",
            ")",
            "",
            "__all__ = [",
            '    "PublicOperationBinding",',
            '    "PUBLIC_OPERATION_BINDINGS",',
            '    "PUBLIC_BINDINGS_BY_OPERATION_ID",',
            "]",
            "",
        ]
    )
    return "\n".join(out).encode("utf-8")


def _render_python_init(catalog_id: str, catalog_sha256: str) -> bytes:
    return (
        "# GENERATED FILE - do not edit by hand.\n"
        f"# generator: {GENERATOR_PATH}\n"
        f"# generator-version: {GENERATOR_VERSION}\n"
        f"# catalog-id: {catalog_id}\n"
        f"# catalog-sha256: {catalog_sha256}\n\n"
        "from .public_bindings import (\n"
        "    PUBLIC_BINDINGS_BY_OPERATION_ID,\n"
        "    PUBLIC_OPERATION_BINDINGS,\n"
        "    PublicOperationBinding,\n"
        ")\n\n"
        "__all__ = [\n"
        '    "PublicOperationBinding",\n'
        '    "PUBLIC_OPERATION_BINDINGS",\n'
        '    "PUBLIC_BINDINGS_BY_OPERATION_ID",\n'
        "]\n"
    ).encode("utf-8")


def _ts_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_typescript(
    rows: Sequence[Mapping[str, str]], catalog_id: str, catalog_sha256: str
) -> bytes:
    http_methods = " | ".join(
        _ts_string(method) for method in sorted({row["http_method"] for row in rows})
    )
    operation_ids = " | ".join(_ts_string(row["operation_id"]) for row in rows)
    action_ids = " | ".join(_ts_string(row["action_id"]) for row in rows)
    out = [
        "// GENERATED FILE - do not edit by hand.",
        f"// generator: {GENERATOR_PATH}",
        f"// generator-version: {GENERATOR_VERSION}",
        f"// catalog-id: {catalog_id}",
        f"// catalog-sha256: {catalog_sha256}",
        "",
        f"export type HttpMethod = {http_methods};",
        f"export type PublicOperationId = {operation_ids};",
        f"export type PublicActionId = {action_ids};",
        "",
        "export interface PublicOperationBinding {",
        "  readonly operationId: PublicOperationId;",
        '  readonly status: "candidate";',
        "  readonly httpMethod: HttpMethod;",
        "  readonly path: string;",
        "  readonly cliCommand: string;",
        '  readonly pythonClient: "BreadBoardClient";',
        "  readonly pythonMethod: string;",
        '  readonly typescriptClient: "BreadBoardClient";',
        "  readonly typescriptMethod: string;",
        "  readonly actionId: PublicActionId;",
        '  readonly actionKind: "action" | "view";',
        "}",
        "",
        "export const PUBLIC_OPERATION_BINDINGS: readonly PublicOperationBinding[] = [",
    ]
    for row in rows:
        out.extend(
            [
                "  {",
                f"    operationId: {_ts_string(row['operation_id'])},",
                f"    status: {_ts_string(row['status'])},",
                f"    httpMethod: {_ts_string(row['http_method'])},",
                f"    path: {_ts_string(row['path'])},",
                f"    cliCommand: {_ts_string(row['cli_command'])},",
                f"    pythonClient: {_ts_string(row['python_client'])},",
                f"    pythonMethod: {_ts_string(row['python_method'])},",
                f"    typescriptClient: {_ts_string(row['typescript_client'])},",
                f"    typescriptMethod: {_ts_string(row['typescript_method'])},",
                f"    actionId: {_ts_string(row['action_id'])},",
                f"    actionKind: {_ts_string(row['action_kind'])},",
                "  },",
            ]
        )
    out.extend(
        [
            "] as const;",
            "",
            "export const PUBLIC_BINDINGS_BY_OPERATION_ID: Readonly<Record<PublicOperationId, PublicOperationBinding>> = {",
        ]
    )
    for index, row in enumerate(rows):
        out.append(
            f"  {_ts_string(row['operation_id'])}: PUBLIC_OPERATION_BINDINGS[{index}],"
        )
    out.extend(
        [
            "} as const;",
            "",
            "export const PUBLIC_BINDINGS_BY_ACTION_ID: Readonly<Record<PublicActionId, PublicOperationBinding>> = {",
        ]
    )
    for index, row in enumerate(rows):
        out.append(
            f"  {_ts_string(row['action_id'])}: PUBLIC_OPERATION_BINDINGS[{index}],"
        )
    out.extend(
        [
            "} as const;",
            "",
            "export interface PublicRouteEntry {",
            "  readonly path: string;",
            "  readonly method: HttpMethod;",
            "  readonly operationId: PublicOperationId;",
            "}",
            "",
            "export const PUBLIC_ROUTES: readonly PublicRouteEntry[] = [",
        ]
    )
    for row in rows:
        out.append(
            f"  {{ path: {_ts_string(row['path'])}, method: {_ts_string(row['http_method'])}, operationId: {_ts_string(row['operation_id'])} }},"
        )
    out.extend(
        [
            "] as const;",
            "",
        ]
    )
    return "\n".join(out).encode("utf-8")


def _render_manifest(
    rows: Sequence[Mapping[str, str]],
    surface: str,
    catalog_id: str,
    catalog_sha256: str,
) -> bytes:
    if surface == "python_sdk":
        operations = [
            {
                "client": row["python_client"],
                "method": row["python_method"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]
    elif surface == "typescript_sdk":
        operations = [
            {
                "client": row["typescript_client"],
                "method": row["typescript_method"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]
    elif surface == "tui":
        operations = [
            {
                "action_id": row["action_id"],
                "kind": row["action_kind"],
                "operation_id": row["operation_id"],
            }
            for row in rows
        ]
    else:  # pragma: no cover - targets are fixed below
        raise AssertionError(surface)
    return canonical_bytes(
        {
            "catalog_id": catalog_id,
            "catalog_sha256": catalog_sha256,
            "generated_by": GENERATOR_PATH,
            "generator_version": GENERATOR_VERSION,
            "operations": operations,
            "schema_version": SCHEMA_VERSION,
            "surface": surface,
        }
    )


def build_outputs(root: Path | str | None = None) -> dict[Path, bytes]:
    """Build every catalog-owned output without writing to disk."""

    repo_root = Path(ROOT if root is None else root).resolve()
    catalog = _load_catalog(repo_root)
    rows = _normalize_catalog(catalog)
    canonical_catalog = _canonical_catalog_bytes(catalog)
    catalog_id = str(catalog["contract_id"])
    catalog_sha256 = _sha256(canonical_catalog)
    return {
        repo_root
        / "breadboard/product/operations/generated_bindings.py": _render_python_module(
            rows, catalog_id, catalog_sha256
        ),
        repo_root
        / "breadboard_sdk/generated/public_bindings.py": _render_python_module(
            rows, catalog_id, catalog_sha256
        ),
        repo_root / "breadboard_sdk/generated/__init__.py": _render_python_init(
            catalog_id, catalog_sha256
        ),
        repo_root / "sdk/ts/src/generated/public-bindings.ts": _render_typescript(
            rows, catalog_id, catalog_sha256
        ),
        repo_root
        / "breadboard_sdk/generated/public_surface_manifest.v1.json": _render_manifest(
            rows, "python_sdk", catalog_id, catalog_sha256
        ),
        repo_root
        / "sdk/ts/src/generated/public_surface_manifest.v1.json": _render_manifest(
            rows, "typescript_sdk", catalog_id, catalog_sha256
        ),
        repo_root
        / "tui_skeleton/src/generated/public_surface_manifest.v1.json": _render_manifest(
            rows, "tui", catalog_id, catalog_sha256
        ),
    }


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify generated outputs without writing"
    )
    args = parser.parse_args(argv)
    try:
        outputs = build_outputs()
    except CatalogError as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2

    stale = [
        path
        for path, content in outputs.items()
        if not path.is_file() or path.read_bytes() != content
    ]
    if args.check:
        if stale:
            for path in sorted(stale, key=lambda item: str(item.relative_to(ROOT))):
                print(f"stale generated public binding: {path.relative_to(ROOT)}")
            return 1
        print(f"public binding codegen check: OK ({len(outputs)} files current)")
        return 0

    for path, content in sorted(outputs.items(), key=lambda item: str(item[0])):
        _write_atomic(path, content)
    print(f"public binding codegen: wrote {len(outputs)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
