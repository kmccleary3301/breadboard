#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e4_parity.soak_runtime_emission import build_soak_report as _build_soak_report
from scripts.e4_parity.soak_runtime_emission import main as _main


def build_soak_report(*, out_dir: Path, sessions: int = 25, provider_latency_ms: int = 500) -> dict[str, Any]:
    """Run the WS-C strict runtime-emission soak and return its JSON report."""

    return _build_soak_report(out_dir=out_dir, sessions=sessions, provider_latency_ms=provider_latency_ms)


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
