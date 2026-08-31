#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.product.evidence.e4 import compile_lane_lock as _owner  # noqa: E402

ROOT = _owner.ROOT
_COMPAT_OVERRIDE_NAMES = ("ROOT", "SOURCE_FREEZE_EXTRACTION_REF", "_artifact_roles")


def __getattr__(name: str) -> Any:
    return getattr(_owner, name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=_owner.__doc__)
    parser.add_argument("mode", choices=("migrate", "compile"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    overrides = {
        name: globals()[name] for name in _COMPAT_OVERRIDE_NAMES if name in globals()
    }
    previous = {name: getattr(_owner, name) for name in overrides}
    for name, value in overrides.items():
        setattr(_owner, name, value)
    try:
        try:
            args = _parser().parse_args(argv)
            result = _owner.compile_manifest(
                args.manifest,
                mode=args.mode,
                legacy_path=args.legacy,
                lock_path=args.lock,
                sidecar_path=args.sidecar,
                check=args.check,
            )
            return 0 if result.matches else 5
        except _owner.ManifestError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except _owner.ReferenceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
    finally:
        for name, value in previous.items():
            setattr(_owner, name, value)


if __name__ == "__main__":
    raise SystemExit(main())
