from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.evidence import canonical_g2_g3_contract_report_bytes
from breadboard.rl.phase5.server_authority import start_phase5_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(argv)
    server = start_phase5_server()
    if not server.has_existing_artifact() or not server.has_existing_graph():
        raise ValueError("Phase 5 report requires existing deployment authority state")
    sys.stdout.buffer.write(canonical_g2_g3_contract_report_bytes() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
