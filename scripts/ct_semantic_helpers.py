from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_json(value: str) -> Any:
    return json.loads(root_path(value).read_text(encoding="utf-8"))


def as_dict(value: Any, pointer: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{pointer} must be an object")
    return value


def as_list(value: Any, pointer: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{pointer} must be an array")
    return value


def expected(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("expected_output") or data.get("expected") or {}
    return as_dict(value, "/expected")


def compare(actual: dict[str, Any], exp: dict[str, Any], keys: list[str] | None = None) -> list[str]:
    keys = list(exp) if keys is None else keys
    violations: list[str] = []
    derived_keys = {"ok", "violation_count", "violations", "error_count"}
    for key in keys:
        if key in derived_keys and key not in actual:
            continue
        if key in exp and actual.get(key) != exp.get(key):
            violations.append(f"{key} expected {exp.get(key)!r}, got {actual.get(key)!r}")
    return violations


def report(actual: dict[str, Any], violations: list[str], *, schema_version: str, mode: str, payload_path: str) -> dict[str, Any]:
    out = dict(actual)
    out["violation_count"] = len(violations)
    out["violations"] = violations
    out["ok"] = not violations
    out["schema_version"] = schema_version
    out["mode"] = mode
    out["payload_path"] = payload_path
    out.setdefault("error_count", 0)
    return out


def run_mode(checks: dict[str, Callable[[dict[str, Any]], dict[str, Any]]], *, schema_version: str, payload_flag: str = "--payload", extra_flags: tuple[str, ...] = ()) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=sorted(checks))
    parser.add_argument(payload_flag, dest="payload", required=True)
    parser.add_argument("--json-out", required=True)
    for flag in extra_flags:
        parser.add_argument(flag, action="append", default=[])
    args = parser.parse_args()
    try:
        data = as_dict(load_json(args.payload), "/")
        out = checks[args.mode](data)
        out.setdefault("schema_version", schema_version)
        out.setdefault("mode", args.mode)
        out.setdefault("payload_path", args.payload)
        out.setdefault("error_count", 0)
    except Exception as exc:
        out = {"ok": False, "schema_version": schema_version, "mode": args.mode, "payload_path": args.payload, "error_count": 1, "violation_count": 1, "violations": [str(exc)]}
    out_path = root_path(args.json_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, sort_keys=True))
    return 0 if out.get("ok") is True else 1
