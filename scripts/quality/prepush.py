#!/usr/bin/env python3
"""Run configured changed-file checks and mapped pytest targets before pushing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ZERO = "0" * 40
BUDGET = 28.0  # Preserve reporting headroom below the 30-second fast-lane limit.
TEST_LOCK = "/srv/km-migration/bb-s2-scratch/s2lock.sh"
PYTHON_SUFFIXES = {".py"}
JS_SUFFIXES = {".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}


def python_executable() -> str:
    return os.environ.get(
        "BB_PREPUSH_PYTHON",
        (
            str(Path(".venv/bin/python").resolve())
            if Path(".venv/bin/python").exists()
            else sys.executable
        ),
    )


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def parse_ranges(text: str) -> list[tuple[str, str]]:
    ranges = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("malformed pre-push ref update")
        _, local, _, remote = fields
        if not all(re.fullmatch(r"[0-9a-f]{40}", oid) for oid in (local, remote)):
            raise ValueError("invalid push object id")
        if local != ZERO:
            ranges.append((local, remote))
    return ranges


def changed_files(text: str, remote_name: str) -> list[str]:
    ranges = parse_ranges(text)
    paths: set[str] = set()
    for local, remote in ranges:
        if remote == ZERO:
            remote = git("merge-base", local, f"refs/remotes/{remote_name}/main")
        raw = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMRT",
                "--no-renames",
                "-z",
                remote,
                local,
                "--",
            ]
        )
        paths.update(os.fsdecode(path) for path in raw.split(b"\0") if path)

    head = git("rev-parse", "HEAD")
    if any(local != head for local, _ in ranges):
        raise ValueError("check out each pushed head before running fast checks")
    if paths:
        dirty = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=no",
                "--",
                *sorted(paths),
            ]
        )
        if dirty:
            raise ValueError("changed push files have uncommitted edits; commit or stash them")
    return sorted(path for path in paths if Path(path).is_file())


def map_tests(paths: list[str], seams: dict[str, list[str]]) -> list[str]:
    tests: set[str] = set()
    for name in paths:
        path = Path(name)
        tests.update(seams.get(name, []))
        if path.suffix != ".py":
            continue
        if path.name.startswith("test_") and "tests" in path.parts:
            tests.add(name)
        else:
            tests.update(
                str(candidate)
                for candidate in (
                    path.with_name(f"test_{path.stem}.py"),
                    Path("tests") / path.parent / f"test_{path.stem}.py",
                    Path("tests") / f"test_{path.stem}.py",
                )
                if candidate.is_file()
            )
    return sorted(test for test in tests if Path(test).is_file())


def check_commands(paths: list[str]) -> list[tuple[str, list[str], Path]]:
    commands = []
    python_files = [path for path in paths if Path(path).suffix in PYTHON_SUFFIXES]
    if python_files:
        print("pre-push: no configured Python formatter/linter; checking syntax")
        commands.append(
            (
                "python syntax",
                [python_executable(), "-m", "py_compile", "--", *python_files],
                Path("."),
            )
        )

    packages: dict[Path, list[str]] = {}
    for name in paths:
        path = Path(name)
        if path.suffix not in JS_SUFFIXES:
            continue
        for parent in path.parents:
            if (parent / "package.json").is_file():
                packages.setdefault(parent, []).append(str(path.relative_to(parent)))
                break

    for parent, files in packages.items():
        package = json.loads((parent / "package.json").read_text(encoding="utf-8"))
        dependencies = package.get("devDependencies", {}) | package.get("dependencies", {})
        configured_tools = False
        for tool, arguments in (("prettier", ["--check"]), ("eslint", [])):
            if tool in dependencies:
                configured_tools = True
                commands.append(
                    (
                        tool,
                        [
                            str((parent / "node_modules/.bin" / tool).resolve()),
                            *arguments,
                            "--",
                            *files,
                        ],
                        parent,
                    )
                )
        if not configured_tools:
            print(f"pre-push: {parent}: no configured JavaScript formatter/linter")

    return commands


def run(
    command: list[str], cwd: Path, timeout: float, *, focused: bool = False
) -> int | None:
    """Bound the owned process group; focused-test spawn failures may skip."""
    try:
        process = subprocess.Popen(command, cwd=cwd, start_new_session=True)
    except OSError as exc:
        if not focused:
            raise
        print(f"FOCUSED-TESTS-NOT-RUN: {exc}; CI covers them")
        return 0

    with process:
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            return None


def run_tests(tests: list[str], remaining: float) -> int:
    if not tests:
        print("pre-push: no mapped focused tests; CI covers the affected set")
        return 0
    if 5.0 * len(tests) > remaining:
        print("FOCUSED-TESTS-NOT-RUN: 30 s total budget; CI covers them")
        return 0

    command = [python_executable(), "-m", "pytest", "-q", *tests]
    if os.path.isfile(TEST_LOCK) and os.access(TEST_LOCK, os.X_OK):
        command = [TEST_LOCK, "s", "--", *command]
    else:
        print("pre-push: focused-test lock absent or not executable; running directly")

    print(f"pre-push: running {len(tests)} focused test file(s)", flush=True)
    result = run(command, Path("."), min(25.0, remaining), focused=True)
    if result is None or result == 75:
        reason = "lock busy" if result == 75 else "budget expired"
        print(f"FOCUSED-TESTS-NOT-RUN: {reason}; CI covers them")
        return 0
    return result


def main() -> int:
    started = time.monotonic()
    checks_done = started
    try:
        if os.environ.get("BB_PREPUSH_SKIP") == "1":
            print("pre-push: fast checks skipped (BB_PREPUSH_SKIP=1)")
            return 0

        paths = changed_files(sys.stdin.read(), sys.argv[1])
        print(f"pre-push: {len(paths)} changed files", flush=True)
        for label, command, cwd in check_commands(paths):
            remaining = BUDGET - (time.monotonic() - started)
            if remaining <= 0:
                raise ValueError("fast check budget exhausted; CI required or BB_PREPUSH_SKIP=1")
            step = time.monotonic()
            result = run(command, cwd, remaining)
            print(f"pre-push timing: {label}={time.monotonic() - step:.3f}s", flush=True)
            if result is None:
                raise ValueError(f"{label} exceeded fast check budget")
            if result:
                return result

        seams = json.loads(
            Path(".githooks/prepush-test-map.json").read_text(encoding="utf-8")
        )
        tests = map_tests(paths, seams)
        checks_done = time.monotonic()
        remaining = BUDGET - (checks_done - started)
        return run_tests(tests, remaining)
    except (OSError, ValueError, subprocess.CalledProcessError, IndexError) as exc:
        print(f"pre-push: {exc}", file=sys.stderr)
        return 1
    finally:
        ended = time.monotonic()
        print(
            "pre-push timing: "
            f"checks={checks_done - started:.3f}s "
            f"tests/report={ended - checks_done:.3f}s "
            f"fast-total={ended - started:.3f}s",
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
