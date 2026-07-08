from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from breadboard.rl.env_package.validate import load_yaml_mapping, validate_env_package_mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def test_protected_split_cannot_be_trainer_visible() -> None:
    payload = deepcopy(load_yaml_mapping(SWE_TOY))
    payload["splits"]["protected_probe"]["trainer_visible"] = True

    errors = validate_env_package_mapping(payload)
    assert "splits.protected_probe protected split cannot be trainer_visible" in errors


def test_protected_split_cannot_be_optimizer_visible() -> None:
    payload = deepcopy(load_yaml_mapping(SWE_TOY))
    payload["splits"]["protected_probe"]["optimizer_visible"] = True

    errors = validate_env_package_mapping(payload)
    assert "splits.protected_probe protected split cannot be optimizer_visible" in errors
