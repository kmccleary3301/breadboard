"""Build the pinned Codex image codec before using the dated native tools profile."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from breadboard_engine.execution.codex_exec import codex_image_binary


def main() -> None:
    binary = codex_image_binary()
    subprocess.run([
        "cargo", "build", "--locked", "--release", "--manifest-path",
        str(ROOT / "breadboard_engine/execution/codex_image/Cargo.toml"),
        "--target-dir", str(binary.parent.parent),
    ], check=True)
    print(binary)


if __name__ == "__main__":
    main()
