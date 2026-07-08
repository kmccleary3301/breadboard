from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from breadboard.rl.env_package.schema import EnvPackage, SCHEMA_VERSION
from breadboard.rl.env_package.validate import load_env_package, load_yaml_mapping, validate_env_package_mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples" / "rl_env_packages"
PYTHON_TOY = EXAMPLES / "python_console_toy" / "env_package.yaml"
SWE_TOY = EXAMPLES / "swe_toy_patch" / "env_package.yaml"


def load_example(path: Path) -> dict:
    return load_yaml_mapping(path)


def assert_invalid(payload: dict, expected_fragment: str) -> None:
    errors = validate_env_package_mapping(payload)
    assert any(expected_fragment in error for error in errors), errors


def test_golden_packages_validate_and_load_as_env_packages() -> None:
    for path in [PYTHON_TOY, SWE_TOY]:
        package = load_env_package(path)
        assert isinstance(package, EnvPackage)
        assert package.schema_version == SCHEMA_VERSION
        assert package.package_hash
        assert package.to_dict()["package_hash"] == package.package_hash


def test_missing_required_field_fails_with_specific_error() -> None:
    payload = load_example(PYTHON_TOY)
    payload.pop("verifier")

    assert_invalid(payload, "missing required field: verifier")
    assert_invalid(payload, "verifier must be a mapping")


def test_missing_package_hash_fails() -> None:
    payload = load_example(PYTHON_TOY)
    payload.pop("package_hash")

    assert_invalid(payload, "missing required field: package_hash")
    assert_invalid(payload, "package_hash must be non-empty")


def test_env_package_from_dict_raises_on_invalid_payload() -> None:
    payload = load_example(PYTHON_TOY)
    payload["schema_version"] = "wrong"

    with pytest.raises(ValueError, match="schema_version"):
        EnvPackage.from_dict(payload)


def test_split_must_be_allowed_by_owning_taskset() -> None:
    payload = deepcopy(load_example(PYTHON_TOY))
    payload["splits"]["not_allowed"] = deepcopy(payload["splits"]["train_probe"])
    payload["splits"]["not_allowed"]["split_id"] = "not_allowed"
    payload["splits"]["not_allowed"]["split_hash"] = "sha256:not-allowed"

    assert_invalid(payload, "splits.not_allowed is not listed in taskset allowed_splits")
