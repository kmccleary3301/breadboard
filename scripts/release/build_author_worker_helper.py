"""Build the standalone helper shipped in the installed wheel.

When changing bundled dependencies, refresh node/THIRD_PARTY_NOTICES.txt beside
the helper from the exact locked upstream packages. Include transitive packages
and distinct versions reached through the linked SDKs; retain their full notices.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OCI_PACKAGE = ROOT / "sdk" / "ts-execution-driver-oci"
TARGET = ROOT / "breadboard_engine" / "execution" / "node"


def _local_tool(name: str) -> Path:
    tool = OCI_PACKAGE / "node_modules" / ".bin" / name
    if not tool.is_file():
        raise FileNotFoundError(
            f"missing admitted local OCI build tool: {tool}; install the frozen SDK inputs before building"
        )
    return tool


def main() -> None:
    tsc = _local_tool("tsc")
    esbuild = _local_tool("esbuild")
    subprocess.run([str(tsc), "-p", "tsconfig.json"], cwd=OCI_PACKAGE, check=True)
    TARGET.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(esbuild), "src/author-bridge-helper.ts",
            "--bundle", "--platform=node", "--target=node20", "--format=esm",
            "--banner:js=import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);",
            f"--outfile={TARGET / 'author-bridge-helper.mjs'}",
        ],
        cwd=OCI_PACKAGE,
        check=True,
    )


if __name__ == "__main__":
    main()
