from __future__ import annotations

import hashlib
import json
from importlib.resources import files

import pytest

import breadboard.rl.harness.sandbox as sandbox_module

from breadboard.rl.harness.sandbox import (
    SANDBOX_CAPABILITY_MATRIX_RESOURCE,
    SANDBOX_CAPABILITY_MATRIX_SHA256,
    SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION,
    load_sandbox_capability_matrix,
)


def test_installed_sandbox_capability_matrix_is_closed_and_truthful() -> None:
    matrix = load_sandbox_capability_matrix()

    assert matrix["schema_version"] == SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION
    assert matrix["workspace_root"] == "/testbed"
    adapters = {item["adapter_id"]: item for item in matrix["adapters"]}
    assert list(adapters) == ["docker", "firecracker", "gvisor", "process"]
    assert adapters["docker"]["status"] == "experimental"
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
    assert (
        hashlib.sha256(resource.read_bytes()).hexdigest()
        == SANDBOX_CAPABILITY_MATRIX_SHA256
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))

    assert payload["schema_version"] == SANDBOX_CAPABILITY_MATRIX_SCHEMA_VERSION
    assert payload["workspace_root"] == "/testbed"


def test_sandbox_capability_matrix_rejects_oversized_resource(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = tmp_path / SANDBOX_CAPABILITY_MATRIX_RESOURCE
    resource.write_bytes(b"x" * (64 * 1024 + 1))
    monkeypatch.setattr(sandbox_module, "files", lambda _package: tmp_path)

    with pytest.raises(sandbox_module.SandboxRuntimeError) as captured:
        load_sandbox_capability_matrix()

    assert captured.value.code == "capability_matrix_invalid"


def test_sandbox_capability_matrix_rejects_symlink_resource(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "matrix-target.json"
    target.write_bytes(
        files("breadboard.rl.harness")
        .joinpath(SANDBOX_CAPABILITY_MATRIX_RESOURCE)
        .read_bytes()
    )
    resource = tmp_path / SANDBOX_CAPABILITY_MATRIX_RESOURCE
    try:
        resource.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    monkeypatch.setattr(sandbox_module, "files", lambda _package: tmp_path)

    with pytest.raises(sandbox_module.SandboxRuntimeError) as captured:
        load_sandbox_capability_matrix()

    assert captured.value.code == "capability_matrix_invalid"
