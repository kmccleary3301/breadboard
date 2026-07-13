from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package


REPO_ROOT = Path(__file__).resolve().parents[3]
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def load_swe_hardening_policy():
    package = load_env_package(SWE_TOY)
    assert package.hardening is not None
    return package.hardening
