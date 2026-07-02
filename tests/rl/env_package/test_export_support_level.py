from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from breadboard.rl.env_package.hash import canonical_env_package_hash
from breadboard.rl.env_package.validate import load_yaml_mapping, validate_env_package_mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_supported_export_requires_support_evidence_refs() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["exports"]["support_level"] = "supported"
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert "exports.support_level=supported requires support_evidence_refs" in validate_env_package_mapping(payload)


def test_non_trainable_export_cannot_use_trainable_status() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["exports"]["trainability_status"] = "rl_candidate"
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert "non-trainable exports cannot use trainable trainability_status" in validate_env_package_mapping(payload)


def test_trainable_unknown_contamination_scope_fails() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["exports"]["trainable"] = True
    payload["exports"]["trainability_status"] = "rl_candidate"
    payload["provenance"]["contamination_scope"] = "unknown"
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert "trainable packages must not use unknown contamination_scope" in validate_env_package_mapping(payload)


def test_trainable_replay_disabled_fails() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["exports"]["trainable"] = True
    payload["exports"]["trainability_status"] = "rl_candidate"
    payload["replay"]["replay_required"] = False
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert "trainable packages require replay.replay_required" in validate_env_package_mapping(payload)


def test_trainable_protected_contamination_scope_fails() -> None:
    payload = deepcopy(load_yaml_mapping(PYTHON_TOY))
    payload["exports"]["trainable"] = True
    payload["exports"]["trainability_status"] = "rl_candidate"
    payload["provenance"]["contamination_scope"] = "dev_hidden"
    payload["package_hash"] = canonical_env_package_hash(payload)

    assert "protected contamination scopes cannot be marked trainable" in validate_env_package_mapping(payload)
