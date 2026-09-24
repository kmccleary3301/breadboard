from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest

from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness.headless import (
    HeadlessRunRequest,
    HeadlessWorkspaceInput,
    ObsoleteOuterIsolationError,
)
from breadboard.rl.harness.lease_envelope import (
    ContainmentReceipt,
    ContainmentReceiptError,
    RuntimeContainment,
    WritableMount,
    add_teardown_outcome,
    verify_containment_receipt,
)
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
from tests.rl.harness.test_sandbox_runtime import RecordingBackend, RuntimeHarness
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
@pytest.mark.parametrize(
    "mutation",
    ("unknown_top_level", "unknown_namespace", "present_optional_outcome_null",
     "unknown_mount", "unknown_outcome"),
)
async def test_admitted_receipt_rejects_presented_extra_or_null_keys(
    tmp_path: Path, mutation: str
) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    try:
        authenticator = harness.manager._containment_authenticator
        adapter = ConductorAdapter(
            CONDUCTOR_RUNTIME_ABI,
            containment_authenticator=authenticator,
            admitted_lease_ledger=harness.manager.admitted_lease_ledger,
        )
        original = lease.runner_workspace.containment_receipt
        assert ContainmentReceipt.from_mapping(original.to_mapping()) == original
        teardown = add_teardown_outcome(
            original, pid1_reaped=True, all_dead=True, authenticator=authenticator
        )
        assert ContainmentReceipt.from_mapping(teardown.to_mapping()) == teardown
        presented = original.to_mapping()
        assert "outcome" not in presented
        if mutation == "unknown_top_level":
            presented["unadmitted_claim"] = "untrusted"
        elif mutation == "unknown_namespace":
            presented["namespaces"]["unadmitted_claim"] = 123
        elif mutation == "unknown_mount":
            presented["writable_mounts"][0]["unadmitted_claim"] = 123
        elif mutation == "unknown_outcome":
            presented["outcome"] = {
                "pid1_reaped": True, "all_dead": True, "unadmitted_claim": 123
            }
        else:
            presented["outcome"] = None
        with pytest.raises(ContainmentReceiptError):
            ContainmentReceipt.from_mapping(presented)
        tools = RecordingToolPort()
        tools.containment_lease_id = lease.lease_id
        tools.containment_receipt = presented
        with pytest.raises(RunnerPlanError) as caught:
            await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
        assert caught.value.code == "containment_receipt_invalid"
    finally:
        await lease.close()
        await harness.manager.close()


@pytest.mark.asyncio
async def test_two_mount_receipt_path_type_error_is_typed(tmp_path: Path) -> None:
    class TwoMountBackend(RecordingBackend):
        async def launch(self, plan, workspace, *, context):
            handle, measurement = await super().launch(plan, workspace, context=context)
            original = handle.containment_receipt
            unsigned = replace(
                original,
                writable_mounts=(
                    WritableMount("/scratch", "tmpfs", 4096, "lease_tmpfs"),
                    *original.writable_mounts,
                ),
            )
            handle.containment_receipt = replace(
                unsigned, signature=self.containment_authenticator.sign(unsigned.canonical_bytes())
            )
            return handle, measurement

    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture, backend=TwoMountBackend())
    lease = await harness.manager.open(fixture.request)
    try:
        adapter = ConductorAdapter(
            CONDUCTOR_RUNTIME_ABI,
            containment_authenticator=harness.manager._containment_authenticator,
            admitted_lease_ledger=harness.manager.admitted_lease_ledger,
        )
        tools = RecordingToolPort()
        tools.containment_lease_id = lease.lease_id
        tools.containment_receipt = lease.containment_receipt.to_mapping()
        session = await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
        await session.close()
        assert len(tools.containment_receipt["writable_mounts"]) == 2
        tools.containment_receipt["writable_mounts"][1]["path"] = 123
        with pytest.raises(ContainmentReceiptError):
            ContainmentReceipt.from_mapping(tools.containment_receipt)
        with pytest.raises(RunnerPlanError) as caught:
            await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
        assert caught.value.code == "containment_receipt_invalid"
    finally:
        await lease.close()
        await harness.manager.close()


@pytest.mark.asyncio
async def test_receipt_schema_type_swaps_are_typed_at_parser_and_admission(tmp_path: Path) -> None:
    fixture = make_runtime_fixture(with_writable_mount=True)
    harness = RuntimeHarness(tmp_path, fixture)
    lease = await harness.manager.open(fixture.request)
    try:
        original = lease.containment_receipt
        authenticator = harness.manager._containment_authenticator
        adapter = ConductorAdapter(
            CONDUCTOR_RUNTIME_ABI,
            containment_authenticator=authenticator,
            admitted_lease_ledger=harness.manager.admitted_lease_ledger,
        )
        base = original.to_mapping()
        outcome = add_teardown_outcome(
            original, pid1_reaped=True, all_dead=True, authenticator=authenticator
        ).to_mapping()
        two_mounts = deepcopy(base)
        two_mounts["writable_mounts"].insert(
            0, WritableMount("/scratch", "tmpfs", 4096, "lease_tmpfs").to_mapping()
        )
        ContainmentReceipt.from_mapping(two_mounts)
        receipt_types = get_type_hints(ContainmentReceipt)
        mount_types = get_type_hints(WritableMount)
        assert all(
            set(mount) == {field.name for field in fields(WritableMount)}
            for mount in two_mounts["writable_mounts"]
        )
        inode_fields = [field for field in fields(ContainmentReceipt) if field.name.endswith("_namespace_inode")]
        assert len(inode_fields) == len(base["namespaces"])
        assert all(receipt_types[field.name] is int for field in inode_fields)
        assert bool in get_args(get_args(receipt_types["outcome"])[0])
        assert all(
            str in (get_args(mount_types[name]) or (mount_types[name],))
            or int in (get_args(mount_types[name]) or (mount_types[name],))
            for name in base["writable_mounts"][0]
        )

        def wrong_type(value: Any) -> Any:
            if type(value) is str:
                return 123
            if type(value) is int or value is None:
                return True
            if type(value) is bool:
                return 1
            raise AssertionError(f"unexpected receipt schema value: {value!r}")

        mutations: list[tuple[str, Any]] = []
        for key, value in base.items():
            if isinstance(value, (dict, list)):
                continue
            changed = deepcopy(base)
            changed[key] = wrong_type(value)
            mutations.append((f"top.{key}", changed))
        for section, source in (("namespaces", base), ("outcome", outcome)):
            for key, value in source[section].items():
                changed = deepcopy(source)
                changed[section][key] = wrong_type(value)
                mutations.append((f"{section}.{key}", changed))
        for index, mount in enumerate(two_mounts["writable_mounts"]):
            for key, value in mount.items():
                changed = deepcopy(two_mounts)
                changed["writable_mounts"][index][key] = wrong_type(value)
                mutations.append((f"writable_mounts[{index}].{key}", changed))
        for section, replacement in (
            ("namespaces", ["not a mapping"]),
            ("outcome", ["not a mapping"]),
            ("writable_mounts", {"not": "a list"}),
            ("writable_mounts", ("not a list",)),
        ):
            changed = deepcopy(outcome if section == "outcome" else base)
            changed[section] = replacement
            mutations.append((f"{section}.container", changed))
        changed = deepcopy(base)
        changed["writable_mounts"][0] = ["not a mapping"]
        mutations.append(("writable_mounts.element", changed))
        mutations.extend((f"receipt.{type(value).__name__}", value) for value in ([], "text", b"bytes", None))

        class ExplodingMapping(Mapping):
            def __getitem__(self, key: str) -> Any:
                return base[key]

            def __iter__(self):
                raise RuntimeError("malformed mapping iterator")

            def __len__(self) -> int:
                return len(base)

        mutations.append(("receipt.exploding_mapping", ExplodingMapping()))

        for name, presented in mutations:
            with pytest.raises(ContainmentReceiptError):
                ContainmentReceipt.from_mapping(presented)
            tools = RecordingToolPort()
            tools.containment_lease_id = lease.lease_id
            tools.containment_receipt = presented
            with pytest.raises(RunnerPlanError) as caught:
                await _open_with_ledger(adapter, tools, runtime_id="trusted-process")
            assert caught.value.code == "containment_receipt_invalid", name
    finally:
        await lease.close()
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
