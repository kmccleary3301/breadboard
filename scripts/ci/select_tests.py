#!/usr/bin/env python3
"""Select conservative Python and Node test targets from changed paths."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]

# These are the selector's supported Node test targets. The workspace package
# has no lockfile, so its package-local source is full-only; CI installs that
# target with npm install rather than npm ci.
NODE_TARGETS = tuple(
    sorted(
        (
            "sdk/ts",
            "sdk/ts-backbone",
            "sdk/ts-execution-driver-local",
            "sdk/ts-execution-driver-oci",
            "sdk/ts-execution-driver-remote",
            "sdk/ts-execution-drivers",
            "sdk/ts-host-bridges",
            "sdk/ts-host-kits",
            "sdk/ts-host-t3",
            "sdk/ts-kernel-contracts",
            "sdk/ts-kernel-core",
            "sdk/ts-orchestration-temporal",
            "sdk/ts-transport-ai-sdk",
            "sdk/ts-workspace",
            "tui_skeleton",
            "vscode_sidebar",
        )
    )
)

# These source trees are leaf test targets in the supported package manifests;
# SDK/kernel packages outside the temporal adapter remain full-suite.
NARROW_SOURCE_TARGETS = {
    "sdk/ts-orchestration-temporal": "sdk/ts-orchestration-temporal",
    "tui_skeleton": "tui_skeleton",
    "vscode_sidebar": "vscode_sidebar",
}
SOURCE_SUFFIXES = {".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}
DOCUMENTATION_PATHS = frozenset(
    {
        "docs/ci/affected-tests.md",
        "docs/ci/consolidation.md",
    }
)
TEST_OR_FIXTURE_PARTS = {
    "__fixtures__",
    "__tests__",
    "fixture",
    "fixtures",
    "test",
    "tests",
}
CONFIG_NAMES = {
    ".babelrc",
    ".npmrc",
    "babel.config.js",
    "babel.config.cjs",
    "babel.config.mjs",
    "jest.config.js",
    "jest.config.cjs",
    "jest.config.mjs",
    "rollup.config.js",
    "rollup.config.mjs",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
    "vitest.config.js",
    "vitest.config.ts",
    "webpack.config.js",
    "webpack.config.cjs",
    "webpack.config.mjs",
}


@dataclass(frozen=True)
class Selection:
    full: bool
    python: bool
    node_targets: tuple[str, ...]


def _full_selection() -> Selection:
    return Selection(full=True, python=True, node_targets=NODE_TARGETS)


def _normalized_parts(path: str) -> tuple[str, ...] | None:
    if not path or "\\" in path:
        return None
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        return None
    return parsed.parts


def _is_documentation(parts: tuple[str, ...]) -> bool:
    return "/".join(parts) in DOCUMENTATION_PATHS


def _narrow_source_target(parts: tuple[str, ...]) -> str | None:
    if not parts or any(part.lower() in TEST_OR_FIXTURE_PARTS for part in parts):
        return None

    is_sdk_package = parts[0] == "sdk"
    source_index = 2 if is_sdk_package else 1
    if len(parts) <= source_index + 1:
        return None

    package = "/".join(parts[:2]) if is_sdk_package else parts[0]

    target = NARROW_SOURCE_TARGETS.get(package)
    if target is None or parts[source_index] != "src":
        return None

    path = PurePosixPath(*parts)
    filename = path.name.lower()
    if (
        path.suffix.lower() not in SOURCE_SUFFIXES
        or filename in CONFIG_NAMES
        or ".config." in filename
        or filename.startswith("tsconfig.")
    ):
        return None
    return target


def select_paths(paths: Iterable[str]) -> Selection:
    """Select tests for repository-relative changed paths.

    Every path outside a small, reviewed documentation/source allowlist takes
    the full path. This includes all Python changes and SDK/kernel changes
    outside the allowlisted temporal adapter, plus tests, fixtures, and build
    configuration.
    """
    normalized = [_normalized_parts(path) for path in paths]
    if not normalized or any(parts is None for parts in normalized):
        return _full_selection()

    valid_parts = [parts for parts in normalized if parts is not None]
    if all(_is_documentation(parts) for parts in valid_parts):
        return Selection(full=False, python=False, node_targets=())

    targets: set[str] = set()
    for parts in valid_parts:
        if _is_documentation(parts):
            continue
        target = _narrow_source_target(parts)
        if target is None:
            return _full_selection()
        targets.add(target)

    if not targets:
        return _full_selection()
    return Selection(full=False, python=False, node_targets=tuple(sorted(targets)))


def parse_name_status(data: bytes) -> tuple[str, ...] | None:
    """Parse `git diff --name-status -z`, retaining both rename paths."""
    if not data:
        return ()
    fields = data.split(b"\0")
    if fields[-1] == b"":
        fields.pop()

    paths: list[str] = []
    index = 0
    try:
        while index < len(fields):
            status = fields[index].decode("ascii")
            index += 1
            if status.startswith(("R", "C")):
                if len(status) < 2 or not status[1:].isdigit():
                    return None
                count = 2
            elif len(status) == 1 and status in "ACDMRTUXB":
                count = 1
            else:
                return None
            if index + count > len(fields):
                return None
            paths.extend(field.decode("utf-8", errors="surrogateescape") for field in fields[index : index + count])
            index += count
    except UnicodeDecodeError:
        return None
    return tuple(paths)


def changed_paths(base: str, head: str, repo_root: Path = ROOT) -> tuple[str, ...] | None:
    """Return changed paths, or None when the git diff cannot be trusted."""
    try:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--find-renames", "--name-status", "-z", base, head, "--"],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_name_status(result.stdout)


def _write_github_output(path: Path, selection: Selection) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"full={str(selection.full).lower()}\n")
        output.write(f"python={str(selection.python).lower()}\n")
        output.write(f"node_targets={json.dumps(list(selection.node_targets), separators=(',', ':'))}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--base", help="base commit for git diff")
    source.add_argument("--paths", nargs="*", metavar="PATH", help="changed paths (deterministic test/smoke input)")
    parser.add_argument("--head", help="head commit for git diff")
    parser.add_argument("--github-output", required=True, type=Path, help="GitHub Actions output file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.paths is None and (args.base is None or args.head is None):
        parser.error("provide both --base and --head, or use --paths")
    if args.paths is not None and args.head is not None:
        parser.error("--head is only valid with --base")

    if args.paths is not None:
        selection = select_paths(args.paths)
    else:
        paths = changed_paths(args.base, args.head)
        if paths is None:
            print("selector: git diff failed or returned malformed output; selecting the full suite", file=sys.stderr)
            selection = _full_selection()
        else:
            selection = select_paths(paths)

    try:
        _write_github_output(args.github_output, selection)
    except OSError as error:
        print(f"selector: unable to write GitHub output: {error}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(selection), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
