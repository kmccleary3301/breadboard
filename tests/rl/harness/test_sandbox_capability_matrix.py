from __future__ import annotations

import json
from importlib.resources import files

import pytest

from breadboard.rl.harness.sandbox import (
    SANDBOX_CAPABILITY_MATRIX_RESOURCE,
    SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION,
    load_sandbox_capability_matrix,
)


def test_installed_sandbox_capability_matrix_is_closed_and_truthful() -> None:
    matrix = load_sandbox_capability_matrix()

    assert matrix["schema_version"] == SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION
    assert matrix["workspace_root"] == "/testbed"
    adapters = {item["adapter_id"]: item for item in matrix["adapters"]}
    assert list(adapters) == ["docker", "firecracker", "gvisor", "process"]
    assert adapters["docker"]["status"] == "ready"
    assert adapters["docker"]["capabilities"]["isolated"] is True
    assert adapters["docker"]["capabilities"]["persistent_workspace"] is True
    assert adapters["gvisor"]["status"] == "experimental"
    assert adapters["firecracker"]["status"] == "unsupported"
    assert adapters["process"]["status"] == "development_only"
    assert adapters["process"]["capabilities"]["isolated"] is False
    with pytest.raises(TypeError):
        matrix["workspace_root"] = "/weaker"


def test_sandbox_capability_matrix_is_an_installed_package_resource() -> None:
    resource = files("breadboard.rl.harness").joinpath(
        SANDBOX_CAPABILITY_MATRIX_RESOURCE
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))

    assert payload["schema_version"] == SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION
    assert payload["workspace_root"] == "/testbed"
