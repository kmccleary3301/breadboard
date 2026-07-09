from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from breadboard.rl.harness import (
    AtomicEpisodeRunRequest,
    BreadBoardEpisodeService,
    EpisodeCreateRequest,
    EpisodeRunRequest,
    HarnessProfileRegistry,
    HarnessTask,
    PolicyRoute,
)
from breadboard.rl.harness.service import EpisodeCancelled, EpisodeConflict
from breadboard.rl.state.cas import InMemoryCAS


IMAGE = "sha256:" + "a" * 64


def _profiles(
    *, max_turns: int = 4, artifact_paths: tuple[str, ...] = ()
) -> HarnessProfileRegistry:
    return HarnessProfileRegistry.from_mapping(
        {
            "swe": {
                "sandbox_driver": "docker",
                "max_turns": max_turns,
                "action_timeout_seconds": 9,
                "verifier_timeout_seconds": 17,
                "max_observation_chars": 20_000,
                "max_artifact_bytes": 10_000,
                "default_image_digest": IMAGE,
                "allowed_image_digests": [IMAGE],
                "default_verifier_ref": "tests",
                "verifier_commands": {"tests": "run-project-verifier"},
                "artifact_paths": list(artifact_paths),
            }
        }
    )


def _create_request(
    episode_id: str = "episode-1", *, task_id: str = "task-1"
) -> EpisodeCreateRequest:
    return EpisodeCreateRequest(
        episode_id=episode_id,
        profile="swe",
        task=HarnessTask(
            task_id=task_id,
            sandbox_image_digest=IMAGE,
            verifier_ref="tests",
        ),
    )


def _atomic_request(
    responses_create_params: dict[str, Any],
    *,
    episode_id: str = "episode-1",
) -> AtomicEpisodeRunRequest:
    return AtomicEpisodeRunRequest(
        episode_id=episode_id,
        profile="swe",
        task=HarnessTask(
            task_id="task-1",
            sandbox_image_digest=IMAGE,
            verifier_ref="tests",
        ),
        responses_create_params=responses_create_params,
        policy=PolicyRoute(base_url="http://policy.test"),
    )


class FakeLease:
    def __init__(
        self,
        *,
        lease_id: str,
        workspace: Path,
        files: dict[str, bytes] | None = None,
        verifier_result: dict[str, Any] | None = None,
    ) -> None:
        self.lease_id = lease_id
        self.workspace = workspace
        self.workspace.mkdir(parents=True)
        self.files = dict(files or {})
        self.verifier_result = dict(
            verifier_result
            or {
                "exit": 0,
                "stdout": "verified",
                "stderr": "",
                "checks": {"tests": "passed"},
            }
        )
        self.closed = False
        self.close_calls = 0
        self.cleanup_count = 0
        self.actions: list[tuple[Any, ...]] = []

    def attestation(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "workspace_id": self.workspace.name,
            "cleanup_state": "closed" if self.closed else "active",
        }

    async def run_shell(self, command: str, *, timeout: int) -> dict[str, Any]:
        self.actions.append(("shell", command, timeout))
        if self.lease_id.endswith("-verifier"):
            return copy.deepcopy(self.verifier_result)
        return {"exit": 0, "stdout": f"ran:{command}", "stderr": ""}

    async def read_text(
        self, path: str, *, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        self.actions.append(("read_file", path, offset, limit))
        content = self.files.get(path, b"").decode("utf-8")
        selected = (
            content[offset:] if limit is None else content[offset : offset + limit]
        )
        return {
            "path": path,
            "content": selected,
            "truncated": len(selected) < len(content[offset:]),
        }

    async def write_text(self, path: str, content: str) -> dict[str, Any]:
        self.actions.append(("write_file", path, content))
        payload = content.encode("utf-8")
        self.files[path] = payload
        return {"ok": True, "path": path, "bytes": len(payload)}

    async def list_files(self, path: str, *, depth: int) -> dict[str, Any]:
        self.actions.append(("list_files", path, depth))
        return {"path": path, "entries": sorted(self.files)}

    async def stat(self, path: str) -> dict[str, Any]:
        payload = self.files.get(path)
        if payload is None:
            return {"path": path, "exists": False}
        return {"path": path, "exists": True, "type": "file", "size": len(payload)}

    async def get_bytes(self, path: str) -> bytes:
        return self.files[path]

    async def close(self) -> dict[str, Any]:
        self.close_calls += 1
        if not self.closed:
            self.closed = True
            self.cleanup_count += 1
            self.workspace.rmdir()
        return self.attestation()


class FakeLeaseManager:
    def __init__(
        self, workspace_root: Path, *, verifier_result: dict[str, Any] | None = None
    ) -> None:
        self.workspace_root = workspace_root
        self.verifier_result = verifier_result
        self.leases: list[FakeLease] = []
        self.open_calls: list[dict[str, Any]] = []

    async def open(
        self,
        *,
        episode_id: str,
        driver: str,
        image_digest: str,
        network: str,
        copy_from: Path | None = None,
    ) -> FakeLease:
        copied_files: dict[str, bytes] = {}
        if copy_from is not None:
            source = next(
                lease for lease in self.leases if lease.workspace == copy_from
            )
            copied_files = dict(source.files)
        lease = FakeLease(
            lease_id=f"{episode_id}-verifier" if copy_from is not None else episode_id,
            workspace=self.workspace_root / f"workspace-{len(self.leases)}",
            files=copied_files,
            verifier_result=self.verifier_result,
        )
        self.leases.append(lease)
        self.open_calls.append(
            {
                "episode_id": episode_id,
                "driver": driver,
                "image_digest": image_digest,
                "network": network,
                "copy_from": copy_from,
            }
        )
        return lease


class ScriptedPolicy:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = copy.deepcopy(responses)
        self.requests: list[dict[str, Any]] = []

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        self.requests.append(copy.deepcopy(dict(request_payload)))
        if not self.responses:
            raise AssertionError("policy received more turns than scripted")
        return self.responses.pop(0)


class BlockingPolicy:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        self.entered.set()
        await self.release.wait()
        return {
            "output": [{"type": "message", "role": "assistant", "content": "too late"}]
        }


def _function_call(call_id: str, name: str, arguments: object) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _observations(output: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        item["call_id"]: json.loads(item["output"])
        for item in output
        if item.get("type") == "function_call_output"
    }


async def test_episode_loop_replaces_wrapper_tools_returns_observations_carriers_reward_and_artifacts(
    tmp_path: Path,
) -> None:
    lease_manager = FakeLeaseManager(tmp_path)
    artifact_store = InMemoryCAS()
    policy = ScriptedPolicy(
        [
            {
                "id": "response-turn-1",
                "output": [
                    _function_call("shell-1", "shell", json.dumps({"command": "pwd"}))
                ],
            },
            {
                "id": "response-final",
                "output": [
                    _function_call(
                        "write-1",
                        "write_file",
                        json.dumps(
                            {"path": "report.json", "content": '{"status":"ok"}'}
                        ),
                    ),
                    _function_call(
                        "submit-1", "submit", json.dumps({"result": "done"})
                    ),
                ],
                "usage": {"input_tokens": 31, "output_tokens": 7},
                "token_ids": [101, 202, 303],
                "token_logprobs": [-0.1, -0.2, -0.3],
                "model_version": "policy-build-42",
                "routing": {"provider": "local", "replica": "r2"},
                "vendor_extension": {"arbitrary": [1, {"nested": True}]},
            },
        ]
    )
    service = BreadBoardEpisodeService(
        profiles=_profiles(artifact_paths=("report.json",)),
        lease_manager=lease_manager,
        artifact_store=artifact_store,
        policy_factory=lambda base_url: policy,
    )
    params = {
        "model": "wrapper-model",
        "input": [{"role": "user", "content": "repair the project"}],
        "tools": [{"type": "function", "name": "wrapper_owned_tool"}],
        "temperature": 0.2,
    }
    original_params = copy.deepcopy(params)

    result = await service.run_atomic(_atomic_request(params))

    assert params == original_params
    assert result.responses_create_params == original_params
    profile_tool_names = {tool["name"] for tool in policy.requests[0]["tools"]}
    assert profile_tool_names == {
        "shell",
        "read_file",
        "write_file",
        "list_files",
        "submit",
    }
    assert "wrapper_owned_tool" not in profile_tool_names
    assert policy.requests[0]["parallel_tool_calls"] is False

    second_turn_observations = _observations(policy.requests[1]["input"])
    assert second_turn_observations == {
        "shell-1": {"exit": 0, "stderr": "", "stdout": "ran:pwd"}
    }
    assert result.termination_reason == "submitted"
    assert result.turns == 2
    assert result.reward == 1.0
    assert result.reward_components == {"verifier": 1.0}
    assert result.response["id"] == "response-final"
    assert result.response["usage"] == {"input_tokens": 31, "output_tokens": 7}
    assert result.response["token_ids"] == [101, 202, 303]
    assert result.response["token_logprobs"] == [-0.1, -0.2, -0.3]
    assert result.response["model_version"] == "policy-build-42"
    assert result.response["routing"] == {"provider": "local", "replica": "r2"}
    assert result.response["vendor_extension"] == {"arbitrary": [1, {"nested": True}]}

    assert len(lease_manager.leases) == 2
    primary, verifier = lease_manager.leases
    assert lease_manager.open_calls[1]["copy_from"] == primary.workspace
    assert verifier.actions == [("shell", "run-project-verifier", 17)]
    assert result.verifier_attestation == verifier.attestation()
    assert verifier.cleanup_count == 1
    assert primary.cleanup_count == 1
    assert result.sandbox_attestation["cleanup_state"] == "closed"

    artifact_by_kind = {ref["metadata"]["kind"]: ref for ref in result.artifact_refs}
    assert set(artifact_by_kind) == {"breadboard_harness_trajectory", "workspace_file"}
    assert artifact_by_kind["breadboard_harness_trajectory"]["retrieval_path"] == (
        "/v1/artifacts/episode-1%3Atrajectory"
    )
    assert artifact_by_kind["workspace_file"]["retrieval_path"] == (
        "/v1/artifacts/episode-1%3Aworkspace%3A0"
    )
    assert (
        artifact_store.get_bytes(artifact_by_kind["workspace_file"]["artifact_id"])
        == b'{"status":"ok"}'
    )
    transcript = json.loads(
        artifact_store.get_bytes(
            artifact_by_kind["breadboard_harness_trajectory"]["artifact_id"]
        )
    )
    assert transcript["termination_reason"] == "submitted"
    assert transcript["trajectory"][0]["observations"][0] == {
        "type": "function_call_output",
        "call_id": "shell-1",
        "output": '{"exit":0,"stderr":"","stdout":"ran:pwd"}',
    }
    assert transcript["verifier"]["result"]["checks"] == {"tests": "passed"}

    first_close = await service.close_episode("episode-1")
    second_close = await service.close_episode("episode-1")
    assert first_close.state == second_close.state == "closed"
    assert primary.cleanup_count == 1


async def test_malformed_and_unsupported_tool_calls_are_policy_observations_not_episode_failures(
    tmp_path: Path,
) -> None:
    policy = ScriptedPolicy(
        [
            {
                "output": [
                    _function_call("bad-json", "shell", {"command": "pwd"}),
                    _function_call("unknown", "delete_everything", "{}"),
                ]
            },
            {
                "output": [
                    {"type": "message", "role": "assistant", "content": "recovered"}
                ]
            },
        ]
    )
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=FakeLeaseManager(tmp_path),
        artifact_store=InMemoryCAS(),
        policy_factory=lambda base_url: policy,
    )

    result = await service.run_atomic(
        _atomic_request({"input": "recover from tool errors"})
    )

    observed = _observations(result.response["output"])
    assert observed["bad-json"] == {
        "error": "ValueError: arguments must be a JSON string"
    }
    assert observed["unknown"] == {
        "error": "ValueError: tool 'delete_everything' is not admitted by profile 'swe'"
    }
    assert _observations(policy.requests[1]["input"]) == observed
    assert result.termination_reason == "assistant_complete"
    assert result.reward == 1.0


async def test_max_turns_stops_repeated_tool_use_at_the_profile_boundary(
    tmp_path: Path,
) -> None:
    policy = ScriptedPolicy(
        [
            {
                "output": [
                    _function_call("call-1", "shell", json.dumps({"command": "first"}))
                ]
            },
            {
                "output": [
                    _function_call("call-2", "shell", json.dumps({"command": "second"}))
                ]
            },
        ]
    )
    service = BreadBoardEpisodeService(
        profiles=_profiles(max_turns=2),
        lease_manager=FakeLeaseManager(tmp_path),
        artifact_store=InMemoryCAS(),
        policy_factory=lambda base_url: policy,
    )

    result = await service.run_atomic(_atomic_request({"input": "keep working"}))

    assert result.termination_reason == "max_turns"
    assert result.turns == 2
    assert len(policy.requests) == 2
    assert set(_observations(result.response["output"])) == {"call-1", "call-2"}


@pytest.mark.parametrize(
    "policy_input",
    [
        (
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,AA==",
                        }
                    ],
                }
            ],
            "responses-input-image",
        ),
        (
            {"nested": {"image_url": {"url": "https://images.invalid/example.png"}}},
            "nested-image-url",
        ),
        ([{"type": "computer_screenshot", "data": "opaque"}], "screenshot-output-type"),
        (
            {"content": {"screenshot_url": "https://images.invalid/screen.png"}},
            "screenshot-url-key",
        ),
        (
            [
                {
                    "type": "input_file",
                    "filename": "screen.png",
                    "file_data": "data:image/png;base64,AA==",
                }
            ],
            "input-file-image-data-url",
        ),
        ([{"type": "input_audio", "audio": "base64-audio"}], "nontext-audio-input"),
        (
            [{"type": "custom_multimodal_blob", "payload": "opaque"}],
            "unknown-multimodal-input",
        ),
    ],
    ids=lambda case: case[1],
)
async def test_policy_visible_images_are_rejected_before_atomic_workspace_allocation(
    tmp_path: Path,
    policy_input: tuple[object, str],
) -> None:
    rejected_input, _ = policy_input
    lease_manager = FakeLeaseManager(tmp_path)
    policy = ScriptedPolicy([])
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=lease_manager,
        artifact_store=InMemoryCAS(),
        policy_factory=lambda base_url: policy,
    )

    with pytest.raises(ValueError, match="policy-visible image input is unsupported"):
        await service.run_atomic(_atomic_request({"input": rejected_input}))

    assert policy.requests == []
    assert lease_manager.leases == []


async def test_text_only_reasoning_input_is_admitted_to_policy(tmp_path: Path) -> None:
    lease_manager = FakeLeaseManager(tmp_path)
    policy = ScriptedPolicy(
        [{"output": [{"type": "message", "role": "assistant", "content": "accepted"}]}]
    )
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=lease_manager,
        artifact_store=InMemoryCAS(),
        policy_factory=lambda base_url: policy,
    )
    reasoning_input = [{"type": "reasoning", "text": "Use the textual repair plan."}]

    result = await service.run_atomic(_atomic_request({"input": reasoning_input}))

    assert policy.requests[0]["input"] == reasoning_input
    assert result.termination_reason == "assistant_complete"
    assert result.reward == 1.0


async def test_duplicate_episode_id_with_conflicting_task_is_rejected_without_a_second_workspace(
    tmp_path: Path,
) -> None:
    lease_manager = FakeLeaseManager(tmp_path)
    service = BreadBoardEpisodeService(
        profiles=_profiles(), lease_manager=lease_manager, artifact_store=InMemoryCAS()
    )
    await service.create_episode(_create_request(task_id="task-original"))

    with pytest.raises(EpisodeConflict, match="already has a different specification"):
        await service.create_episode(_create_request(task_id="task-conflict"))

    assert len(lease_manager.leases) == 1
    await service.close_episode("episode-1")
    assert lease_manager.leases[0].cleanup_count == 1


async def test_cancellation_closes_the_workspace_prevents_verification_and_is_idempotent(
    tmp_path: Path,
) -> None:
    lease_manager = FakeLeaseManager(tmp_path)
    policy = BlockingPolicy()
    service = BreadBoardEpisodeService(
        profiles=_profiles(),
        lease_manager=lease_manager,
        artifact_store=InMemoryCAS(),
        policy_factory=lambda base_url: policy,
    )
    await service.create_episode(_create_request())
    run_task = asyncio.create_task(
        service.run_episode(
            "episode-1",
            EpisodeRunRequest(
                responses_create_params={"input": "wait for cancellation"},
                policy=PolicyRoute(base_url="http://policy.test"),
            ),
        )
    )
    await asyncio.wait_for(policy.entered.wait(), timeout=1)

    cancelled = await service.cancel_episode("episode-1", "operator stop")
    policy.release.set()

    with pytest.raises(EpisodeCancelled, match="operator stop"):
        await run_task
    assert cancelled.state == "cancelled"
    assert cancelled.reason == "operator stop"
    assert len(lease_manager.leases) == 1
    assert lease_manager.leases[0].closed is True
    assert lease_manager.leases[0].cleanup_count == 1

    closed_again = await service.close_episode("episode-1")
    assert closed_again.state == "closed"
    assert closed_again.reason == "operator stop"
    assert lease_manager.leases[0].cleanup_count == 1
