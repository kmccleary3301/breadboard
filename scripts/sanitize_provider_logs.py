#!/usr/bin/env python3
"""
Redact sensitive fields (API keys, auth headers) from provider dump JSON files.

Usage:
    python scripts/sanitize_provider_logs.py <input-dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

SENSITIVE_HEADER_NAMES = {"x-api-key", "authorization", "x-client-api-key"}
SENSITIVE_JSON_KEYS = {"api_key", "apiKey", "authorization", "auth"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Redact sensitive fields from provider dumps.")
    parser.add_argument("input_dir", type=Path, help="Directory containing *_request/_response JSON logs.")
    parser.add_argument("--dry-run", action="store_true", help="Show files that would be sanitized without modifying them.")
    return parser.parse_args()


def scrub_headers(headers: List[Dict[str, Any]]) -> bool:
    dirty = False
    for header in headers:
        name = str(header.get("name") or header.get("Name") or "").lower()
        if name in SENSITIVE_HEADER_NAMES and header.get("value") != "[redacted]":
            header["value"] = "[redacted]"
            dirty = True
    return dirty


def scrub_json(obj: Any) -> bool:
    dirty = False
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            lowered = key.lower()
            if lowered in SENSITIVE_JSON_KEYS and isinstance(value, str) and value:
                obj[key] = "[redacted]"
                dirty = True
            else:
                dirty = scrub_json(value) or dirty
    elif isinstance(obj, list):
        for item in obj:
            dirty = scrub_json(item) or dirty
    return dirty


def sanitize_file(path: Path, dry_run: bool = False) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    modified = False
    if isinstance(payload.get("headers"), list):
        modified = scrub_headers(payload["headers"]) or modified
    body = payload.get("body") or {}
    if isinstance(body, dict):
        if isinstance(body.get("json"), dict):
            modified = scrub_json(body["json"]) or modified
        if isinstance(body.get("text"), str):
            for keyword in SENSITIVE_JSON_KEYS:
                if keyword in body["text"].lower():
                    body["text"] = body["text"].replace(keyword, "[redacted]")
                    modified = True
    if modified and not dry_run:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return modified


def main() -> int:
    args = parse_args()
    input_dir: Path = args.input_dir
    if not input_dir.exists():
        print(f"[sanitize] directory {input_dir} does not exist.")
        return 1
    changed = 0
    for file in sorted(input_dir.glob("*.json")):
        if sanitize_file(file, dry_run=args.dry_run):
            changed += 1
            if args.dry_run:
                print(f"[sanitize] would update {file}")
    print(f"[sanitize] {'modified' if not args.dry_run else 'checked'} {changed} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
