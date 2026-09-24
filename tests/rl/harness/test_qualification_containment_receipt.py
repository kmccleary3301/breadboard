from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness.headless import (
    HeadlessRunRequest,
    HeadlessWorkspaceInput,
    ObsoleteOuterIsolationError,
)
from breadboard.rl.harness.lease_envelope import RuntimeContainment, verify_containment_receipt
from breadboard.rl.harness.qualification import (
    materialize_production_composition_fixture,
)
from breadboard.rl.harness.runners.base import RunnerOpenRequest, RunnerPlanError
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_ADAPTER_ID,
    CONDUCTOR_IMPLEMENTATION_DIGEST,
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
    PolicyRuntimeBinding,
)
from tests.rl.harness.test_runner_conductor import (
    RecordingCancellationProbe,
    RecordingEventSink,
    RecordingToolPort,
    _open,
)
from tests.rl.harness.test_runner_policy_runtime import RecordingPolicyClient, _observation, _plan
from tests.rl.harness.test_sandbox_runtime import RuntimeHarness
from tests.rl.harness.v2_service_fixtures import signed_containment_receipt
from tests.rl.harness.wp7_fixtures import make_runtime_fixture


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
async def test_public_qualification_entry_rejects_unconfined_trusted_process() -> None:
    """Public conductor admission rejects a trusted-process workspace without a receipt."""
    tools = RecordingToolPort()
    tools.containment = RuntimeContainment.UNCONFINED_TEST_ONLY
    tools.containment_receipt = None
    with pytest.raises(RunnerPlanError) as caught:
        await _open(tools=tools)
    assert caught.value.code == "containment_receipt_invalid"


async def _open_with_ledger(
    adapter: ConductorAdapter, tools: RecordingToolPort, *, runtime_id: str = "sandbox"
) -> Any:
    observation = _observation()
    plan = _plan(
        observation=observation,
        implementation_digest=CONDUCTOR_IMPLEMENTATION_DIGEST,
        sandbox_runtime_id=runtime_id,
    )
    request = RunnerOpenRequest(episode_id="episode-a", effective_plan=plan)
    return await adapter.open(
        request,
        policy=PolicyRuntimeBinding(request, RecordingPolicyClient(observation)),
        workspace=tools,
        cancellation=RecordingCancellationProbe(),
        events=RecordingEventSink(),
    )


@pytest.mark.asyncio
async def test_composer_signed_counterfeit_without_manager_admission_is_rejected(tmp_path: Path) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    composition = load_production_composition(str(fixture.composition_ref_path), fixture.secret_files)
    try:
        adapter = composition.service._dependencies.runner_registry.resolve(
            CONDUCTOR_ADAPTER_ID, CONDUCTOR_RUNTIME_ABI
        )
        tools = RecordingToolPort()
        tools.containment_receipt = signed_containment_receipt(
            tools.containment_lease_id, "sandbox", composition.authority_graph.authenticator
        )
        with pytest.raises(RunnerPlanError) as caught:
            await _open_with_ledger(adapter, tools)
        assert caught.value.code == "containment_receipt_invalid"
    finally:
        await composition.close()


@pytest.mark.asyncio
async def test_admitted_receipt_requires_exact_live_lease(tmp_path: Path) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    try:
        assert not hasattr(lease.runner_workspace, "containment_authenticator")
        authenticator = harness.manager._containment_authenticator
        adapter = ConductorAdapter(
            CONDUCTOR_RUNTIME_ABI,
            containment_authenticator=authenticator,
            admitted_lease_ledger=harness.manager.admitted_lease_ledger,
        )
        tools = RecordingToolPort()
        tools.containment_lease_id = lease.lease_id
        original = lease.runner_workspace.containment_receipt
        tools.containment_receipt = original
        session = await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
        await session.close()

        unsigned = replace(original, created_at="2026-09-25T00:00:00Z")
        tools.containment_receipt = replace(
            unsigned, signature=authenticator.sign(unsigned.canonical_bytes())
        )
        verify_containment_receipt(
            tools.containment_receipt,
            lease_id=lease.lease_id,
            runtime_id="trusted-process",
            authenticator=authenticator,
        )
        with pytest.raises(RunnerPlanError) as modified:
            await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
        assert modified.value.code == "containment_receipt_invalid"

        tools.containment_receipt = original
        await lease.close()
        with pytest.raises(RunnerPlanError) as replayed:
            await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
        assert replayed.value.code == "containment_receipt_invalid"
    finally:
        await harness.manager.close()

@pytest.mark.asyncio
async def test_verifier_admission_is_removed_at_teardown(tmp_path: Path) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    primary = await harness.manager.open(fixture.request)
    try:
        snapshot = await primary.seal_for_verifier()
        verifier = await harness.manager.open_verifier(primary, snapshot)
        assert harness.manager.admitted_lease_ledger.lookup(verifier.lease_id) is not None
        await verifier.close()
        assert harness.manager.admitted_lease_ledger.lookup(verifier.lease_id) is None
    finally:
        await primary.close()
        await harness.manager.close()


@pytest.mark.asyncio
async def test_production_composition_close_stabilizes_directory_fds(tmp_path: Path) -> None:
    baseline = len(os.listdir("/dev/fd"))
    for index in range(5):
        fixture = materialize_production_composition_fixture(tmp_path / str(index))
        composition = load_production_composition(
            str(fixture.composition_ref_path), fixture.secret_files
        )
        await composition.close()
        assert len(os.listdir("/dev/fd")) == baseline
