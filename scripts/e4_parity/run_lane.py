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

from breadboard.product.evidence.e4 import run_lane as _owner  # noqa: E402


def __getattr__(name: str) -> Any:
    return getattr(_owner, name)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an E4 lane from lane_def/inventory data."
    )
    parser.add_argument(
        "--lane",
        required=True,
        help="lane_id from config/e4_lanes, or north-star for the WS-J lane set",
    )
    parser.add_argument(
        "--stage",
        choices=[*_owner.ALL_STAGES, "all"],
        default="all",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="scratch output root for all artifact writes",
    )
    parser.add_argument(
        "--promote-accepted",
        action="store_true",
        help="allow accepted-root artifact writes for promotion regeneration",
    )
    parser.add_argument(
        "--defer-promotion-refresh",
        action="store_true",
        help=(
            "skip immediate promoted-binding refresh; use only inside orchestrators "
            "that run explicit catalog/support refresh stages later"
        ),
    )
    parser.add_argument(
        "--defer-derived-writes",
        action="store_true",
        help=(
            "isolate capture-derived claims/manifests/node gates and promote only "
            "lane-owned outputs"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit JSON result")
    parser.add_argument(
        "--lane-def-dir",
        type=Path,
        default=_owner.DEFAULT_LANE_DEF_DIR,
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=_owner.DEFAULT_INVENTORY,
    )
    parser.add_argument(
        "--comparator-registry",
        type=Path,
        default=_owner.DEFAULT_COMPARATOR_REGISTRY,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    lane_ids = (
        list(_owner.NORTH_STAR_LANE_IDS) if args.lane == "north-star" else [args.lane]
    )
    rows: list[dict[str, Any]] = []
    exit_code = 0
    for lane_id in lane_ids:
        try:
            result = _owner.run_lane(
                lane_id,
                stage=args.stage,
                out_dir=args.out,
                lane_def_dir=args.lane_def_dir,
                inventory_path=args.inventory,
                comparator_registry_path=args.comparator_registry,
                promote_accepted=args.promote_accepted,
                defer_promotion_refresh=args.defer_promotion_refresh,
                defer_derived_writes=args.defer_derived_writes,
            )
        except (_owner.LaneRunError, ValueError) as exc:
            payload = {"ok": False, "lane_id": lane_id, "error": str(exc)}
            rows.append(payload)
            exit_code = 5 if isinstance(exc, _owner.LaneLockDriftError) else 2
            if not args.json:
                print(f"run_lane: {exc}", file=sys.stderr)
            break
        rows.append(result)
        if not result["ok"]:
            exit_code = 1
            break
    payload = {"ok": exit_code == 0, "lanes": rows} if len(lane_ids) > 1 else rows[0]
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for result in rows:
            if not result.get("ok"):
                continue
            for item in result["stages"]:
                output = item.get("output_path", "<metadata>")
                sha = item.get("output_sha256", "<missing>")
                print(
                    f"{item['stage']} {item['lane_id']} "
                    f"rc={item['returncode']} output={output} sha={sha}"
                )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
