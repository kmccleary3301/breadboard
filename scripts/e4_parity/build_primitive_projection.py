#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.product.evidence.e4 import build_primitive_projection as _owner  # noqa: E402


def __getattr__(name: str) -> Any:
    return getattr(_owner, name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project Oh-My-Pi P6 L1/L2 packet data into BreadBoard kernel primitive records."
    )
    parser.add_argument("--output-dir", default=str(_owner.OUTPUT_DIR), help="Directory for projection JSON artifacts")
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing artifacts")
    args = parser.parse_args(argv)

    try:
        report = _owner.build_projection(output_dir=Path(args.output_dir), write=not args.dry_run)
    except Exception as exc:  # pragma: no cover - CLI defensive path.
        if args.json:
            print(_owner._canonical_json_bytes({"error": str(exc), "ok": False}).decode("utf-8"), end="")
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(_owner._canonical_json_bytes(report).decode("utf-8"), end="")
    else:
        for output in report["outputs"]:
            print(f"{output['status']}: {output['path']} {output['sha256']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
