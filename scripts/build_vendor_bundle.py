#!/usr/bin/env python3
"""Bundle KyleCode logging artifacts for vendor escalation.

Collects the key diagnostic files from a logging run (capability probes,
structured requests, provider metrics, raw excerpts, and error payloads)
and packages them into a single ZIP archive that can be attached to
support tickets.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from typing import Iterable

DEFAULT_ARTIFACTS = [
    "conversation/conversation.md",
    "meta/capability_probes.json",
    "meta/provider_metrics.json",
    "meta/requests",
    "errors",
    "raw/responses",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a vendor escalation bundle from a KyleCode logging directory."
    )
    parser.add_argument(
        "log_dir",
        help="Path to a logging run directory (e.g. logging/20251014-220404_agent_ws_opencode)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output zip path (default: <log_dir>_vendor_bundle.zip)",
    )
    parser.add_argument(
        "--extra",
        nargs="*",
        default=[],
        help="Additional relative paths to include in the bundle",
    )
    return parser.parse_args()


def iter_artifacts(log_dir: Path, relative_paths: Iterable[str]) -> Iterable[Path]:
    for rel in relative_paths:
        candidate = log_dir / rel
        if not candidate.exists():
            continue
        if candidate.is_file():
            yield candidate
        else:
            for path in candidate.rglob("*"):
                if path.is_file():
                    yield path


def build_bundle(log_dir: Path, output_path: Path, relative_paths: Iterable[str]) -> None:
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in iter_artifacts(log_dir, relative_paths):
            arcname = path.relative_to(log_dir)
            zf.write(path, arcname)


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir).resolve()
    if not log_dir.exists():
        print(f"[error] Logging directory not found: {log_dir}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output).resolve()
        if args.output
        else log_dir.with_name(f"{log_dir.name}_vendor_bundle.zip")
    )

    relative_paths = DEFAULT_ARTIFACTS + list(args.extra or [])
    build_bundle(log_dir, output_path, relative_paths)
    print(f"[vendor-bundle] Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
