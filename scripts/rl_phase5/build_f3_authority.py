from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5.f3_authority_authoring import (
    F3AuthorityInput,
    author_f3_authority,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one exact R-SWE-001 terminal authority bundle."
    )
    parser.add_argument("--input", help="Absolute path to canonical F3 authority JSON")
    parser.add_argument("--output", help="New absolute output directory")
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print the closed canonical input JSON Schema",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_schema:
        sys.stdout.buffer.write(canonical_json_bytes(F3AuthorityInput.model_json_schema()) + b"\n")
        return 0
    if args.input is None or args.output is None:
        _parser().error("--input and --output are required")
    manifest_path = author_f3_authority(args.input, args.output)
    sys.stdout.buffer.write(
        canonical_json_bytes({"manifest_path": manifest_path}) + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
