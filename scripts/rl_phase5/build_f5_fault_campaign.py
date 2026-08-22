from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5.f5_fault_campaign import (
    F5CampaignInput,
    author_f5_fault_campaign,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and validate one closed canonical F5 fault campaign."
    )
    parser.add_argument("--input", help="Absolute path to canonical F5 campaign JSON")
    parser.add_argument("--output", help="New absolute output directory")
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print the closed canonical F5 campaign input JSON Schema",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_schema:
        sys.stdout.buffer.write(
            canonical_json_bytes(F5CampaignInput.model_json_schema()) + b"\n"
        )
        return 0
    if args.input is None or args.output is None:
        _parser().error("--input and --output are required")
    artifacts = author_f5_fault_campaign(args.input, args.output)
    sys.stdout.buffer.write(
        canonical_json_bytes(artifacts.model_dump(mode="json")) + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
