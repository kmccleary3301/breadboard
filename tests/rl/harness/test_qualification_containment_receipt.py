from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import (
    load_production_composition,
    _PinnedTrustedProcessBackend,
)
from breadboard.rl.harness.headless import (
    HeadlessRunRequest,
    HeadlessWorkspaceInput,
    ObsoleteOuterIsolationError,
)
from breadboard.rl.harness.lease_envelope import RuntimeContainment
from breadboard.rl.harness.qualification import (
    materialize_production_composition_fixture,
)
from breadboard.rl.harness.sandbox import SandboxLaunchError


def test_headless_workspace_input_rejects_obsolete_outer_isolation() -> None:
    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessWorkspaceInput(
            base_commit="a" * 40,
            task_image_digest="sha256:" + "0" * 64,
            outer_isolation="apptainer",
        )

    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessWorkspaceInput.model_validate(
            {
                "base_commit": "a" * 40,
                "task_image_digest": "sha256:" + "0" * 64,
                "outer_isolation": "apptainer",
            }
        )

    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessWorkspaceInput.model_validate_json(
            '{"base_commit": "'
            + "a" * 40
            + '", "task_image_digest": "sha256:'
            + "0" * 64
            + '", "outer_isolation": "apptainer"}'
        )


def test_headless_run_request_rejects_obsolete_outer_isolation() -> None:
    raw_request: dict[str, Any] = {
        "schema_version": "bb.rl.headless-run-request.v1",
        "episode_id": "ep-1",
        "result_path": "/tmp/result.json",
        "event_log_path": "/tmp/event.log",
        "prompt": "test prompt",
        "context": {},
        "tool_allowlist": ["bash"],
        "resolve_request": {
            "schema_version": "bb.rl.resolve-episode-request.v1",
            "episode_id": "ep-1",
            "subject": {"authority_id": "test", "authority_scope_digest": "sha256:" + "0" * 64},
            "selector": {"digest": "sha256:" + "0" * 64, "ref": "cas://selector"},
            "selection_nonce": None,
            "task": {
                "task_id": "task-1",
                "task_binding_digest": "sha256:" + "0" * 64,
                "repository_snapshot_digest": None,
                "dataset_digests": (),
                "input_artifact_digests": (),
            },
            "policy_binding": {
                "route_id": "route-1",
                "registry_revision_digest": "sha256:" + "0" * 64,
                "attestation_digest": "sha256:" + "0" * 64,
            },
            "episode_overlays": (),
        },
        "workspace": {
            "base_commit": "a" * 40,
            "task_image_digest": "sha256:" + "0" * 64,
            "outer_isolation": "apptainer",
        },
        "expected_resources": {
            "cpu_cores": 1,
            "memory_bytes": 1024,
            "disk_bytes": 1024,
            "wall_time_ms": 1000,
        },
        "expected_limits": {
            "action_timeout_ms": 1000,
            "output_bytes": 1024,
            "observation_bytes": 1024,
        },
        "expected_sandbox": {
            "image_digest": "sha256:" + "0" * 64,
            "network_policy_digest": "sha256:" + "0" * 64,
            "security_policy_digest": "sha256:" + "0" * 64,
        },
        "provider": {
            "model": "model-1",
            "authority_model_id": "auth-model-1",
            "credential_handle": "cred-1",
            "context_window": 4096,
            "max_output_tokens": 1024,
            "timeout_seconds": 1.0,
        },
    }
    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessRunRequest.model_validate(raw_request)

    top_level_request = dict(raw_request)
    top_level_request["outer_isolation"] = "apptainer"
    top_level_request["workspace"] = {
        "base_commit": "a" * 40,
        "task_image_digest": "sha256:" + "0" * 64,
    }
    with pytest.raises(ObsoleteOuterIsolationError):
        HeadlessRunRequest.model_validate(top_level_request)


@pytest.mark.asyncio
async def test_public_qualification_entry_rejects_unconfined_trusted_process(
    tmp_path: Path,
) -> None:
    """Prove that UNCONFINED_TEST_ONLY is unreachable and rejected by public qualification entry."""
    fixture = materialize_production_composition_fixture(tmp_path)
    composition = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    backend = composition.service._dependencies.sandbox_runtime.process_backend
    assert isinstance(backend, _PinnedTrustedProcessBackend)

    unconfined_plan = MagicMock()
    unconfined_plan.containment = RuntimeContainment.UNCONFINED_TEST_ONLY
    with pytest.raises(
        SandboxLaunchError,
        match="production composition rejects unconfined trusted-process execution",
    ):
        await backend.launch(unconfined_plan)


def test_headless_trusted_process_rejects_unconfined_lane() -> None:
    inp = HeadlessWorkspaceInput(
        base_commit="a" * 40,
        task_image_digest="sha256:" + "0" * 64,
    )
    assert inp.containment == "attested"
