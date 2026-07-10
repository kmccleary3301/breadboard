#!/usr/bin/env python3
"""Project generated workspace lane inputs into the isolated checkout."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Sequence


DEFAULT_INPUTS = (
    "docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",
    "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
)


class LaneInputMaterializationError(ValueError):
    """The generated lane-input projection is unsafe or incomplete."""


def _resolve_root(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LaneInputMaterializationError(f"{label} must exist: {path}") from exc
    if not resolved.is_dir():
        raise LaneInputMaterializationError(f"{label} must be a directory: {resolved}")
    return resolved


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise LaneInputMaterializationError(
            f"lane input path must be a normalized relative path: {value}"
        )
    return path


def _bounded_path(root: Path, relative: PurePosixPath, *, label: str) -> Path:
    candidate = root.joinpath(*relative.parts)
    parent = candidate.parent.resolve(strict=False)
    if not parent.is_relative_to(root):
        raise LaneInputMaterializationError(f"{label} escapes root {root}: {relative}")
    return candidate


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_checkout_lane_inputs(
    *,
    repo_root: Path,
    workspace_root: Path,
    inputs: Sequence[str] = DEFAULT_INPUTS,
) -> tuple[str, ...]:
    """Copy exact generated workspace inputs into checkout-local adapter paths."""
    repo = _resolve_root(repo_root, label="repo root")
    workspace = _resolve_root(workspace_root, label="workspace root")
    if repo == workspace or repo.parent != workspace:
        raise LaneInputMaterializationError(
            f"repo root must be an immediate child of workspace root: repo={repo} workspace={workspace}"
        )

    written: list[str] = []
    for value in inputs:
        relative = _relative_path(value)
        source = _bounded_path(workspace, relative, label="workspace input")
        destination = _bounded_path(repo, relative, label="checkout input")
        if source.is_symlink() or not source.is_file():
            raise LaneInputMaterializationError(
                f"generated workspace input must be a regular file: {source}"
            )
        if destination.is_symlink():
            raise LaneInputMaterializationError(
                f"checkout lane input destination must not be a symlink: {destination}"
            )
        _write_atomic(destination, source.read_bytes())
        written.append(relative.as_posix())
    return tuple(written)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--input", action="append", dest="inputs")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        written = materialize_checkout_lane_inputs(
            repo_root=args.repo_root,
            workspace_root=args.workspace_root,
            inputs=tuple(args.inputs) if args.inputs else DEFAULT_INPUTS,
        )
    except LaneInputMaterializationError as exc:
        raise SystemExit(f"lane input materialization failed: {exc}") from exc
    result = {"ok": True, "written": list(written)}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"materialized {len(written)} checkout lane inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
