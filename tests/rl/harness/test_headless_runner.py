from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness.headless import (
    HeadlessProviderInput,
    HeadlessRunRequest,
    HeadlessWorkspaceInput,
    _atomic_write,
    run_headless_request,
)
from breadboard.rl.harness.policy_provider import E4TargetPolicyProjection
from breadboard.rl.harness.runners.base import freeze_json_object
from breadboard_engine.provider.contracts import (
    ProviderMessage,
    ProviderResult,
    ProviderToolCall,
)
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime

from tests.rl.harness.production_composition_fixture import (
    materialize_production_composition_fixture,
)


class _Transport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": "Run a shell command in the workspace.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {"type": "string", "minLength": 1}
                            },
                            "required": ["command"],
                        },
                    },
                },
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


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="production trusted-process sandbox requires Linux",
)
@pytest.mark.asyncio
async def test_headless_runner_uses_production_lifecycle_and_writes_replay_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_fixture = materialize_production_composition_fixture(tmp_path / "probe")
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
    fixture = materialize_production_composition_fixture(tmp_path / "fixture")
    resolution = c.ResolveEpisodeRequest.model_validate(
        fixture.create_body["resolution"]
    )

    monkeypatch.setattr(
        E4TargetPolicyProjection,
        "load",
        classmethod(lambda _cls, _target_id, _fields: target),
    )
    transport = _Transport()
    provider_calls: list[dict[str, Any]] = []
    provider_results = [
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
    ]
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
        composition_ref_path=str(fixture.composition_ref_path),
        target_id=target.target_id,
        target_overlay_id=target.overlay_id,
        target_dynamic_fields={"fixture": "value"},
        resolve_request=resolution,
        prompt="Repair the task and verify the result.",
        tool_allowlist=target.ordered_tool_names,
        context={"campaign": "e4"},
        workspace=HeadlessWorkspaceInput(
            repository_snapshot_digest=plan.task.repository_snapshot_digest,
            base_commit="0" * 40,
            task_image_digest=plan.sandbox.image_digest,
        ),
        expected_resources=plan.effective_capabilities.resources,
        expected_limits=plan.effective_capabilities.limits,
        expected_sandbox=plan.sandbox,
        provider=HeadlessProviderInput(
            model="Qwen/Qwen3.5-35B-A3B",
            base_url="http://127.0.0.1:8000/v1",
            credential_handle="episode-provider",
            context_window=131_072,
            max_output_tokens=32_000,
            timeout_seconds=30,
            caller_headers={"X-Episode-ID": resolution.episode_id},
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
    )
    assert str(credential) not in request.model_dump_json()
    assert all(
        path not in request.model_dump_json() for path in fixture.secret_files.values()
    )

    result = await run_headless_request(
        request,
        secret_files=fixture.secret_files,
        provider_credentials={"episode-provider": str(credential)},
    )

    assert result["terminal"]["status"] == "succeeded"
    assert result["terminal"]["reason"] == "assistant_complete"
    assert result["terminal"]["turn_count"] == 2
    assert result["cleanup"]["disposition"] == "released"
    assert result["cleanup"]["receipt_digest"] is not None
    assert result["cleanup_inventory"]["active_lease_ids"] == []
    assert result["cleanup_inventory"]["container_ids"] == []
    assert result["workspace_evidence"]["materialization_digest"] is not None
    assert result["workspace_evidence"]["final_workspace_snapshot_digest"] is not None
    assert result["event_log"]["available"] is True
    assert result["provider_profile_identity"]["capabilities"]["supports_tools"]
    assert result["provider_profile_identity"]["compatibility"]["sdk_max_retries"] == 0
    assert len(provider_calls) == 2
    assert provider_calls[0]["messages"][:2] == [
        {"role": "system", "content": target.system_prompt},
        {"role": "user", "content": "Repair the task and verify the result."},
    ]
    assert transport.closed

    persisted_result = result_path.read_bytes()
    persisted_events = event_path.read_bytes()
    assert json.loads(persisted_result) == result
    event_ledger = json.loads(persisted_events)
    assert event_ledger["schema_version"] == "bb.rl.runner-event-ledger.v2"
    assert event_ledger["event_count"] > 0
    assert b"headless-secret" not in persisted_result
    assert b"headless-secret" not in persisted_events
