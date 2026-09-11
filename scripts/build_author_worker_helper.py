from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OCI_PACKAGE = ROOT / "sdk" / "ts-execution-driver-oci"
TARGET = ROOT / "breadboard_engine" / "execution" / "node"


def main() -> None:
    subprocess.run(["npm", "run", "build"], cwd=OCI_PACKAGE, check=True)
    TARGET.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "npm", "exec", "--", "esbuild", "src/author-bridge-helper.ts",
            "--bundle", "--platform=node", "--target=node20", "--format=esm",
            "--banner:js=import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);",
            f"--outfile={TARGET / 'author-bridge-helper.mjs'}",
        ],
        cwd=OCI_PACKAGE,
        check=True,
    )


if __name__ == "__main__":
    main()
