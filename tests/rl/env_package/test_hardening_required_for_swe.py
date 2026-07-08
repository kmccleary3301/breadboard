from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from breadboard.rl.env_package.validate import load_yaml_mapping, validate_env_package_mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def test_swe_package_requires_hardening_policy() -> None:
    payload = deepcopy(load_yaml_mapping(SWE_TOY))
    payload["hardening"] = None

    assert "SWE packages require hardening policy" in validate_env_package_mapping(payload)


def test_untrusted_runtime_requires_hardening_policy() -> None:
    payload = deepcopy(load_yaml_mapping(SWE_TOY))
    payload["package_id"] = "bb.patch_toy.v1alpha"
    payload["tasksets"][0]["source_kind"] = "local_fixture"
    payload["harness"]["interaction_mode"] = "multi_turn"
    payload["hardening"] = None

    assert "untrusted runtime packages require hardening policy" in validate_env_package_mapping(payload)


def test_hardening_forbids_root_agent() -> None:
    payload = deepcopy(load_yaml_mapping(SWE_TOY))
    payload["runtime"]["agent_user"] = "root"

    assert "hardening.agent_non_root_required forbids runtime.agent_user=root" in validate_env_package_mapping(payload)


def test_hardening_requires_verifier_isolation() -> None:
    payload = deepcopy(load_yaml_mapping(SWE_TOY))
    payload["verifier"]["isolated_from_agent"] = False

    errors = validate_env_package_mapping(payload)
    assert "hardening.verifier_isolated_required requires verifier.isolated_from_agent=true" in errors
