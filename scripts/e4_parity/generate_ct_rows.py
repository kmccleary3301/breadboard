#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.product.evidence.e4 import generate_ct_rows as _owner  # noqa: E402


def __getattr__(name: str) -> Any:
    return getattr(_owner, name)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate inventory-backed C4 CT scenario rows."
    )
    parser.add_argument("--inventory", default=str(_owner.DEFAULT_INVENTORY))
    parser.add_argument("--manifest", default=str(_owner.DEFAULT_MANIFEST))
    parser.add_argument(
        "--out", default=None, help="write merged manifest to this path"
    )
    parser.add_argument(
        "--rows-out", default=None, help="write generated inventory rows to this path"
    )
    parser.add_argument(
        "--lane-defs",
        default=str(_owner.DEFAULT_LANE_DEFS),
        help="directory of bb.e4.lane_def.v1 YAML files",
    )
    parser.add_argument(
        "--retired-evidence-pins",
        default=str(_owner.DEFAULT_RETIRED_EVIDENCE_PINS),
        help="hash pins for validation-only retired evidence",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated inventory rows differ from manifest rows",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    inventory_path = Path(args.inventory)
    manifest_path = Path(args.manifest)
    inventory = _owner.load_json(inventory_path)
    manifest = _owner.load_json(manifest_path)
    rows = _owner.generate_inventory_scenarios(
        inventory,
        lane_defs=_owner.load_lane_defs(Path(args.lane_defs)),
        retired_evidence_pins=_owner.load_retired_evidence_pins(
            Path(args.retired_evidence_pins)
        ),
    )

    managed_test_ids = _owner.inventory_ct_test_ids(inventory)
    if args.rows_out:
        _owner.write_json(Path(args.rows_out), rows)
    if args.out:
        _owner.write_json(
            Path(args.out),
            _owner.merge_inventory_scenarios(
                manifest,
                rows,
                managed_test_ids=managed_test_ids,
            ),
        )

    diffs = _owner.field_level_diffs(
        manifest,
        rows,
        ignored_fields={"description"},
        managed_test_ids=managed_test_ids,
    )
    errors = (
        _owner.mismatches(manifest, rows, managed_test_ids=managed_test_ids)
        if args.check
        else []
    )
    print(
        json.dumps(
            {
                "description_diff_count": sum(
                    1
                    for diff in diffs
                    if diff["fields"] and not diff["non_ignored_fields"]
                ),
                "field_diff_count": len(diffs),
                "generated_row_count": len(rows),
                "inventory": str(inventory_path),
                "manifest": str(manifest_path),
                "mismatch_count": len(errors),
                "ok": not errors,
                "errors": errors[:20],
            },
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
