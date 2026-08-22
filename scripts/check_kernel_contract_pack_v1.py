#!/usr/bin/env python3
"""Validate the Kernel Contract Pack v1 hash manifest.

Exit codes:
- 0: pass
- 2: contract pack mismatch or malformed manifest
- 3: invalid input/runtime error
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "contracts" / "policies" / "kernel_contract_pack_v1_manifest.json"
EXPECTED_SCHEMA = "breadboard.kernel_contract_pack_manifest.v1"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_manifest_path(raw_path: object) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"manifest file path must be a non-empty string: {raw_path!r}")
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"manifest file path must be repo-relative and safe: {raw_path}")
    return raw_path.replace("\\", "/")


def validate_contract_pack(*, manifest_path: Path, repo_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = _load_json(manifest_path)

    schema = manifest.get("schema")
    if schema != EXPECTED_SCHEMA:
        errors.append(f"schema mismatch: expected {EXPECTED_SCHEMA!r}, got {schema!r}")

    if manifest.get("contract_pack_version") != "kernel_contract_pack_v1":
        errors.append("contract_pack_version must be 'kernel_contract_pack_v1'")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        errors.append("files must be a non-empty array")
        raw_files = []

    seen_paths: set[str] = set()
    file_results: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict):
            errors.append(f"files[{index}] must be an object")
            continue
        try:
            rel_path = _safe_manifest_path(entry.get("path"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        expected_sha = entry.get("sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            errors.append(f"{rel_path}: sha256 must be a 64-character hex string")
            expected_sha = ""
        elif any(ch not in "0123456789abcdef" for ch in expected_sha):
            errors.append(f"{rel_path}: sha256 must be lowercase hexadecimal")

        if rel_path in seen_paths:
            errors.append(f"duplicate manifest path: {rel_path}")
        seen_paths.add(rel_path)

        file_path = repo_root / rel_path
        result: dict[str, Any] = {
            "path": rel_path,
            "expected_sha256": expected_sha,
            "actual_sha256": None,
            "exists": file_path.is_file(),
            "ok": False,
        }
        if not file_path.is_file():
            errors.append(f"missing contract pack file: {rel_path}")
        else:
            actual_sha = _sha256_file(file_path)
            result["actual_sha256"] = actual_sha
            result["ok"] = actual_sha == expected_sha
            if actual_sha != expected_sha:
                errors.append(
                    f"hash mismatch: {rel_path} expected {expected_sha}, got {actual_sha}"
                )
        file_results.append(result)

    return {
        "ok": not errors,
        "schema": EXPECTED_SCHEMA,
        "manifest_path": str(manifest_path),
        "repo_root": str(repo_root),
        "contract_pack_version": manifest.get("contract_pack_version"),
        "files_checked": len(file_results),
        "files": file_results,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Kernel Contract Pack v1 hashes.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to manifest JSON.")
    parser.add_argument("--repo-root", default=str(ROOT), help="Repository root for manifest paths.")
    parser.add_argument("--json-out", default="", help="Optional path to write full JSON report.")
    parser.add_argument("--output-json", default="", help="Alias for --json-out.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report to stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        repo_root = Path(args.repo_root).expanduser().resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"manifest not found: {manifest_path}")
        if not repo_root.is_dir():
            raise FileNotFoundError(f"repo root not found: {repo_root}")

        result = validate_contract_pack(manifest_path=manifest_path, repo_root=repo_root)
        out_path = args.json_out or args.output_json
        if out_path:
            target = Path(out_path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(
                json.dumps(
                    {
                        "ok": result["ok"],
                        "files_checked": result["files_checked"],
                        "errors": result["errors"],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0 if result["ok"] else 2
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
