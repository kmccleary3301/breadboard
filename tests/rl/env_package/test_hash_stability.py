from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from breadboard.rl.env_package.hash import canonical_env_package_hash
from breadboard.rl.env_package.validate import load_yaml_mapping, validate_env_package_mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_canonical_hash_matches_declared_hash() -> None:
    payload = load_yaml_mapping(PYTHON_TOY)

    assert validate_env_package_mapping(payload) == []
    assert payload["package_hash"] == canonical_env_package_hash(payload)


def test_canonical_hash_ignores_declared_package_hash_value() -> None:
    payload = load_yaml_mapping(PYTHON_TOY)
    mutated = deepcopy(payload)
    mutated["package_hash"] = "sha256:not-the-real-hash"

    assert canonical_env_package_hash(payload) == canonical_env_package_hash(mutated)


def test_canonical_hash_is_stable_under_mapping_order_changes() -> None:
    payload = load_yaml_mapping(PYTHON_TOY)
    reordered = {
        "exports": payload["exports"],
        "schema_version": payload["schema_version"],
        "runtime": payload["runtime"],
        "splits": payload["splits"],
        "provenance": payload["provenance"],
        "version": payload["version"],
        "package_id": payload["package_id"],
        "package_hash": payload["package_hash"],
        "tasksets": payload["tasksets"],
        "harness": payload["harness"],
        "verifier": payload["verifier"],
        "reward": payload["reward"],
        "renderer": payload["renderer"],
        "hardening": payload["hardening"],
        "replay": payload["replay"],
    }

    assert canonical_env_package_hash(payload) == canonical_env_package_hash(reordered)


def test_canonical_hash_changes_under_semantic_edit() -> None:
    payload = load_yaml_mapping(PYTHON_TOY)
    edited = deepcopy(payload)
    edited["runtime"]["backend"] = "docker"

    assert canonical_env_package_hash(payload) != canonical_env_package_hash(edited)


def test_declared_hash_mismatch_fails_validation() -> None:
    payload = load_yaml_mapping(PYTHON_TOY)
    payload["package_hash"] = "sha256:bad"

    assert "package_hash does not match canonical EnvPackage hash" in validate_env_package_mapping(payload)
