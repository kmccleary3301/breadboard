#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(*, log_path: Path, schema_path: Path) -> dict[str, Any]:
    payload = _load_json(log_path)
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    rows = payload if isinstance(payload, list) else []
    errors: list[str] = []
    if not isinstance(payload, list):
        errors.append("repair policy log must be a JSON list")
    for index, row in enumerate(rows):
        for err in validator.iter_errors(row):
            loc = ".".join(str(part) for part in err.path)
            prefix = f"repair_policy_log[{index}]"
            errors.append(f"{prefix}{'.' + loc if loc else ''}: {err.message}")

    return {
        "schema": "breadboard.atp.repair_policy_log_report.v1",
        "log_path": str(log_path.resolve()),
        "schema_path": str(schema_path.resolve()),
        "row_count": len(rows),
        "error_count": len(errors),
        "errors": errors,
        "ok": len(errors) == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--schema", default="docs/contracts/atp/schemas/atp_repair_policy_decision_v1.schema.json")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = evaluate(
        log_path=Path(args.log).resolve(),
        schema_path=Path(args.schema).resolve(),
    )
    if args.json_out:
        out = Path(args.json_out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = "PASS" if report["ok"] else "FAIL"
        print(f"[atp-repair-policy-log-v1] status={status} rows={report['row_count']} errors={report['error_count']}")
        for error in report["errors"]:
            print(f"error: {error}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
