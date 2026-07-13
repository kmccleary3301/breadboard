from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from breadboard.rl.env_package.hash import canonical_env_package_hash
from breadboard.rl.env_package.validate import load_yaml_mapping, validate_env_package_mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_full_network_requires_allowlist_reason() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["runtime"]["network"] = "full"
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert "runtime.network=full requires network_allowlist_reason" in validate_env_package_mapping(payload)


def test_full_network_with_allowlist_reason_validates() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["runtime"]["network"] = "full"
    payload["runtime"]["network_allowlist_reason"] = "fixture-localhost-only"
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert validate_env_package_mapping(payload) == []
