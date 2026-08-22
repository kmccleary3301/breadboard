#!/usr/bin/env python3
"""T-4 codegen ownership: OpenAPI-derived TS DTOs + route manifest for sdk/ts.

Deterministic and network-independent: the OpenAPI document is produced by
importing the CLI bridge FastAPI app in-process (public API surface enabled),
never by hitting a server. Outputs are committed; CI runs ``--check``.

Outputs (sdk/ts/src/generated/):
  openapi.v1.json   canonical (sorted, 1-indent) OpenAPI document
  dtos.ts           TS interfaces for components.schemas
  routes.ts         route manifest: path, method, operationId

Every generated file carries a header with the generator path, the OpenAPI
schema hash, and the app source hash so drift is attributable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "sdk" / "ts" / "src" / "generated"
APP_SOURCE = REPO_ROOT / "breadboard_engine" / "api" / "cli_bridge" / "app.py"

_TS_PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}


def _load_spec() -> dict:
    os.environ.setdefault("BREADBOARD_ENABLE_PUBLIC_API", "1")
    os.environ.setdefault("BREADBOARD_LEGACY_ROUTES", "0")
    sys.path.insert(0, str(REPO_ROOT))
    from breadboard_engine.api.cli_bridge.app import app  # noqa: E402

    return app.openapi()


def _canonical(spec: dict) -> str:
    return json.dumps(spec, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ts_type(schema: dict | bool | None) -> str:
    if schema is None or schema is True or schema == {}:
        return "unknown"
    if schema is False:
        return "never"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [_ts_type(s) for s in schema[key]]
            return " | ".join(dict.fromkeys(parts)) or "unknown"
    if "allOf" in schema:
        parts = [_ts_type(s) for s in schema["allOf"]]
        return " & ".join(dict.fromkeys(parts)) or "unknown"
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"]) or "unknown"
    if "const" in schema:
        return json.dumps(schema["const"])
    typ = schema.get("type")
    if isinstance(typ, list):
        return " | ".join(dict.fromkeys(_TS_PRIMITIVES.get(t, "unknown") for t in typ))
    if typ == "array":
        item = _ts_type(schema.get("items"))
        return f"Array<{item}>"
    if typ == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = []
        for name in sorted(props):
            opt = "" if name in required else "?"
            fields.append(f"{json.dumps(name)}{opt}: {_ts_type(props[name])};")
        extra = schema.get("additionalProperties")
        if extra is not None and extra is not False:
            fields.append(f"[key: string]: {_ts_type(extra)};")
        if not fields:
            return "Record<string, unknown>"
        return "{ " + " ".join(fields) + " }"
    if typ in _TS_PRIMITIVES:
        return _TS_PRIMITIVES[typ]
    return "unknown"


def _header(schema_hash: str, source_hash: str) -> str:
    return (
        "// GENERATED FILE - do not edit by hand.\n"
        "// generator: scripts/dev/generate_ts_sdk.py (deterministic, in-process, no network)\n"
        f"// openapi-schema-sha256: {schema_hash}\n"
        f"// app-source-sha256: {source_hash}\n"
    )


def _render_dtos(spec: dict, schema_hash: str, source_hash: str) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    out = [_header(schema_hash, source_hash)]
    for name in sorted(schemas):
        body = _ts_type(schemas[name])
        if body.startswith("{ "):
            out.append(f"export interface {name} {body}\n")
        else:
            out.append(f"export type {name} = {body};\n")
    return "\n".join(out)


def _render_routes(spec: dict, schema_hash: str, source_hash: str) -> str:
    rows = []
    for path in sorted(spec.get("paths", {})):
        for method in sorted(spec["paths"][path]):
            op = spec["paths"][path][method]
            if not isinstance(op, dict):
                continue
            rows.append(
                "  { path: %s, method: %s, operationId: %s },"
                % (json.dumps(path), json.dumps(method.upper()), json.dumps(op.get("operationId", "")))
            )
    return (
        _header(schema_hash, source_hash)
        + "\nexport interface RouteEntry { path: string; method: string; operationId: string }\n\n"
        + "export const ROUTES: readonly RouteEntry[] = [\n"
        + "\n".join(rows)
        + "\n] as const;\n"
    )


def generate() -> dict[str, str]:
    spec = _load_spec()
    canonical = _canonical(spec)
    schema_hash = _sha(canonical)
    source_hash = _sha(APP_SOURCE.read_text(encoding="utf-8"))
    return {
        "openapi.v1.json": canonical,
        "dtos.ts": _render_dtos(spec, schema_hash, source_hash),
        "routes.ts": _render_routes(spec, schema_hash, source_hash),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify committed output is current")
    args = ap.parse_args()

    files = generate()
    if args.check:
        stale = []
        for name, content in files.items():
            path = OUT_DIR / name
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        if stale:
            print(f"STALE generated SDK files: {stale}; run scripts/dev/generate_ts_sdk.py", file=sys.stderr)
            return 1
        print(f"ts-sdk codegen check: OK ({len(files)} files current)")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")
        print(f"wrote {OUT_DIR / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
