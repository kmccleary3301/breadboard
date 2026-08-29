from __future__ import annotations

import json
import hashlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import breadboard.rl.harness.headless as headless_module
from breadboard.rl.harness import contracts as c
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
from breadboard.rl.harness.policy_provider import E4TargetPolicyProjection
from breadboard.rl.harness.runners.base import freeze_json_object, thaw_json
from breadboard_engine.provider.contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
)
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime

from breadboard.rl.harness.qualification import (
    materialize_production_composition_fixture,
)


class _Transport:
    def __init__(self) -> None:
        self.closed = False
        self.closed_event = threading.Event()

    def close(self) -> None:
        self.closed = True
        self.closed_event.set()


def _target_projection(plan: c.EffectiveExecutionPlan) -> E4TargetPolicyProjection:
    semantics = plan.effective_semantics
    prompts = semantics["prompts"]
    providers = semantics["providers"]
    mode = semantics["modes"][0]
    variant = next(
        item
        for item in prompts["variants"]
        if item["config_node_id"] == semantics["root_config_node_id"]
        and item["mode_id"] == mode["mode_id"]
        and item["model_id"] == providers["default_model_id"]
    )
    system = variant["system"]["text"]
    catalog = variant["tool_catalog"]["text"]
    if prompts["tool_prompt_mode"] == "system_once":
        system = "\n\n".join(value for value in (system, catalog) if value)
    definition = semantics["tools"]["definitions"][0]
    tool_name = definition["model_name"]
    return E4TargetPolicyProjection(
        target_id="fixture@1.0.0",
        overlay_id="fixture-headless.v1",
        descriptor_digest="sha256:" + "1" * 64,
        execution_config_digest="sha256:" + "3" * 64,
        overlay_digest="sha256:" + "4" * 64,
        rendered_prompt_digest="sha256:" + "2" * 64,
        system_prompt=system,
        ordered_tool_names=(tool_name,),
        chat_tools=(
            freeze_json_object(
                headless_module._project_effective_chat_tool(definition),
                field_name="fixture target tool",
            ),
        ),
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


def test_headless_projection_exports_the_exact_workspace_patch() -> None:
    patch = b"diff --git a/a.py b/a.py\n"
    events = b'{"event":"done"}\n'

    class CAS:
        def __init__(self) -> None:
            artifact_manifest = json.dumps(
                {"objects": [{"role": "patch", "payload": "runner-result-json"}]},
                sort_keys=True,
            ).encode()
            self.payloads = {
                "manifest": json.dumps(
                    {
                        "runner_ledger_ref": self.ref("events", events),
                        "artifact_manifest_ref": self.ref(
                            "artifacts",
                            artifact_manifest,
                        ),
                    },
                    sort_keys=True,
                ).encode(),
                "events": events,
                "artifacts": artifact_manifest,
            }

        @staticmethod
        def ref(artifact_id: str, payload: bytes) -> dict[str, str]:
            return {
                "artifact_id": artifact_id,
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }

        def get_ref(self, artifact_id: str) -> SimpleNamespace:
            payload = self.payloads[artifact_id]
            projection = self.ref(artifact_id, payload)
            return SimpleNamespace(**projection, to_dict=lambda: projection)

        def get_bytes(self, ref: SimpleNamespace, *, max_bytes: int) -> bytes:
            payload = self.payloads[ref.artifact_id]
            assert len(payload) <= max_bytes
            return payload

    cas = CAS()
    composition = SimpleNamespace(
        authority_graph=SimpleNamespace(cas=cas),
    )
    run = SimpleNamespace(
        primary_disposition=SimpleNamespace(value="succeeded"),
        termination="completed",
        turn_count=1,
        response={"answer": "done"},
        completed_envelope_ref=None,
        closed_envelope_ref=None,
        result_ref=None,
        evidence_manifest_ref=cas.get_ref("manifest"),
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

    with pytest.raises(ValueError, match="base commit mismatch"):
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
async def test_target_semantics_reject_changed_tool_parameter_schema(
    tmp_path: Path,
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    composition = load_production_composition(
        str(fixture.composition_ref_path),
        fixture.secret_files,
    )
    try:
        resolution = c.ResolveEpisodeRequest.model_validate(
            fixture.create_body["resolution"]
        )
        plan = composition.authority_graph.config_runtime.resolve_episode(
            resolution
        ).effective_plan
        target = _target_projection(plan)
        headless_module._validate_target_semantics(
            target,
            plan.effective_semantics,
        )
        semantics = thaw_json(plan.effective_semantics)
        parameter_schema = semantics["tools"]["definitions"][0]["parameters"][0][
            "schema"
        ]
        parameter_schema["minLength"] = parameter_schema["minLength"] + 1
        changed_semantics = freeze_json_object(
            semantics,
            field_name="changed target semantics",
        )

        with pytest.raises(
            ValueError,
            match="do not match the selected E4 target",
        ):
            headless_module._validate_target_semantics(
                target,
                changed_semantics,
            )
    finally:
        await composition.close()


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="development trusted-process sandbox requires Linux",
)
@pytest.mark.asyncio
async def test_headless_runner_rejects_development_trusted_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_fixture = materialize_production_composition_fixture(
        tmp_path / "probe",
        policy_provider_id="openai",
        policy_model_id="qwen3.5-35b-a3b",
        policy_context_window=131_072,
        policy_max_output_tokens=32_000,
    )
    probe_resolution = c.ResolveEpisodeRequest.model_validate(
        probe_fixture.create_body["resolution"]
    )
    probe = load_production_composition(
        str(probe_fixture.composition_ref_path),
        probe_fixture.secret_files,
    )
    try:
        resolved = probe.authority_graph.config_runtime.resolve_episode(
            probe_resolution
        )
        plan = resolved.effective_plan
        target = _target_projection(plan)
    finally:
        await probe.close()
    fixture = materialize_production_composition_fixture(
        tmp_path / "fixture",
        policy_provider_id="openai",
        policy_model_id="qwen3.5-35b-a3b",
        policy_context_window=131_072,
        policy_max_output_tokens=32_000,
    )
    resolution = c.ResolveEpisodeRequest.model_validate(
        fixture.create_body["resolution"]
    )

    credential_handle = str(fixture.policy_observation["credential_handle_id"])
    route = HeadlessProviderRouteAuthority(
        model="Qwen/Qwen3.5-35B-A3B",
        authority_model_id="qwen3.5-35b-a3b",
        base_url="http://127.0.0.1:8000/v1",
        caller_headers={"X-Episode-ID": resolution.episode_id},
        policy_observation_digest=c.PolicyCapabilityObservation.model_validate(
            fixture.policy_observation
        ).canonical_digest(),
    )
    monkeypatch.setattr(
        E4TargetPolicyProjection,
        "load",
        classmethod(lambda _cls, _target_id, _fields: target),
    )
    transport = _Transport()
    provider_calls: list[dict[str, Any]] = []
    scripted_results = (
        ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ProviderToolCall(
                            id="write-task-output",
                            name="shell",
                            arguments={
                                "command": (
                                    'printf \'{"answer":"breadboard-production-fixture"}\' '
                                    "> task-output.json"
                                )
                            },
                        )
                    ],
                )
            ],
            raw_response={},
        ),
        ProviderResult(
            messages=[ProviderMessage(role="assistant", content="done")],
            raw_response={},
        ),
    )
    provider_results = list(scripted_results)
    monkeypatch.setattr(
        OpenAIChatRuntime,
        "create_client_from_profile",
        lambda _self, _profile, **_kwargs: transport,
    )

    def invoke(_self: Any, **kwargs: Any) -> ProviderResult:
        provider_calls.append(kwargs)
        return provider_results.pop(0)

    monkeypatch.setattr(OpenAIChatRuntime, "invoke", invoke)
    credential = tmp_path / "provider-credential"
    credential.write_text("headless-secret\n", encoding="utf-8")
    credential.chmod(0o600)
    result_path = tmp_path / "result.json"
    event_path = tmp_path / "events.json"
    request = HeadlessRunRequest(
        target_id=target.target_id,
        target_overlay_id=target.overlay_id,
        target_dynamic_fields={"fixture": "value"},
        resolve_request=resolution,
        prompt="Repair the task and verify the result.",
        tool_allowlist=target.ordered_tool_names,
        context={"campaign": "e4"},
        workspace=HeadlessWorkspaceInput(
            repository_snapshot_digest=plan.task.repository_snapshot_digest,
            base_commit=(
                None if plan.task.repository_snapshot_digest is None else "0" * 40
            ),
            task_image_digest=plan.sandbox.image_digest,
        ),
        expected_resources=plan.effective_capabilities.resources,
        expected_limits=plan.effective_capabilities.limits,
        expected_sandbox=plan.sandbox,
        provider=HeadlessProviderInput(
            model="Qwen/Qwen3.5-35B-A3B",
            authority_model_id="qwen3.5-35b-a3b",
            credential_handle=credential_handle,
            context_window=131_072,
            max_output_tokens=32_000,
            timeout_seconds=30,
            capabilities={
                "supports_tools": True,
                "supports_thinking_control": True,
            },
            compatibility={
                "sdk_max_retries": 0,
                "transport_max_retries": 0,
                "provider_fallback": False,
            },
        ),
        result_path=str(result_path),
        event_log_path=str(event_path),
        patch_path=str(tmp_path / "workspace.patch"),
    )
    assert str(credential) not in request.model_dump_json()
    assert all(
        path not in request.model_dump_json() for path in fixture.secret_files.values()
    )
    assert str(fixture.composition_ref_path) not in request.model_dump_json()
    with pytest.raises(ValueError):
        HeadlessProviderInput.model_validate(
            {
                **request.provider.model_dump(),
                "base_url": "http://127.0.0.1:8001/v1",
            }
        )
    alternate_route = route.model_copy(
        update={"caller_headers": {"X-Episode-ID": "different-episode"}}
    )
    assert (
        alternate_route.identity_dict()["caller_header_names_sha256"]
        == route.identity_dict()["caller_header_names_sha256"]
    )
    assert (
        alternate_route.identity_dict()["caller_headers_sha256"]
        != route.identity_dict()["caller_headers_sha256"]
    )

    bound_digest = "sha256:" + "1" * 64
    bound_commit = "2" * 40
    bound_request = request.model_copy(
        update={
            "workspace": HeadlessWorkspaceInput(
                repository_snapshot_digest=bound_digest,
                base_commit=bound_commit,
                task_image_digest=plan.sandbox.image_digest,
            )
        }
    )
    with pytest.raises(ValueError, match="not bound"):
        _validate_repository_base_commit_binding(bound_request, {})
    _validate_repository_base_commit_binding(
        bound_request,
        {bound_digest: bound_commit},
    )

    with pytest.raises(HeadlessRunFailed) as rejected:
        await run_headless_request(
            request,
            composition_ref_path=str(fixture.composition_ref_path),
            secret_files=fixture.secret_files,
            provider_credentials={credential_handle: str(credential)},
            provider_routes={credential_handle: route},
            repository_base_commits={},
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
    assert provider_calls == []
