#!/usr/bin/env python3
"""Run the provider regression matrix harness.

This script ensures the necessary environment flags are set and then
invokes pytest for the regression matrix suite. It is intended for use
in CI or scheduled jobs to cover the provider × streaming × tool
combinations defined in P2.1.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    env = os.environ.copy()
    env.setdefault("AGENT_SCHEMA_V2_ENABLED", "1")
    env.setdefault("KC_DISABLE_PROVIDER_PROBES", "0")
    env.setdefault("RAY_SCE_LOCAL_MODE", "1")
    env.setdefault("OPENROUTER_API_KEY", env.get("OPENROUTER_API_KEY", "regression-dummy"))

    repo_root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "-m", "pytest", "tests/test_provider_regression_matrix.py", "-q"]
    print(f"[regression-matrix] Running {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=repo_root, env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
