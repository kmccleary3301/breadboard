from __future__ import annotations

from pathlib import Path

import pytest

from breadboard.rl.harness.sandbox import (
    OPENHANDS_SDK_LOCAL_ADAPTER_ID,
    PI_CODING_AGENT_LOCAL_ADAPTER_ID,
    WorkspaceStateError,
    _admit_native_phase_payload,
)


@pytest.mark.parametrize("authority_key", ("workspace", "scratch", "package_dir"))
def test_initialize_rejects_caller_supplied_authority(authority_key: str) -> None:
    payload = {authority_key: "caller-owned"}

    with pytest.raises(WorkspaceStateError) as captured:
        _admit_native_phase_payload(
            "initialize",
            payload,
            adapter_id=PI_CODING_AGENT_LOCAL_ADAPTER_ID,
            workspace=Path("/lease/repository"),
            scratch=Path("/lease/scratch"),
            runtime_root=Path("/sealed/pi"),
        )

    assert captured.value.code == "workspace_authority_mismatch"
    assert payload == {authority_key: "caller-owned"}


def test_initialize_injects_lease_authority_and_pinned_pi_package() -> None:
    payload = {"task": "owned task", "advertisement": {"tools": {}}}

    admitted = _admit_native_phase_payload(
        "initialize",
        payload,
        adapter_id=PI_CODING_AGENT_LOCAL_ADAPTER_ID,
        workspace=Path("/lease/repository"),
        scratch=Path("/lease/scratch"),
        runtime_root=Path("/sealed/pi"),
    )

    assert admitted == {
        "task": "owned task",
        "advertisement": {"tools": {}},
        "workspace": "/lease/repository",
        "scratch": "/lease/scratch",
        "package_dir": "/sealed/pi/node_modules/@mariozechner/pi-coding-agent",
    }
    assert "workspace" not in payload
    assert "scratch" not in payload
    assert "package_dir" not in payload


def test_non_initialize_phase_preserves_payload_without_authority_injection() -> None:
    payload = {"workspace": "caller", "package_dir": "caller", "value": 1}

    admitted = _admit_native_phase_payload(
        "project_request",
        payload,
        adapter_id=PI_CODING_AGENT_LOCAL_ADAPTER_ID,
        workspace=Path("/lease/repository"),
        scratch=Path("/lease/scratch"),
        runtime_root=Path("/sealed/pi"),
    )

    assert admitted == payload
    assert admitted is not payload


def test_adapter_without_package_dir_does_not_receive_pi_package_path() -> None:
    admitted = _admit_native_phase_payload(
        "initialize",
        {"task": "openhands"},
        adapter_id=OPENHANDS_SDK_LOCAL_ADAPTER_ID,
        workspace="/lease/repository",
        scratch="/lease/scratch",
        runtime_root="/sealed/openhands",
    )

    assert admitted["workspace"] == "/lease/repository"
    assert admitted["scratch"] == "/lease/scratch"
    assert "package_dir" not in admitted
