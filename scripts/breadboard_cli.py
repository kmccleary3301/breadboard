"""Thin installed entry point for the BreadBoard product CLI."""
from __future__ import annotations

from collections.abc import Sequence

from breadboard.product.cli.main import build_parser, main as _main

__all__ = ["build_parser", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch to the installed product CLI without importing command backends."""
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
