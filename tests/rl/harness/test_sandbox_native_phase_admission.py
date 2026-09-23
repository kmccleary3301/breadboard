from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.runners.base import RunnerToolBinding
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
            package_subpath="node_modules/@mariozechner/pi-coding-agent",
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
        package_subpath="node_modules/@mariozechner/pi-coding-agent",
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


@pytest.mark.asyncio
async def test_lease_rejects_authority_before_repository_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = RunnerToolBinding("read", "sha256:" + ("1" * 64), ())
    adapter = SimpleNamespace(
        adapter_id=PI_CODING_AGENT_LOCAL_ADAPTER_ID,
        tool_ids=("bash", "edit", "read", "write"),
        runtime_root_path="/sealed/pi",
    )
    plan = SimpleNamespace(
        effective_plan_digest="plan",
        tool_bindings=(binding,),
        installed_tool_adapters=(adapter,),
        limits=SimpleNamespace(observation_bytes=4096),
        materialization_plan=SimpleNamespace(entries=()),
    )

    async def begin() -> None:
        return None

    async def end() -> None:
        return None

    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _begin_operation=begin,
        _end_operation=end,
    )
    monkeypatch.setattr(
        sandbox_module.TrustedProcessHandle,
        "_validate_native_binding",
        staticmethod(lambda _plan, _adapter: None),
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))

    with pytest.raises(WorkspaceStateError) as captured:
        await workspace.invoke_native_phase(
            "initialize",
            {"workspace": "caller-owned"},
            timeout_ms=1_000,
        )

    assert captured.value.code == "workspace_authority_mismatch"
    assert "cannot supply workspace authority" in str(captured.value)


@pytest.mark.asyncio
async def test_initialize_accepts_non_repository_writable_policy_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = RunnerToolBinding("read", "sha256:" + ("1" * 64), ())
    adapter = SimpleNamespace(
        adapter_id=PI_CODING_AGENT_LOCAL_ADAPTER_ID,
        tool_ids=("bash", "edit", "read", "write"),
        runtime_root_path="/sealed/pi",
    )
    workspace_root = tmp_path / "seed"
    workspace_root.mkdir()
    entry = SimpleNamespace(
        role="workspace_seed",
        target_logical_path="seed",
        access=SimpleNamespace(value="rw"),
    )
    plan = SimpleNamespace(
        effective_plan_digest="plan",
        tool_bindings=(binding,),
        installed_tool_adapters=(adapter,),
        limits=SimpleNamespace(observation_bytes=4096),
        materialization_plan=SimpleNamespace(entries=(entry,)),
    )
    captured: list[Mapping[str, object]] = []

    async def begin() -> None:
        return None

    async def end() -> None:
        return None

    async def invoke(
        _adapter: object,
        _operation: str,
        payload: Mapping[str, object],
        *,
        timeout_ms: int,
    ) -> Mapping[str, object]:
        del timeout_ms
        captured.append(payload)
        return {"schema_version": "bb.pi-native.test.v1", "kind": "initialized"}

    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _materialized=SimpleNamespace(workspace_path=tmp_path),
        _runtime=SimpleNamespace(invoke_native_phase=invoke),
        _begin_operation=begin,
        _end_operation=end,
        _resolve=lambda logical_path, writable=False: tmp_path / logical_path,
    )
    monkeypatch.setattr(
        sandbox_module.TrustedProcessHandle,
        "_validate_native_binding",
        staticmethod(lambda _plan, _adapter: None),
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))

    result = await workspace.invoke_native_phase(
        "initialize",
        {"task": "owned"},
        timeout_ms=1_000,
        package_subpath="node_modules/@mariozechner/pi-coding-agent",
    )

    assert result["kind"] == "initialized"
    assert captured[0]["workspace"] == str(workspace_root)
    assert captured[0]["package_dir"] == "/sealed/pi/node_modules/@mariozechner/pi-coding-agent"


@pytest.mark.asyncio
async def test_workspace_effects_measure_content_diff_and_binary_without_text(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "seed"
    workspace_root.mkdir()
    (workspace_root / "keep.txt").write_text("before", encoding="utf-8")
    (workspace_root / "deleted.txt").write_text("gone", encoding="utf-8")
    binding = RunnerToolBinding("read", "sha256:" + ("1" * 64), ())
    entry = SimpleNamespace(
        role="workspace_seed",
        target_logical_path="seed",
        access=SimpleNamespace(value="rw"),
    )
    plan = SimpleNamespace(
        effective_plan_digest="plan",
        tool_bindings=(binding,),
        materialization_plan=SimpleNamespace(entries=(entry,)),
    )
    async def begin() -> None:
        return None
    async def end() -> None:
        return None
    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _materialized=SimpleNamespace(workspace_path=tmp_path),
        _begin_operation=begin,
        _end_operation=end,
        _assert_active=lambda: None,
        _resolve=lambda logical_path, writable=False: workspace_root,
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))
    (workspace_root / "keep.txt").write_text("after", encoding="utf-8")
    (workspace_root / "new.txt").write_text("new", encoding="utf-8")
    (workspace_root / "binary.bin").write_bytes(b"\xff\x00")
    (workspace_root / "deleted.txt").unlink()

    effects = await workspace.measure_workspace_effects()

    assert effects["keep.txt"] == {
        "exists": True,
        "bytes": 5,
        "sha256": "sha256:" + hashlib.sha256(b"after").hexdigest(),
        "content_utf8": "after",
    }
    assert effects["new.txt"]["content_utf8"] == "new"
    assert effects["deleted.txt"] == {"exists": False}
    assert effects["binary.bin"] == {
        "exists": True,
        "bytes": 2,
        "sha256": "sha256:" + hashlib.sha256(b"\xff\x00").hexdigest(),
    }
