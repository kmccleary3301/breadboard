from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5.f6_restart_replay_authoring import (
    F6RestartReplayAuthoringInput,
    build_f6_restart_replay_input,
    read_f6_restart_replay_authoring_input,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one source-closed F6 restart/cache/live-replay target input "
            "from exact current F3 production artifacts."
        )
    )
    parser.add_argument("--input", help="Absolute canonical F6 authoring input path")
    parser.add_argument("--output", help="New absolute F6 target input path")
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print the closed F6 authoring input JSON Schema",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.print_schema:
        sys.stdout.buffer.write(
            canonical_json_bytes(F6RestartReplayAuthoringInput.model_json_schema())
            + b"\n"
        )
        return 0
    if args.input is None or args.output is None:
        _parser().error("--input and --output are required")
    descriptor = build_f6_restart_replay_input(
        read_f6_restart_replay_authoring_input(args.input),
        os.fspath(Path(args.output)),
    )
    sys.stdout.buffer.write(
        canonical_json_bytes(descriptor.model_dump(mode="json")) + b"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
