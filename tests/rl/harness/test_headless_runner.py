from __future__ import annotations

import json
import hashlib
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.artifacts import InMemoryCAS
from breadboard.rl.harness.service import EpisodePrimaryDisposition, V2RunResult
from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness.headless import (
    HeadlessRunFailed,
    HeadlessProviderInput,
    HeadlessProviderRouteAuthority,
    HeadlessRunRequest,
    HeadlessWorkspaceInput,
    _atomic_write,
    _project_headless_run,
    _validate_repository_base_commit_binding,
    run_headless_request,
)
from breadboard.rl.harness.runners.base import freeze_json_object, thaw_json

from breadboard.rl.harness.qualification import (
    materialize_production_composition_fixture,
)
from tests.rl.harness.e4_compiler_test_helper import compile_pi_target
def test_headless_workspace_mode_preserves_repository_identity_and_rejects_mixed_states() -> None:
    task_image = "sha256:" + "0" * 64
    legacy = HeadlessWorkspaceInput(
        repository_snapshot_digest=None,
        base_commit="1" * 40,
        task_image_digest=task_image,
    )
    assert legacy.identity_dict() == {
        "repository_snapshot_digest": None,
        "base_commit": "1" * 40,
        "task_image_digest": task_image,
        "outer_isolation": None,
    }
    seeded = HeadlessWorkspaceInput(
        workspace_mode="seeded",
        workspace_directory_mode=0o755,
        workspace_seed_digest="sha256:" + "1" * 64,
        task_image_digest=task_image,
    )
    _validate_repository_base_commit_binding(
        HeadlessRunRequest.model_construct(workspace=seeded),
        {},
    )
    with pytest.raises(ValueError):
        HeadlessWorkspaceInput(
            workspace_mode="seeded",
            base_commit="1" * 40,
            task_image_digest=task_image,
        )


def test_atomic_result_publication_refuses_existing_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.json"
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        _atomic_write(str(destination), b"replacement")

    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [destination]


def test_headless_projection_preserves_evidence_without_fabricating_patches() -> None:
    patch = b"diff --git a/a.py b/a.py\n"
    events = b'{"event":"done"}\n'

    cas = InMemoryCAS()
    events_ref = cas.put_bytes(events, media_type="application/json")
    artifacts_ref = cas.put_bytes(
        json.dumps({"objects": [{"role": "patch", "payload": "runner-result-json"}]}).encode(),
        media_type="application/json",
    )
    manifest_ref = cas.put_bytes(
        json.dumps({
            "runner_ledger_ref": events_ref.to_dict(),
            "artifact_manifest_ref": artifacts_ref.to_dict(),
        }).encode(),
        media_type="application/json",
    )
    composition = SimpleNamespace(
        authority_graph=SimpleNamespace(cas=cas),
    )
    run = V2RunResult(
        episode_id="projection-test",
        create_fingerprint="sha256:" + "0" * 64,
        run_fingerprint="sha256:" + "1" * 64,
        primary_disposition=EpisodePrimaryDisposition.SUCCEEDED,
        termination="completed",
        turn_count=1,
        response=freeze_json_object(
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]},
            field_name="runner response",
        ),
        completed_envelope_ref=None,
        closed_envelope_ref=None,
        result_ref=None,
        evidence_manifest_ref=manifest_ref,
        evidence_root="sha256:" + "0" * 64,
        artifact_manifest_ref=None,
        primary_measurement_digest="sha256:" + "1" * 64,
        verifier_measurement_digest="sha256:" + "2" * 64,
        verifier_result_digest="sha256:" + "3" * 64,
        reward=0.0,
        reward_components={},
        workspace_diff={
            "returncode": 0,
            "stdout": patch.decode(),
            "stderr": "",
            "base_commit": "0" * 40,
            "git_executable_digest": "sha256:" + "4" * 64,
            "patch_digest": "sha256:" + hashlib.sha256(patch).hexdigest(),
            "snapshot_root_digest": "sha256:" + "5" * 64,
        },
    )
    result: dict[str, Any] = {}

    with pytest.raises(ValueError):
        _project_headless_run(
            {}, run, composition, expected_base_commit="1" * 40
        )
    event_bytes, patch_bytes = _project_headless_run(
        result, run, composition, expected_base_commit="0" * 40
    )

    assert event_bytes == events
    assert patch_bytes == patch
    assert result["workspace_evidence"]["patch_digest"] == (
        "sha256:" + hashlib.sha256(patch).hexdigest()
    )
    assert json.loads(json.dumps(result))["terminal"]["response"] == {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]
    }
    seed_digest = "sha256:" + "6" * 64
    seeded_workspace = HeadlessWorkspaceInput(
        workspace_mode="seeded",
        workspace_seed_digest=seed_digest,
        task_image_digest="sha256:" + "7" * 64,
    )
    assert seeded_workspace.workspace_mode == "seeded"
    seeded_run = replace(
        run,
        workspace_diff={**run.workspace_diff, "base_commit": seed_digest},
    )
    seeded_result: dict[str, Any] = {}
    _project_headless_run(
        seeded_result,
        seeded_run,
        composition,
        expected_base_commit=seed_digest,
    )
    assert seeded_result["workspace_evidence"]["patch_base_commit"] == seed_digest

    with pytest.raises(ValueError):
        _project_headless_run(
            {}, replace(run, workspace_diff=None), composition,
            expected_base_commit="0" * 40,
        )
    cancelled_run = replace(
        run, primary_disposition=EpisodePrimaryDisposition.CANCELLED,
        response=None, termination=None, turn_count=0, workspace_diff=None,
    )
    cancelled: dict[str, Any] = {}
    event_bytes, patch_bytes = _project_headless_run(
        cancelled, cancelled_run, composition, expected_base_commit="0" * 40,
    )
    assert event_bytes == events
    assert patch_bytes is None
    assert cancelled["terminal"]["status"] == "cancelled"
    assert cancelled["workspace_evidence"]["runner_event_ledger_digest"] == (
        "sha256:" + hashlib.sha256(events).hexdigest()
    )
    failed_run = replace(
        run, primary_disposition=EpisodePrimaryDisposition.FAILED,
        response=None, termination=None, turn_count=2, workspace_diff=None,
    )
    failed: dict[str, Any] = {}
    event_bytes, patch_bytes = _project_headless_run(
        failed, failed_run, composition, expected_base_commit="0" * 40,
    )
    assert event_bytes == events
    assert patch_bytes is None
    assert failed["terminal"]["status"] == "failed"
    assert failed["workspace_evidence"]["runner_event_ledger_digest"] == (
        "sha256:" + hashlib.sha256(events).hexdigest()
    )


@pytest.mark.parametrize(
    "base_url",
    (
        "http://127.0.0.1:0/v1",
        "http://localhost:8000/v1",
        "https://192.0.2.1:443/v1",
    ),
)
def test_provider_requires_usable_literal_loopback_authority(base_url: str) -> None:
    with pytest.raises(ValueError, match="explicit loopback port"):
        HeadlessProviderRouteAuthority(
            model="Qwen/Qwen3.5-35B-A3B",
            authority_model_id="qwen3.5-35b-a3b",
            base_url=base_url,
            policy_observation_digest="sha256:" + "0" * 64,
        )


@pytest.mark.asyncio
async def test_composition_loader_uses_admitted_ref_bytes(tmp_path: Path) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    admitted_data = fixture.composition_ref_path.read_bytes()
    fixture.composition_ref_path.write_bytes(b'{"schema_version":"replaced"}')

    composition = load_production_composition(
        str(fixture.composition_ref_path),
        fixture.secret_files,
        composition_ref_data=admitted_data,
    )
    await composition.close()


def test_target_semantics_reject_changed_tool_parameter_schema(
    tmp_path: Path,
) -> None:
    target, semantics = compile_pi_target(tmp_path)
    target.validate_semantics(semantics)

    changed_semantics = thaw_json(semantics)
    parameter_schema = changed_semantics["tools"]["definitions"][0]["parameters"][0][
        "schema"
    ]
    parameter_schema["type"] = "number"

    with pytest.raises(ValueError):
        target.validate_semantics(
            freeze_json_object(
                changed_semantics,
                field_name="changed target semantics",
            )
        )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="composition fixtures require POSIX file authorities",
)
@pytest.mark.parametrize(
    ("request_schema", "runtime_class"),
    (
        ("bb.rl.headless-run-request.v1", c.RuntimeClass.TRUSTED_PROCESS),
        ("bb.rl.headless-run-request.v1", c.RuntimeClass.HARDENED_DOCKER),
        ("bb.rl.headless-run-request.v2", c.RuntimeClass.HARDENED_DOCKER),
    ),
)
@pytest.mark.asyncio
async def test_headless_runner_rejects_unadmitted_requests_before_credentials(
    tmp_path: Path,
    request_schema: str,
    runtime_class: c.RuntimeClass,
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    resolution = c.ResolveEpisodeRequest.model_validate(
        fixture.create_body["resolution"]
    )
    result_path = tmp_path / "result.json"
    event_path = tmp_path / "events.json"
    task_image_digest = "sha256:" + "0" * 64
    request = HeadlessRunRequest(
        schema_version=request_schema,
        target_id="pi@0.57.1",
        target_overlay_id="r3-json-no-session.v1",
        target_dynamic_fields={
            "readme_path": "README.md",
            "docs_path": "docs",
            "examples_path": "examples",
            "current_date_time": "2026-09-14T00:00:00Z",
            "cwd": "/workspace",
        },
        resolve_request=resolution,
        prompt="Repair the task and verify the result.",
        tool_allowlist=("shell",),
        context={"campaign": "e4"},
        workspace=HeadlessWorkspaceInput(
            repository_snapshot_digest=None,
            base_commit="0" * 40,
            task_image_digest=task_image_digest,
        ),
        expected_resources=c.ResourceLimits(
            cpu_millis=1_000,
            memory_bytes=1_000_000,
            pids=32,
            storage_bytes=1_000_000,
            open_files=128,
            wall_time_ms=60_000,
        ),
        expected_limits=c.ExecutionLimits(
            max_turns=4,
            action_timeout_ms=9_000,
            observation_bytes=20_000,
            response_bytes=100_000,
            artifact_bytes_each=10_000,
            artifact_bytes_total=20_000,
            transcript_bytes=100_000,
            setup_timeout_ms=5_000,
            verifier_timeout_ms=17_000,
        ),
        expected_sandbox=c.SandboxGrant(
            runtime_id="fixture-trusted-process",
            runtime_class=runtime_class,
            driver_implementation_digest=task_image_digest,
            runtime_binary_digest="sha256:" + "1" * 64,
            security_policy_digest="sha256:" + "2" * 64,
            image_digest=task_image_digest,
            network_policy_digest="sha256:" + "3" * 64,
            egress_route_ids=(),
            mounts=(),
        ),
        provider=HeadlessProviderInput(
            model="Qwen/Qwen3.5-35B-A3B",
            authority_model_id="qwen3.5-35b-a3b",
            credential_handle="policy-callback",
            context_window=131_072,
            max_output_tokens=32_000,
            timeout_seconds=30,
        ),
        result_path=str(result_path),
        event_log_path=str(event_path),
        patch_path=str(tmp_path / "workspace.patch"),
    )
    assert all(
        path not in request.model_dump_json()
        for path in fixture.secret_files.values()
    )
    assert str(fixture.composition_ref_path) not in request.model_dump_json()

    with pytest.raises(HeadlessRunFailed) as rejected:
        await run_headless_request(
            request,
            composition_ref_path=str(fixture.composition_ref_path),
            secret_files={
                handle: str(tmp_path / "unavailable-secrets" / handle)
                for handle in fixture.secret_files
            },
            provider_credentials={
                request.provider.credential_handle: str(
                    tmp_path / "unavailable-provider-credential"
                )
            },
            provider_routes={
                request.provider.credential_handle: HeadlessProviderRouteAuthority(
                    model=request.provider.model,
                    authority_model_id=request.provider.authority_model_id,
                    base_url="http://127.0.0.1:45219/v1",
                    policy_observation_digest=task_image_digest,
                )
            },
            repository_base_commits={
                task_image_digest: request.workspace.base_commit
            },
        )

    assert rejected.value.result["terminal"]["status"] == "failed"
    assert rejected.value.result["terminal"]["failure"] == {
        "code": "ValueError",
        "category": "ValueError",
    }
    assert rejected.value.result["patch"] == {
        "requested": True,
        "destination": str(tmp_path / "workspace.patch"),
        "digest": None,
        "size_bytes": None,
        "available": False,
    }
    assert json.loads(result_path.read_bytes()) == rejected.value.result
    assert not event_path.exists()


