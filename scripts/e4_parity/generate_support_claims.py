#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.product.evidence.e4 import generate_support_claims as _owner  # noqa: E402


def __getattr__(name: str) -> Any:
    return getattr(_owner, name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate lifecycle-default E4 support claims from inventory, catalog, "
            "and comparator reports."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--defer-node-gates",
        action="store_true",
        help="leave node-gate reports to the canonical lane reverify stages",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = _owner.generate(
        dry_run=args.dry_run,
        update_node_gates=not args.defer_node_gates,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"generated {report['claim_count']} support_claim records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
