from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5.f4_authority_authoring import build_f4_target_input, read_f4_authoring_input

def main() -> int:
    parser = argparse.ArgumentParser(description="Build the F4 V2 multi-config production composition and target input")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = build_f4_target_input(read_f4_authoring_input(args.input), os.fspath(Path(args.output_dir).resolve()))
    os.write(1, canonical_json_bytes(result.model_dump(mode="json")) + b"\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
