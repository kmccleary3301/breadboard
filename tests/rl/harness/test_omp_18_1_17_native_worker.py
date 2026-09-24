from __future__ import annotations

from pathlib import Path
import sys

import pytest

from breadboard.rl.harness.omp_native_tools import (
    NativeToolWorker,
    NativeWorkerPhaseError,
    deny_excluded_capabilities,
    pinned_worker_spec,
    verified_tool_worker_path,
)
def _authority_payload(tmp_path: Path) -> dict[str, object]:
    scratch = tmp_path / ".scratch"
    package_dir = tmp_path / "package"
    (scratch / "home").mkdir(parents=True)
    package_dir.mkdir()
    runtime_inputs = {
        "cwd": str(tmp_path),
        "home": str(scratch / "home"),
        "current_date": "2026-09-23",
        "package_dir": str(package_dir),
    }
    return {
        "workspace": str(tmp_path),
        "scratch": str(scratch),
        "package_dir": str(package_dir),
        "runtime_inputs": runtime_inputs,
    }



def test_native_worker_spec_binds_source_and_real_leaves() -> None:
    spec = pinned_worker_spec()
    metadata = spec.as_dict()
    assert metadata["commit"] == "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec"
    assert "pi-natives EditStore/EditSession" in metadata["native_leaves"]
    assert "brush-core Shell" in metadata["native_leaves"]


def test_denial_happens_before_native_resolution() -> None:
    with pytest.raises(PermissionError):
        deny_excluded_capabilities({"path": "https://example.invalid"})
    with pytest.raises(PermissionError):
        deny_excluded_capabilities({"pty": True})


def test_worker_resolves_verified_installed_entrypoint(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path))
    command = worker.start().command
    assert command[1] == str(verified_tool_worker_path())
    assert str(tmp_path) not in command[1]
    worker.stop()


def test_worker_has_no_offline_execute_fallback() -> None:
    assert not hasattr(NativeToolWorker(cwd="/workspace/repo"), "execute")


def test_persistent_worker_phases_preserve_wire_order(tmp_path: Path) -> None:
    script = tmp_path / "phase_worker.py"
    script.write_text(
        """
import json
import struct
import sys
while True:
    header = sys.stdin.buffer.read(4)
    if not header:
        break
    body = sys.stdin.buffer.read(struct.unpack('>I', header)[0])
    request = json.loads(body)
    operation = request['operation']
    payload = request.get('payload', {})
    if operation == 'initialize':
        result = {'schema_version': 'bb.omp-native.v1', 'kind': 'initialized', 'system_prompt': '', 'tool_schemas': [], 'bootstrap': {}}
    elif operation == 'prepare_tools':
        result = {'schema_version': 'bb.omp-native.v1', 'kind': 'prepared', 'calls': payload.get('calls', []), 'history_calls': payload.get('calls', [])}
    elif operation == 'execute_batch':
        calls = payload.get('calls', [])
        result = {'schema_version': 'bb.omp-native.v1', 'kind': 'tool_results', 'results': [{'id': call['id'], 'completion_index': 1 - index, 'content': 'ok', 'details': {}, 'isError': False} for index, call in enumerate(calls)]}
    elif operation == 'close':
        result = {'schema_version': 'bb.omp-native.v1', 'kind': 'closed', 'cleanup': {'processes': [], 'all_dead': True}}
    else:
        result = {'schema_version': 'bb.omp-native.v1', 'kind': 'request', 'messages': [], 'tools': []}
    encoded = json.dumps({'schema_version': 'bb.native-worker.rpc.v1', 'request_id': request['request_id'], 'result': result}).encode()
    sys.stdout.buffer.write(struct.pack('>I', len(encoded)) + encoded)
    sys.stdout.buffer.flush()
""".strip()
    )

    class TestSpec:
        def tool_worker_command(self, *, cwd: str) -> tuple[str, ...]:
            return (sys.executable, str(script), "--cwd", cwd)

    worker = NativeToolWorker(cwd=str(tmp_path), spec=TestSpec())
    assert worker.phase("initialize", {"workspace": str(tmp_path)})["kind"] == "initialized"
    calls = [{"id": "a", "name": "read", "arguments": {}}, {"id": "b", "name": "bash", "arguments": {}}]
    prepared = worker.phase("prepare_tools", {"calls": calls})
    assert prepared["kind"] == "prepared"
    completed = worker.phase("execute_batch", {"calls": calls})
    assert [item["id"] for item in completed["results"]] == ["a", "b"]
    assert [item["completion_index"] for item in completed["results"]] == [1, 0]
    assert worker.close()["cleanup"]["all_dead"] is True


@pytest.mark.skipif(
    not Path(pinned_worker_spec().bun).is_file()
    or not Path(pinned_worker_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_real_pinned_worker_runs_initialize_and_close(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path))
    worker.start()
    initialized = worker.phase(
        "initialize",
        {
            "task": "read the workspace",
            "model_config": {},
            "advertisement": {
                "system_prompt": "",
                "tool_descriptions": {name: name for name in ("read", "bash", "edit", "write")},
                "capability_denials": {
                    capability: {
                        "schema_version": "bb.omp-capability-denial.v1",
                        "capability": capability,
                        "message": f"OMP capability denied: {capability}",
                        "source_ref": "test",
                    }
                    for capability in ("pty", "async")
                },
            },
            **_authority_payload(tmp_path),
        },
    )
    assert initialized["kind"] == "initialized"
    closed = worker.close()
    assert closed["cleanup"]["all_dead"] is True


@pytest.mark.skipif(
    not Path(pinned_worker_spec().bun).is_file()
    or not Path(pinned_worker_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_real_pinned_worker_closes_background_brush_descendant(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path))
    worker.start()
    advertisement = {
        "system_prompt": "",
        "tool_descriptions": {name: name for name in ("read", "bash", "edit", "write")},
        "capability_denials": {
            capability: {
                "schema_version": "bb.omp-capability-denial.v1",
                "capability": capability,
                "message": f"OMP capability denied: {capability}",
                "source_ref": "test",
            }
            for capability in ("pty", "async")
        },
    }
    worker.phase(
        "initialize",
        {
            "task": "background descendant cleanup",
            "model_config": {},
            "advertisement": advertisement,
            "workspace": str(tmp_path),
            **_authority_payload(tmp_path),
        },
    )
    worker.execute_batch([{
        "id": "bash-1",
        "name": "bash",
        "arguments": {"command": "sleep 30 & printf descendant > descendant_marker.txt"},
    }])
    closed = worker.close()
    assert closed["cleanup"] == {"processes": [], "all_dead": True}


@pytest.mark.skipif(
    not Path(pinned_worker_spec().bun).is_file()
    or not Path(pinned_worker_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
@pytest.mark.parametrize("mutation", ["top", "descriptions", "denials", "denial_entry", "settings"])
def test_real_pinned_worker_rejects_advertisement_extra_keys(tmp_path: Path, mutation: str) -> None:
    denial = {
        "schema_version": "bb.omp-capability-denial.v1",
        "capability": "pty",
        "message": "OMP capability denied: pty",
        "source_ref": "test",
    }
    advertisement = {
        "system_prompt": "",
        "tool_descriptions": {name: name for name in ("read", "bash", "edit", "write")},
        "capability_denials": {
            "pty": denial,
            "async": {**denial, "capability": "async", "message": "OMP capability denied: async"},
        },
    }
    if mutation == "top":
        advertisement["extra"] = True
    elif mutation == "descriptions":
        advertisement["tool_descriptions"]["extra"] = "not admitted"
    elif mutation == "denials":
        advertisement["capability_denials"]["extra"] = denial
    elif mutation == "denial_entry":
        advertisement["capability_denials"]["pty"]["extra"] = True
    else:
        advertisement["settings"] = {"request_cap": 8, "model_max_tokens": 2048, "provider_attempts": 1, "extra": True}
    worker = NativeToolWorker(cwd=str(tmp_path))
    try:
        with pytest.raises(NativeWorkerPhaseError, match="invalid keys"):
            worker.phase(
                "initialize",
                {
                    "advertisement": advertisement,
                    **_authority_payload(tmp_path),
                },
            )
    finally:
        worker.stop()
