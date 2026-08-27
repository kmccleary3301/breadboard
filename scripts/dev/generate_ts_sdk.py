#!/usr/bin/env python3
"""Generate the observed-app OpenAPI document and complete TS route manifest."""

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

_PUBLIC_SURFACE_ENV = {
    "BREADBOARD_ENABLE_PUBLIC_API": "1",
    "BREADBOARD_LEGACY_ROUTES": "0",
    "BREADBOARD_ENABLE_E4_API": "0",
    "ATP_REPL_ENABLE": "0",
    "ATP_REPL_ROUTE": "0",
    "BREADBOARD_EXTENSIONS_CONFIG_PATH": "",
    "BREADBOARD_ENGINE_VERSION": "0.1.0",
    "BREADBOARD_RL_RUN_STORE": ":memory:",
}


def _load_spec() -> dict:
    previous = {name: os.environ.get(name) for name in _PUBLIC_SURFACE_ENV}
    os.environ.update(_PUBLIC_SURFACE_ENV)
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from breadboard_engine.api.cli_bridge.app import create_app  # noqa: E402

        return create_app(include_atp_routes=False).openapi()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _canonical(spec: dict) -> str:
    return json.dumps(spec, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _header(schema_hash: str, source_hash: str) -> str:
    return (
        "// GENERATED FILE - do not edit by hand.\n"
        "// generator: scripts/dev/generate_ts_sdk.py (deterministic, in-process, no network)\n"
        f"// openapi-schema-sha256: {schema_hash}\n"
        f"// app-source-sha256: {source_hash}\n"
    )


def _render_routes(spec: dict, schema_hash: str, source_hash: str) -> str:
    rows = []
    for path in sorted(spec.get("paths", {})):
        for method in sorted(spec["paths"][path]):
            op = spec["paths"][path][method]
            if not isinstance(op, dict):
                continue
            rows.append(
                "  { path: %s, method: %s, operationId: %s },"
                % (
                    json.dumps(path),
                    json.dumps(method.upper()),
                    json.dumps(op.get("operationId", "")),
                )
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
        "routes.ts": _render_routes(spec, schema_hash, source_hash),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true", help="verify committed output is current"
    )
    args = ap.parse_args()

    files = generate()
    if args.check:
        stale = []
        for name, content in files.items():
            path = OUT_DIR / name
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        if stale:
            print(
                f"STALE generated SDK files: {stale}; run scripts/dev/generate_ts_sdk.py",
                file=sys.stderr,
            )
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
