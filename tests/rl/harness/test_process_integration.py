from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

ray = pytest.importorskip(
    "ray", reason="Ray is required for the real local-process harness smoke"
)

from breadboard.rl.harness import (  # noqa: E402
    AtomicEpisodeRunRequest,
    BreadBoardEpisodeService,
    HarnessProfileRegistry,
    HarnessTask,
    PolicyRoute,
)
from breadboard.rl.harness.sandbox import SandboxLease, SandboxLeaseManager  # noqa: E402
from breadboard.rl.state.cas import InMemoryCAS  # noqa: E402


IMAGE = "sha256:" + "c" * 64


class RecordingLeaseManager(SandboxLeaseManager):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__(workspace_root)
        self.leases: list[SandboxLease] = []

    async def open(
        self,
        *,
        episode_id: str,
        driver: str,
        image_digest: str,
        network: str,
        copy_from: Path | None = None,
    ) -> SandboxLease:
        lease = await super().open(
            episode_id=episode_id,
            driver=driver,
            image_digest=image_digest,
            network=network,
            copy_from=copy_from,
        )
        self.leases.append(lease)
        return lease


class WorkspacePolicy:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses = [
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "write",
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "result.txt", "content": "written\n"}
                        ),
                    }
                ]
            },
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "shell",
                        "name": "shell",
                        "arguments": json.dumps(
                            {
                                "command": "printf 'shell\\n' >> result.txt && cat result.txt"
                            }
                        ),
                    }
                ]
            },
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "submit",
                        "name": "submit",
                        "arguments": json.dumps({"result": "workspace ready"}),
                    }
                ],
                "token_ids": [41, 42],
                "token_logprobs": [-0.25, -0.5],
            },
        ]

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        self.requests.append(copy.deepcopy(dict(request_payload)))
        return self.responses.pop(0)


def _observation(request: dict[str, Any], call_id: str) -> dict[str, Any]:
    item = next(
        value
        for value in request["input"]
        if value.get("type") == "function_call_output"
        and value.get("call_id") == call_id
    )
    return json.loads(item["output"])


@pytest.fixture
def shutdown_local_ray() -> None:
    was_initialized = ray.is_initialized()
    yield
    if not was_initialized and ray.is_initialized():
        ray.shutdown()


async def test_atomic_episode_executes_real_process_tools_and_verifier_then_closes_all_resources(
    tmp_path: Path,
    shutdown_local_ray: None,
) -> None:
    workspace_root = tmp_path / "workspaces"
    lease_manager = RecordingLeaseManager(workspace_root)
    artifact_store = InMemoryCAS()
    policy = WorkspacePolicy()
    profiles = HarnessProfileRegistry.from_mapping(
        {
            "terminal": {
                "sandbox_driver": "process",
                "trusted_process": True,
                "network": "host",
                "max_turns": 3,
                "action_timeout_seconds": 10,
                "verifier_timeout_seconds": 10,
                "max_observation_chars": 20_000,
                "max_artifact_bytes": 20_000,
                "default_image_digest": IMAGE,
                "allowed_image_digests": [IMAGE],
                "default_verifier_ref": "workspace-check",
                "verifier_commands": {
                    "workspace-check": (
                        "grep -qx 'written' result.txt && grep -qx 'shell' result.txt "
                        "&& printf 'verifier-ran' > verifier-only.txt"
                    )
                },
                "artifact_paths": ["result.txt"],
            }
        }
    )
    service = BreadBoardEpisodeService(
        profiles=profiles,
        lease_manager=lease_manager,
        artifact_store=artifact_store,
        policy_factory=lambda base_url: policy,
    )
    request = AtomicEpisodeRunRequest(
        episode_id="real-process-episode",
        profile="terminal",
        task=HarnessTask(
            task_id="real-process-task",
            sandbox_image_digest=IMAGE,
            verifier_ref="workspace-check",
        ),
        responses_create_params={"input": "create the requested workspace file"},
        policy=PolicyRoute(base_url="http://policy.test"),
    )

    result = await service.run_atomic(request)

    assert _observation(policy.requests[1], "write")["bytes"] == len(b"written\n")
    shell_observation = _observation(policy.requests[2], "shell")
    assert shell_observation["exit"] == 0
    assert shell_observation["stdout"] == "written\nshell\n"
    assert result.termination_reason == "submitted"
    assert result.reward == 1.0
    assert result.response["token_ids"] == [41, 42]
    assert result.response["token_logprobs"] == [-0.25, -0.5]

    workspace_artifact = next(
        ref
        for ref in result.artifact_refs
        if ref["metadata"]["kind"] == "workspace_file"
    )
    assert (
        artifact_store.get_bytes(workspace_artifact["artifact_id"])
        == b"written\nshell\n"
    )

    assert len(lease_manager.leases) == 2
    primary, verifier = lease_manager.leases
    assert primary.lease_id != verifier.lease_id
    assert result.sandbox_attestation["cleanup_state"] == "closed"
    assert result.sandbox_attestation["network"] == "host"
    assert result.verifier_attestation is not None
    assert result.verifier_attestation["cleanup_state"] == "closed"
    assert result.verifier_attestation["network"] == "host"
    assert all(lease.closed for lease in lease_manager.leases)
    assert all(lease.actor is None for lease in lease_manager.leases)
    assert all(not lease.workspace.exists() for lease in lease_manager.leases)
    assert list(workspace_root.iterdir()) == []
