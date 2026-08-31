#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.product.evidence.e4 import promote_lane_payload_source as _owner  # noqa: E402


def __getattr__(name: str) -> Any:
    return getattr(_owner, name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_owner.__doc__)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        expected = _owner.canonical_source_bytes(
            _owner.extract_payload_source(args.legacy)
        )
    except (OSError, yaml.YAMLError, _owner.PayloadSourceError) as exc:
        parser.error(str(exc))
    if args.check:
        return (
            0 if args.output.is_file() and args.output.read_bytes() == expected else 5
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
