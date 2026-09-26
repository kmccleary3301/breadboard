from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard_engine.e4_targets import load_e4_target
from breadboard.rl.harness import sandbox as sandbox_module
from breadboard.rl.harness.runners.base import JsonSnapshotError, RunnerToolBinding
from breadboard.rl.harness.sandbox import (
    InstalledToolAdapter,
    OMP_NATIVE_LOCAL_ADAPTER_ID,
    OPENCLAW_LOCAL_ADAPTER_ID,
    OPENCLAW_NATIVE_TOOL_IDS,
    OPENHANDS_SDK_LOCAL_ADAPTER_ID,
    PI_CODING_AGENT_LOCAL_ADAPTER_ID,
    SandboxLaunchError,
    TrustedProcessHandle,
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
async def test_registered_openhands_initialize_admits_native_tool_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_config = json.loads(load_e4_target("openhands-sdk@1.47.0").read_asset_text("native-config.json"))
    binding = RunnerToolBinding("read", "sha256:" + ("1" * 64), ())
    adapter = SimpleNamespace(
        adapter_id=OPENHANDS_SDK_LOCAL_ADAPTER_ID,
        tool_ids=("file_editor", "finish", "task_tracker", "terminal", "think"),
        runtime_root_path="/sealed/openhands",
    )
    entry = SimpleNamespace(
        role="workspace_seed", target_logical_path=".", access=SimpleNamespace(value="rw")
    )
    plan = SimpleNamespace(
        effective_plan_digest="plan",
        tool_bindings=(binding,),
        installed_tool_adapters=(adapter,),
        limits=SimpleNamespace(observation_bytes=1 << 20),
        materialization_plan=SimpleNamespace(entries=(entry,)),
    )
    workspace_root = tmp_path / "seed"
    workspace_root.mkdir()
    lease_root = tmp_path / "leases"
    lease_root.mkdir()
    lease_root_fd = os.open(lease_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    calls: list[Mapping[str, object]] = []

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
        calls.append(payload)
        return {"kind": "initialized"}

    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _manager=SimpleNamespace(lease_root=lease_root, _lease_root_fd=lease_root_fd),
        _materialized=SimpleNamespace(workspace_path=workspace_root),
        _runtime=SimpleNamespace(invoke_native_phase=invoke),
        _begin_operation=begin,
        _end_operation=end,
        _resolve=lambda logical_path, writable=False: workspace_root / logical_path,
    )
    monkeypatch.setattr(
        sandbox_module.TrustedProcessHandle,
        "_validate_native_binding",
        staticmethod(lambda _plan, _adapter: None),
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))

    try:
        initialized = await workspace.invoke_native_phase(
            "initialize",
            {"task": "native schema admission", "native_config": native_config},
            timeout_ms=1_000,
        )
        assert initialized["kind"] == "initialized"
        assert calls[0]["native_config"]["tool_schemas"] == native_config["tool_schemas"]

        nested: object = "leaf"
        for _ in range(63):
            nested = {"next": nested}
        with pytest.raises(WorkspaceStateError) as captured:
            await workspace.invoke_native_phase(
                "initialize",
                {"nested": nested},
                timeout_ms=1_000,
            )
        assert captured.value.code == "runtime_preflight_failed"
        assert isinstance(captured.value.__cause__, JsonSnapshotError)
        assert captured.value.__cause__.code == "depth"
        assert len(calls) == 1
    finally:
        os.close(lease_root_fd)


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
    lease_root = tmp_path / "leases"
    lease_root.mkdir()
    lease_root_fd = os.open(lease_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    entry = SimpleNamespace(
        role="workspace_seed",
        target_logical_path=".",
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

    manager = SimpleNamespace(lease_root=lease_root, _lease_root_fd=lease_root_fd)
    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _manager=manager,
        _materialized=SimpleNamespace(workspace_path=workspace_root),
        _runtime=SimpleNamespace(invoke_native_phase=invoke),
        _begin_operation=begin,
        _end_operation=end,
        _resolve=lambda logical_path, writable=False: workspace_root / logical_path,
    )
    monkeypatch.setattr(
        sandbox_module.TrustedProcessHandle,
        "_validate_native_binding",
        staticmethod(lambda _plan, _adapter: None),
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))

    try:
        result = await workspace.invoke_native_phase(
            "initialize",
            {"task": "owned"},
            timeout_ms=1_000,
            package_subpath="node_modules/@mariozechner/pi-coding-agent",
        )
    finally:
        os.close(lease_root_fd)
    assert result["kind"] == "initialized"
    assert captured[0]["workspace"] == str(workspace_root)
    scratch = Path(str(captured[0]["scratch"]))
    assert scratch == lease_root / "lease.native-scratch"
    assert not scratch.is_relative_to(workspace_root)
    assert scratch.stat().st_mode & 0o777 == 0o700
    # With the policy mount at ".", nothing runner-private lands in the measured tree.
    assert list(workspace_root.iterdir()) == []
    assert captured[0]["package_dir"] == "/sealed/pi/node_modules/@mariozechner/pi-coding-agent"


@pytest.mark.asyncio
async def test_workspace_effects_measure_content_diff_and_supplier_utf8(tmp_path: Path) -> None:
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
        resources=SimpleNamespace(storage_bytes=1 << 30),
        security_policy=SimpleNamespace(snapshot_max_inodes=1 << 16, snapshot_max_depth=64),
    )
    async def begin() -> None:
        return None
    async def end() -> None:
        return None
    close_calls = 0
    async def terminate() -> tuple[object, ...]:
        nonlocal close_calls
        close_calls += 1
        return (
            sandbox_module.CleanupStepReceipt(
                "runtime", sandbox_module.CleanupState.RELEASED
            ),
        )
    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _materialized=SimpleNamespace(workspace_path=tmp_path),
        _begin_operation=begin,
        _runtime=SimpleNamespace(terminate=terminate),
        _end_operation=end,
        _assert_active=lambda: None,
        _resolve=lambda logical_path, writable=False: workspace_root,
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))
    await workspace.begin_native_workspace_effects()
    (workspace_root / "keep.txt").write_text("after", encoding="utf-8")
    (workspace_root / "new.txt").write_text("new", encoding="utf-8")
    (workspace_root / "binary.bin").write_bytes(b"\xff\x00")
    (workspace_root / "deleted.txt").unlink()
    closed = await workspace.close_native_runtime()
    assert closed["cleanup"]["all_dead"] is True
    assert close_calls == 1
    effects = await workspace.measure_workspace_effects()


    assert effects["keep.txt"] == {
        "exists": True,
        "bytes": 5,
        "sha256": "sha256:" + hashlib.sha256(b"after").hexdigest(),
        "content_utf8": "after",
    }
    assert effects["deleted.txt"] == {"exists": False}
    assert effects["binary.bin"] == {
        "exists": True,
        "bytes": 2,
        "sha256": "sha256:" + hashlib.sha256(b"\xff\x00").hexdigest(),
        "content_utf8": "\ufffd\x00",
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(os, "killpg") or not hasattr(os, "setsid"),
    reason="process-group primitive is unavailable",
)
async def test_close_native_runtime_drains_real_process_group_before_effect_scan(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "seed"
    workspace_root.mkdir()
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
        resources=SimpleNamespace(storage_bytes=1 << 30),
        security_policy=SimpleNamespace(snapshot_max_inodes=1 << 16, snapshot_max_depth=64),
    )
    async def begin() -> None:
        return None
    async def end() -> None:
        return None
    process = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 0.25; printf late > late.txt"],
        cwd=workspace_root,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    terminated = False

    async def terminate() -> tuple[object, ...]:
        nonlocal terminated
        if terminated:
            return (
                sandbox_module.CleanupStepReceipt(
                    "runtime", sandbox_module.CleanupState.ALREADY_RELEASED
                ),
            )
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.to_thread(process.wait, 1)
        except subprocess.TimeoutExpired:
            return (
                sandbox_module.CleanupStepReceipt(
                    "runtime", sandbox_module.CleanupState.FAILED, "survivor"
                ),
            )
        terminated = True
        return (
            sandbox_module.CleanupStepReceipt(
                "runtime", sandbox_module.CleanupState.RELEASED
            ),
        )

    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _materialized=SimpleNamespace(workspace_path=tmp_path),
        _begin_operation=begin,
        _runtime=SimpleNamespace(terminate=terminate),
        _end_operation=end,
        _assert_active=lambda: None,
        _resolve=lambda logical_path, writable=False: workspace_root,
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", (binding,))
    try:
        await workspace.begin_native_workspace_effects()
        await asyncio.sleep(0.03)
        closed = await workspace.close_native_runtime()
        assert closed["cleanup"]["all_dead"] is True
        closed_again = await workspace.close_native_runtime()
        assert closed_again["cleanup"]["all_dead"] is True
        assert closed_again["cleanup"]["steps"][0]["state"] == "already_released"
        effects = await workspace.measure_workspace_effects()
        final_files = sorted(
            path.relative_to(workspace_root).as_posix()
            for path in workspace_root.rglob("*")
            if path.is_file()
        )
        assert effects == {}
        assert final_files == []
        await asyncio.sleep(0.35)
        assert not (workspace_root / "late.txt").exists()
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class _LaunchCaptured(Exception):
    pass


def _openclaw_native_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[InstalledToolAdapter, SimpleNamespace, tuple[RunnerToolBinding, ...], object]:
    root = tmp_path / "openclaw"
    (root / "bin").mkdir(parents=True)
    (root / "bin/node").write_bytes(b"node")
    (root / "worker.mjs").write_bytes(b"worker")
    metadata = root.stat()
    manifest = "sha256:" + ("2" * 64)
    adapter = InstalledToolAdapter(
        adapter_id=OPENCLAW_LOCAL_ADAPTER_ID,
        tool_ids=OPENCLAW_NATIVE_TOOL_IDS,
        runtime_root_path=str(root),
        runtime_root_device=metadata.st_dev,
        runtime_root_inode=metadata.st_ino,
        runtime_root_owner_uid=metadata.st_uid,
        runtime_root_mode=f"{metadata.st_mode & 0o777:04o}",
        manifest_digest=manifest,
        executable_relative_path="bin/node",
        entrypoint_relative_path="worker.mjs",
        executable_digest="sha256:" + hashlib.sha256(b"node").hexdigest(),
        entrypoint_digest="sha256:" + hashlib.sha256(b"worker").hexdigest(),
    )
    bindings = tuple(
        RunnerToolBinding(tool_id, manifest, ()) for tool_id in OPENCLAW_NATIVE_TOOL_IDS
    )
    plan = SimpleNamespace(
        effective_plan_digest="plan",
        tool_bindings=bindings,
        installed_tool_adapters=(adapter,),
        # The installed image declares the loader path the pinned node needs.
        runtime=SimpleNamespace(
            runtime_class=sandbox_module.RuntimeClass.TRUSTED_PROCESS,
            fixed_environment=(
                ("LD_LIBRARY_PATH", "/opt/openclaw/lib"),
                ("PATH", "/usr/bin:/bin"),
            ),
        ),
        limits=SimpleNamespace(
            action_timeout_ms=5_000, setup_timeout_ms=5_000, observation_bytes=4096,
        ),
    )
    monkeypatch.setattr(
        sandbox_module,
        "_snapshot_installed_executable",
        lambda _path, _digest: SimpleNamespace(
            fd=-1, proc_fd_path="/pinned/node", close=lambda: None,
        ),
    )
    handle = sandbox_module.TrustedProcessHandle(
        plan, tmp_path, "lease", SimpleNamespace(proc_fd_path="/pinned/sh"),
        "/usr/bin/git", -1, (0, 0),
    )
    return adapter, plan, bindings, handle


@pytest.mark.asyncio
async def test_openclaw_finalizer_launch_receives_native_session_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, plan, bindings, handle = _openclaw_native_handle(tmp_path, monkeypatch)
    root = Path(adapter.runtime_root_path)
    session_launch: dict[str, str] = {}

    async def capture_session(self, argv, *, timeout_ms, extra_fds=(), environment=None):
        del self, argv, timeout_ms, extra_fds
        session_launch.update(environment)
        raise _LaunchCaptured

    monkeypatch.setattr(
        sandbox_module.TrustedProcessHandle, "_start_stopped_process", capture_session,
    )
    with pytest.raises(_LaunchCaptured):
        await handle.invoke_native_phase(adapter, "initialize", {}, timeout_ms=1_000)

    finalizer_launch: dict[str, object] = {}

    async def capture_exec(*argv, **kwargs):
        finalizer_launch.update(kwargs, argv=argv)
        raise _LaunchCaptured

    async def noop() -> None:
        return None

    async def terminate() -> tuple[object, ...]:
        return (
            sandbox_module.CleanupStepReceipt(
                "runtime", sandbox_module.CleanupState.RELEASED
            ),
        )

    lease = SimpleNamespace(
        lease_id="lease",
        plan=plan,
        _runtime=SimpleNamespace(terminate=terminate),
        _assert_active=lambda: None,
        _begin_operation=noop,
        _end_operation=noop,
    )
    workspace = sandbox_module.LeaseBackedRunnerWorkspace(lease, "plan", bindings)
    await workspace.close_native_runtime()
    monkeypatch.setattr(sandbox_module.asyncio, "create_subprocess_exec", capture_exec)
    with pytest.raises(_LaunchCaptured):
        await workspace.invoke_native_finalization_phase(
            "finalize_command_result", {}, timeout_ms=1_000,
        )

    assert finalizer_launch["argv"][-1] == "--finalize-only"
    assert finalizer_launch["env"] == session_launch
    assert session_launch["LD_LIBRARY_PATH"] == "/opt/openclaw/lib"
    assert session_launch["OPENCLAW_DIST"] == str(root / "dist")


@pytest.mark.asyncio
async def test_native_worker_shell_wrapper_keeps_the_admitted_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Skill eligibility reads PATH in the worker; a login shell would let the
    # image's /etc/profile reset or reorder it before the worker starts.
    adapter, _plan, _bindings, handle = _openclaw_native_handle(tmp_path, monkeypatch)
    launch: dict[str, object] = {}

    async def capture_session(self, argv, *, timeout_ms, extra_fds=(), environment=None):
        del self, timeout_ms, extra_fds
        launch.update(argv=tuple(argv), environment=dict(environment))
        raise _LaunchCaptured

    monkeypatch.setattr(
        sandbox_module.TrustedProcessHandle, "_start_stopped_process", capture_session,
    )
    with pytest.raises(_LaunchCaptured):
        await handle.invoke_native_phase(adapter, "initialize", {}, timeout_ms=1_000)

    argv = launch["argv"]
    environment = launch["environment"]
    assert argv[0] == "/pinned/sh" and argv[3] == "breadboard-native-worker"
    home = tmp_path / "home"
    home.mkdir()
    shown = subprocess.run(
        ("/bin/sh", *argv[1:4], "/usr/bin/printenv", "PATH"),
        env={**environment, "HOME": str(home)},
        capture_output=True, text=True, check=True,
    )
    assert shown.stdout.rstrip("\n") == environment["PATH"]
    assert environment["PATH"].split(os.pathsep)[0] == str(
        Path(adapter.runtime_root_path) / "bin"
    )


_SCAN_BOUNDS = {"max_total_bytes": 1 << 30, "max_inodes": 1 << 16, "max_depth": 64}


def test_workspace_effect_scanner_rejects_root_symlink_swap(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    snapshot, identity = sandbox_module._workspace_effect_snapshot(
        root,
        exclude_root_git=False,
        **_SCAN_BOUNDS,
    )
    assert snapshot == {}
    moved = tmp_path / "workspace-real"
    root.rename(moved)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.txt").write_text("escape", encoding="utf-8")
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceStateError, match="root identity|effects"):
        sandbox_module._workspace_effect_snapshot(
            root,
            exclude_root_git=False,
            **_SCAN_BOUNDS,
            expected_root_identity=identity,
        )


@pytest.mark.parametrize("node_kind", ["symlink", "fifo"])
def test_workspace_effect_scanner_fails_closed_on_unsupported_nodes(
    tmp_path: Path,
    node_kind: str,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    if node_kind == "symlink":
        (root / "link").symlink_to(tmp_path / "target")
    else:
        os.mkfifo(root / "pipe")
    with pytest.raises(WorkspaceStateError, match="unauthorized"):
        sandbox_module._workspace_effect_snapshot(root, exclude_root_git=False, **_SCAN_BOUNDS)


@pytest.mark.asyncio
async def test_workspace_effect_scanner_rejects_fifo_swap_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    victim = root / "victim"
    victim.write_text("regular", encoding="utf-8")
    original_open = os.open
    swapped = False

    def swapping_open(
        path: os.PathLike[str] | str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if dir_fd is not None and path == "victim" and not swapped:
            victim.unlink()
            os.mkfifo(victim)
            swapped = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(sandbox_module.os, "open", swapping_open)
    with pytest.raises(WorkspaceStateError, match="changed|unauthorized"):
        await asyncio.wait_for(
            asyncio.to_thread(
                sandbox_module._workspace_effect_snapshot,
                root,
                exclude_root_git=False,
                **_SCAN_BOUNDS,
            ),
            timeout=1,
        )
    assert swapped


def test_workspace_effect_scanner_omits_content_for_oversize_file(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    content = b"x" * (sandbox_module.EFFECT_CONTENT_UTF8_MAX_BYTES + 1)
    (root / "large.bin").write_bytes(content)
    snapshot, _ = sandbox_module._workspace_effect_snapshot(
        root,
        exclude_root_git=False,
        **_SCAN_BOUNDS,
    )
    assert snapshot["large.bin"]["bytes"] == len(content)
    assert snapshot["large.bin"]["sha256"] == "sha256:" + hashlib.sha256(content).hexdigest()
    assert "content_utf8" not in snapshot["large.bin"]


@pytest.mark.asyncio
async def test_workspace_effect_scanner_rejects_sparse_file_before_reading(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with open(root / "result.bin", "wb") as handle:
        handle.truncate(1 << 40)
    with pytest.raises(WorkspaceStateError, match="admitted storage bound") as raised:
        await asyncio.wait_for(
            asyncio.to_thread(
                sandbox_module._workspace_effect_snapshot,
                root,
                exclude_root_git=False,
                **_SCAN_BOUNDS,
            ),
            timeout=5,
        )
    assert raised.value.code == "output_limit_exceeded"


def test_workspace_effect_scanner_bound_is_cumulative_and_inclusive(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_bytes(b"a" * 500)
    (root / "b.txt").write_bytes(b"b" * 500)
    snapshot, _ = sandbox_module._workspace_effect_snapshot(
        root,
        exclude_root_git=False,
        **{**_SCAN_BOUNDS, "max_total_bytes": 1000},
    )
    assert sorted(snapshot) == ["a.txt", "b.txt"]
    with pytest.raises(WorkspaceStateError, match="admitted storage bound"):
        sandbox_module._workspace_effect_snapshot(
            root,
            exclude_root_git=False,
            **{**_SCAN_BOUNDS, "max_total_bytes": 999},
        )


def test_workspace_effect_scanner_inode_ceiling_counts_empty_nodes(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".git").mkdir()
    for index in range(50):
        (root / ".git" / f"object-{index}").touch()
    (root / "empty").mkdir()
    for index in range(3):
        (root / "empty" / f"{index}.txt").touch()
    # Four nodes: one directory plus three zero-byte files; root .git is excluded.
    snapshot, _ = sandbox_module._workspace_effect_snapshot(
        root,
        exclude_root_git=True,
        **{**_SCAN_BOUNDS, "max_inodes": 4},
    )
    assert sorted(snapshot) == ["empty/0.txt", "empty/1.txt", "empty/2.txt"]
    (root / "empty" / "3.txt").touch()
    with pytest.raises(WorkspaceStateError, match="traversal ceiling") as raised:
        sandbox_module._workspace_effect_snapshot(
            root,
            exclude_root_git=True,
            **{**_SCAN_BOUNDS, "max_inodes": 4},
        )
    assert raised.value.code == "output_limit_exceeded"


def test_workspace_effect_scanner_rejects_nesting_beyond_depth_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "leaf.txt").write_text("x", encoding="utf-8")
    snapshot, _ = sandbox_module._workspace_effect_snapshot(
        root,
        exclude_root_git=False,
        **{**_SCAN_BOUNDS, "max_depth": 3},
    )
    assert sorted(snapshot) == ["a/b/c/leaf.txt"]
    with pytest.raises(WorkspaceStateError, match="traversal ceiling"):
        sandbox_module._workspace_effect_snapshot(
            root,
            exclude_root_git=False,
            **{**_SCAN_BOUNDS, "max_depth": 2},
        )



@pytest.mark.skipif(shutil.which("node") is None, reason="Node is unavailable")
def test_supplier_utf8_replacement_matches_node() -> None:
    corpus = [
        b"\x80",
        b"\xc0\x80",
        b"\xe0\x80\x80",
        b"\xed\xa0\x80",
        b"\xf0\x80\x80\x80",
        b"\xe2\x82",
        b"\xf0\x9f\x92",
        b"\xef\xbf",
        b"\x80\x80",
        b"a\xed\xa0\x80b",
    ]
    encoded = [base64.b64encode(value).decode("ascii") for value in corpus]
    script = (
        "const values = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "console.log(JSON.stringify(values.map(value => "
        "Buffer.from(value, 'base64').toString('utf8'))));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(encoded).encode("utf-8"),
        capture_output=True,
        check=True,
    )
    node_values = json.loads(completed.stdout)
    python_values = [value.decode("utf-8", "replace") for value in corpus]
    assert node_values == python_values


@pytest.mark.asyncio
async def test_omp_native_phase_launch_environment_has_no_python_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    adapter = InstalledToolAdapter(
        adapter_id=OMP_NATIVE_LOCAL_ADAPTER_ID,
        tool_ids=("bash", "edit", "read", "write"),
        runtime_root_path="/opt/omp",
        runtime_root_device=1,
        runtime_root_inode=2,
        runtime_root_owner_uid=0,
        runtime_root_mode="0755",
        manifest_digest=digest,
        executable_relative_path="bin/bun",
        entrypoint_relative_path="worker.js",
        executable_digest=digest,
        entrypoint_digest=digest,
    )
    handle = object.__new__(TrustedProcessHandle)
    handle._native_session_lock = asyncio.Lock()
    handle._native_session = None
    handle.lease_id = "test-lease"
    handle._executable = SimpleNamespace(proc_fd_path="/proc/self/fd/2")
    handle.plan = SimpleNamespace(
        runtime=SimpleNamespace(
            fixed_environment={"FIXED_ENTRY": "val"},
        ),
        limits=SimpleNamespace(
            action_timeout_ms=10_000,
            setup_timeout_ms=5_000,
        ),
    )

    monkeypatch.setattr(sandbox_module, "_validate_native_root", lambda _b: None)
    monkeypatch.setattr(sandbox_module, "_measure_native_file", lambda _p, _d: None)
    monkeypatch.setattr(
        sandbox_module,
        "_snapshot_installed_executable",
        lambda _p, _d: SimpleNamespace(proc_fd_path="/proc/self/fd/3", fd=3, close=lambda: None),
    )
    monkeypatch.setattr(
        TrustedProcessHandle,
        "_validate_native_binding",
        staticmethod(lambda _plan, _binding: None),
    )

    captured_environment: dict[str, str] | None = None

    async def fake_start(*_args: object, **kwargs: object) -> None:
        nonlocal captured_environment
        captured_environment = kwargs.get("environment")  # type: ignore[assignment]
        raise RuntimeError("intercepted_launch")

    handle._start_stopped_process = fake_start  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="intercepted_launch"):
        await handle.invoke_native_phase(adapter, "initialize", {"task": "test"}, timeout_ms=1_000)

    assert captured_environment is not None
    assert captured_environment["FIXED_ENTRY"] == "val"
    assert "PYTHONHOME" not in captured_environment
    assert "LD_LIBRARY_PATH" not in captured_environment
    assert "PYTHONNOUSERSITE" not in captured_environment


@pytest.mark.asyncio
async def test_native_phase_launch_rejects_unknown_adapter_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    adapter = SimpleNamespace(
        adapter_id="unsupported.local.adapter",
        runtime_root_path="/opt/unsupported",
        executable_relative_path="bin/unknown",
        entrypoint_relative_path="worker.js",
        executable_digest=digest,
        entrypoint_digest=digest,
    )
    handle = object.__new__(TrustedProcessHandle)
    handle._native_session_lock = asyncio.Lock()
    handle._native_session = None
    handle.lease_id = "test-lease"
    handle._executable = SimpleNamespace(proc_fd_path="/proc/self/fd/2")
    handle.plan = SimpleNamespace(
        runtime=SimpleNamespace(
            fixed_environment={},
        ),
        limits=SimpleNamespace(
            action_timeout_ms=10_000,
            setup_timeout_ms=5_000,
        ),
    )

    monkeypatch.setattr(sandbox_module, "_validate_native_root", lambda _b: None)
    monkeypatch.setattr(sandbox_module, "_measure_native_file", lambda _p, _d: None)
    monkeypatch.setattr(
        sandbox_module,
        "_snapshot_installed_executable",
        lambda _p, _d: SimpleNamespace(proc_fd_path="/proc/self/fd/3", fd=3, close=lambda: None),
    )
    monkeypatch.setattr(
        TrustedProcessHandle,
        "_validate_native_binding",
        staticmethod(lambda _plan, _binding: None),
    )

    with pytest.raises(SandboxLaunchError) as captured:
        await handle.invoke_native_phase(
            adapter,  # type: ignore[arg-type]
            "initialize",
            {"task": "test"},
            timeout_ms=1_000,
        )

    assert captured.value.code == "runtime_unsupported"
    assert "unsupported.local.adapter" in str(captured.value)


@pytest.mark.parametrize("adapter_id", sorted(sandbox_module.NATIVE_PHASE_TOOL_IDS))
def test_native_phase_tool_ids_are_admissible_installed_adapter_tools(adapter_id: str) -> None:
    # Installed adapters admit only sorted, unique tool IDs, and native phase
    # admission requires them to equal this table's entry exactly.
    digest = "sha256:" + "a" * 64
    tool_ids = sandbox_module.NATIVE_PHASE_TOOL_IDS[adapter_id]
    adapter = InstalledToolAdapter(
        adapter_id=adapter_id,
        tool_ids=tool_ids,
        runtime_root_path="/opt/native",
        runtime_root_device=1,
        runtime_root_inode=2,
        runtime_root_owner_uid=0,
        runtime_root_mode="0755",
        manifest_digest=digest,
        executable_relative_path="bin/runtime",
        entrypoint_relative_path="worker.js",
        executable_digest=digest,
        entrypoint_digest=digest,
    )
    assert adapter.tool_ids == tool_ids
