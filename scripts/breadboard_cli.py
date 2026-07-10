"""BreadBoard harness authoring command-line front door."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

EXIT_OK = 0
EXIT_VALIDATION_FAILURE = 2
EXIT_RESOLUTION_FAILURE = 3
EXIT_RUNTIME_FAILURE = 4
EXIT_LOCK_DRIFT = 5


def _not_implemented(command: str, item: str) -> int:
    """Report a planned command whose implementation belongs to a later packet."""
    print(
        f"bbh {command} is not implemented yet; see Phase 20 item {item}.",
        file=sys.stderr,
    )
    return EXIT_VALIDATION_FAILURE


def _add_leaf(
    namespace: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    *,
    help_text: str,
    command: str,
    item: str,
) -> argparse.ArgumentParser:
    parser = namespace.add_parser(name, help=help_text, description=help_text)
    parser.set_defaults(handler=lambda _args: _not_implemented(command, item))
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build the complete public command tree without importing command backends."""
    parser = argparse.ArgumentParser(
        prog="bbh",
        description="BreadBoard harness and E4 lane authoring front door.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="suppress nonessential output")

    namespaces = parser.add_subparsers(dest="namespace", required=True)

    harness = namespaces.add_parser(
        "harness", help="operate on product harness configurations"
    )
    harness_commands = harness.add_subparsers(dest="command", required=True)

    harness_init = _add_leaf(
        harness_commands,
        "init",
        help_text="create a minimal product harness configuration",
        command="harness init",
        item="G2",
    )
    harness_init.add_argument("--out", metavar="DIR")

    harness_validate = _add_leaf(
        harness_commands,
        "validate",
        help_text="validate a product harness configuration",
        command="harness validate",
        item="G2",
    )
    harness_validate.add_argument("PATH")

    harness_explain = _add_leaf(
        harness_commands,
        "explain",
        help_text="explain a resolved product harness configuration",
        command="harness explain",
        item="G2",
    )
    harness_explain.add_argument("PATH")
    harness_explain.add_argument("--strict", action="store_true")

    harness_run = _add_leaf(
        harness_commands,
        "run",
        help_text="run a product harness configuration",
        command="harness run",
        item="G3",
    )
    harness_run.add_argument("PATH")
    run_target = harness_run.add_mutually_exclusive_group()
    run_target.add_argument("--server", metavar="URL")
    run_target.add_argument("--local", action="store_true")
    harness_run.add_argument("--task", metavar="TEXT")

    lane = namespaces.add_parser(
        "lane", help="operate on E4 conformance lane manifests"
    )
    lane_commands = lane.add_subparsers(dest="command", required=True)

    lane_init = _add_leaf(
        lane_commands,
        "init",
        help_text="create an E4 lane manifest",
        command="lane init",
        item="G2",
    )
    lane_init.add_argument("--out", metavar="DIR")

    lane_validate = _add_leaf(
        lane_commands,
        "validate",
        help_text="validate an E4 lane manifest",
        command="lane validate",
        item="G2",
    )
    lane_validate.add_argument("PATH")

    lane_lock = _add_leaf(
        lane_commands,
        "lock",
        help_text="compile or check an E4 lane lock",
        command="lane lock",
        item="G4",
    )
    lane_lock.add_argument("PATH")
    lane_lock.add_argument("--check", action="store_true")
    lane_lock.add_argument("--out", metavar="DIR")

    lane_capture = _add_leaf(
        lane_commands,
        "capture",
        help_text="run the capture stage for an E4 lane manifest",
        command="lane capture",
        item="G4",
    )
    lane_capture.add_argument("MANIFEST")
    lane_capture.add_argument("--out", metavar="DIR")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch the selected command."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
