"""Build the pinned BM25 helper before using Codex client tool discovery."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from breadboard_engine.execution.codex_search import codex_search_binary


def main() -> None:
    binary = codex_search_binary()
    subprocess.run([
        "cargo", "build", "--locked", "--release", "--manifest-path",
        str(ROOT / "breadboard_engine/execution/codex_search/Cargo.toml"),
        "--target-dir", str(binary.parent.parent),
    ], check=True)
    print(binary)


if __name__ == "__main__":
    main()
