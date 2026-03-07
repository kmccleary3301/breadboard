#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

APPROVED_STATUSES = {
    "approved_full",
    "approved_internal_only",
    "approved_with_restrictions",
    "operator_override_internal_only",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scope",
        default="config/compliance/aristotle_permission_scope_v1.json",
    )
    parser.add_argument("--require-external-reporting", action="store_true")
    args = parser.parse_args()

    path = Path(args.scope).resolve()
    if not path.exists():
        print(f"missing scope file: {path}")
        return 2

    payload = json.loads(path.read_text(encoding="utf-8"))
    status = str(payload.get("status") or "").strip().lower()
    permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}

    required = [
        "benchmark_evaluation_public_datasets",
        "store_outputs_for_reproducibility",
        "internal_comparative_reporting",
    ]
    if args.require_external_reporting:
        required.append("external_aggregate_reporting")

    missing = [
        key
        for key in required
        if not bool((permissions.get(key) if isinstance(permissions.get(key), dict) else {}).get("approved", False))
    ]
    status_ok = status in APPROVED_STATUSES
    ok = status_ok and not missing
    print(
        json.dumps(
            {
                "ok": ok,
                "status": status,
                "status_ok": status_ok,
                "missing_permissions": missing,
                "scope_path": str(path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
