"""Build the revision-pinned stock Codex patch parser and executor explicitly."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from breadboard_engine.execution.codex_exec import codex_patch_binary


def main() -> None:
    binary = codex_patch_binary()
    subprocess.run([
        "cargo", "build", "--locked", "--release", "--manifest-path",
        str(ROOT / "breadboard_engine/execution/codex_patch/Cargo.toml"),
        "--target-dir", str(binary.parent.parent),
    ], check=True)
    print(binary)


if __name__ == "__main__":
    main()
