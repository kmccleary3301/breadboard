from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
import json
import re
import subprocess
import sys
import zipfile
import pytest
from breadboard.rl.harness.omp_native_tools import NativeToolWorker, NativeWorkerPhaseError, deny_excluded_capabilities, deny_pinned_route, pinned_worker_spec, verified_tool_worker_path

from tests.rl.harness.omp_18_1_17_test_support import (
    OMP_AVAILABLE,
    _INSTALLED_SPEC,
    _PINNED_COMPAT_SCRIPT,
    _authority_payload,
    _description_policy,
    _differential_bun,
    _differential_source_root,
    _lease_model,
    _model_registry,
    _runtime_spec,
    _source_registry_sets,
    _source_route_patterns,
    _test_denials,
)

def test_native_worker_spec_binds_source_and_real_leaves() -> None:
    spec = pinned_worker_spec()
    metadata = spec.as_dict()
    assert metadata["commit"] == "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec"
    assert "pi-natives EditStore/EditSession" in metadata["native_leaves"]
    assert "brush-core Shell" in metadata["native_leaves"]


def test_static_denial_happens_before_native_resolution() -> None:
    with pytest.raises(PermissionError):
        deny_excluded_capabilities({"pty": True})


def test_static_route_policy_is_typed_and_fail_closed() -> None:
    policy = {
        "archive": {"message": "archive denied"},
        "internal-resource": {"message": "internal denied"},
    }
    with pytest.raises(PermissionError, match="archive denied"):
        deny_pinned_route({"route": "archive"}, denial_policy=policy)
    with pytest.raises(PermissionError, match="unknown pinned read route"):
        deny_pinned_route({"route": "unexpected"}, denial_policy=policy)
    deny_pinned_route({"route": "file"}, denial_policy=policy)
    with pytest.raises(PermissionError, match="internal denied"):
        deny_pinned_route({"route": "internal:agent"}, denial_policy=policy)


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime/source is unavailable on this host",
)
def test_real_pinned_worker_classifies_fuzz_overadmission_fixtures(tmp_path: Path) -> None:
    literal_root = tmp_path / "file:" / "evil"
    literal_root.mkdir(parents=True)
    archive_path = literal_root / "data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("member", "fixture")
    sqlite_path = literal_root / "xyz.sqlite"
    sqlite_path.write_bytes(b"SQLite format 3\0")
    (tmp_path / "visible.txt").write_text("read route observation preserves the real tool\n", encoding="utf-8")
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    config = json.loads(
        (Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17/native-config.json")
        .read_text(encoding="utf-8")
    )
    denials = config["capability_denials"]
    try:
        worker.start()
        worker.phase(
            "initialize",
            {
                "task": "classify pinned read routes",
                "model_config": _lease_model(),
                "advertisement": {
                    "bounded_description_policy": _description_policy(),
                    "model_registry": _model_registry(),
                    "capability_denials": denials,
                },
                **_authority_payload(tmp_path),
            },
        )
        prepared = worker.phase(
            "prepare_tools",
            {
                "calls": [
                    {"id": "archive", "name": "read", "arguments": {"path": "file://evil/data.zip:member"}},
                    {"id": "sqlite", "name": "read", "arguments": {"path": "file://evil/xyz.sqlite:users"}},
                    {"id": "url", "name": "read", "arguments": {"path": "HTTP://example.invalid"}},
                    {"id": "ssh", "name": "read", "arguments": {"path": "ssh://example.invalid/etc/hosts"}},
                    {"id": "plain", "name": "read", "arguments": {"path": "visible.txt"}},
                ],
            },
        )
        assert [call["route"]["route"] for call in prepared["calls"]] == ["archive", "sqlite", "url", "ssh", "file"]
        assert [call.get("error") for call in prepared["calls"]] == [
            "OMP capability denied: archive",
            "OMP capability denied: sqlite",
            "OMP capability denied: url",
            "OMP capability denied: ssh",
            None,
        ]
        completed = worker.phase("execute_batch", {})
        plain = next(item for item in completed["results"] if item["id"] == "plain")
        assert plain["isError"] is False
        assert any("read route observation preserves the real tool" in part["text"] for part in plain["content"])
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime/source is unavailable on this host",
)
def test_real_pinned_worker_rejects_tampered_classifier_module(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    config = json.loads((root / "native-config.json").read_text(encoding="utf-8"))
    classifier = json.loads(json.dumps(config["route_classifier"]))
    source_root = Path(_runtime_spec().source_root)
    copied_root = tmp_path / "omp-source"
    for entry in [*classifier["modules"].values(), {"path": "bun.lock"}]:
        relative = entry["path"]
        destination = copied_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / relative).read_bytes())
    tampered = copied_root / next(iter(classifier["modules"].values()))["path"]
    tampered.write_bytes(tampered.read_bytes() + b"\n")
    classifier["source_root"] = str(copied_root)
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        with pytest.raises(NativeWorkerPhaseError, match="source verification failed"):
            worker.phase(
                "initialize",
                {
                    "task": "reject tampered source",
                    "model_config": _lease_model(),
                    "advertisement": {
                        "bounded_description_policy": _description_policy(),
                        "model_registry": _model_registry(),
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
                    "route_classifier": classifier,
                    **_authority_payload(tmp_path),
                },
            )
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime/source is unavailable on this host",
)
def test_real_pinned_worker_rejects_unknown_classifier_module(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    config = json.loads((root / "native-config.json").read_text(encoding="utf-8"))
    classifier = json.loads(json.dumps(config["route_classifier"]))
    classifier["modules"]["unknown.ts"] = next(iter(classifier["modules"].values()))
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        with pytest.raises(NativeWorkerPhaseError, match="route_classifier.modules has invalid keys"):
            worker.phase(
                "initialize",
                {
                    "task": "reject unknown classifier module",
                    "model_config": _lease_model(),
                    "advertisement": {
                        "bounded_description_policy": _description_policy(),
                        "model_registry": _model_registry(),
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
                    "route_classifier": classifier,
                    **_authority_payload(tmp_path),
                },
            )
    finally:
        worker.stop()


@pytest.mark.skipif(
    not OMP_AVAILABLE,
    reason="pinned OMP runtime/source archive is unavailable on this host",
)
def test_route_declarations_match_parsed_pinned_omp_registries() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    policy = json.loads((root / "native-config.json").read_text())["capability_denials"]
    parsed = _source_registry_sets()
    for capability, expected in parsed.items():
        assert set(policy[capability]["route"]["extensions" if capability != "internal-resource" else "schemes"]) == expected
    url_patterns, ssh_patterns = _source_route_patterns()
    assert policy["url"]["route"]["patterns"] == url_patterns
    assert policy["ssh"]["route"]["patterns"] == ssh_patterns


def test_worker_resolves_verified_installed_entrypoint(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=pinned_worker_spec())
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
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_real_pinned_worker_runs_initialize_and_close(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    worker.start()
    initialized = worker.phase(
        "initialize",
        {
            "task": "read the workspace",
            "model_config": _lease_model(),
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
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
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_real_pinned_worker_validates_partial_bash_call_before_execution(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    marker = tmp_path / "must-not-exist"
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "check arguments",
            "model_config": _lease_model(),
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": {
                    name: {
                        "schema_version": "bb.omp-capability-denial.v1",
                        "capability": name,
                        "message": f"OMP capability denied: {name}",
                        "source_ref": "test",
                    }
                    for name in ("pty", "async")
                },
            },
            **_authority_payload(tmp_path),
        })
        prepared = worker.phase("prepare_tools", {"calls": [{
            "id": "malformed", "name": "bash", "arguments": '{"command":',
        }]})
        assert prepared["calls"][0]["arguments"] == {}
        assert "command must be a string (was missing)" in prepared["calls"][0]["error"]
        completed = worker.phase("execute_batch", {"calls": prepared["calls"]})
        assert completed["results"][0]["isError"] is True
        assert "Received arguments:\n{}" in completed["results"][0]["content"]
        assert not marker.exists()
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
@pytest.mark.parametrize("capability", ["pty", "async"])
def test_real_pinned_worker_denies_excluded_bash_capabilities(
    tmp_path: Path,
    capability: str,
) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    advertisement = {
        "bounded_description_policy": _description_policy(),
        "model_registry": _model_registry(),
        "capability_denials": {
            name: {
                "schema_version": "bb.omp-capability-denial.v1",
                "capability": name,
                "message": f"OMP capability denied: {name}",
                "source_ref": "test",
            }
            for name in ("pty", "async")
        },
    }
    try:
        worker.start()
        worker.phase(
            "initialize",
            {
                "task": "deny excluded capability",
                "model_config": _lease_model(),
                "advertisement": advertisement,
                **_authority_payload(tmp_path),
            },
        )
        marker = tmp_path / "should-not-exist"
        prepared = worker.phase(
            "prepare_tools",
            {
                "calls": [{
                    "id": capability,
                    "name": "bash",
                    "arguments": {"command": f"touch {marker}", capability: True},
                }],
            },
        )
        assert prepared["calls"][0]["error"] == f"OMP capability denied: {capability}"
        completed = worker.phase("execute_batch", {"calls": prepared["calls"]})
        assert completed["results"][0]["content"] == f"OMP capability denied: {capability}"
        assert completed["results"][0]["isError"] is True
        assert not marker.exists()
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_real_pinned_worker_closes_background_brush_descendant(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    worker.start()
    advertisement = {
        "bounded_description_policy": _description_policy(),
        "model_registry": _model_registry(),
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
            "model_config": _lease_model(),
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
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
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
        "bounded_description_policy": _description_policy(),
        "model_registry": _model_registry(),
        "capability_denials": {
            "pty": denial,
            "async": {**denial, "capability": "async", "message": "OMP capability denied: async"},
        },
    }
    if mutation == "top":
        advertisement["extra"] = True
    elif mutation == "descriptions":
        advertisement["bounded_description_policy"]["extra"] = {}
    elif mutation == "denials":
        advertisement["capability_denials"]["extra"] = denial
    elif mutation == "denial_entry":
        advertisement["capability_denials"]["pty"]["extra"] = True
    else:
        advertisement["settings"] = {"request_cap": 8, "model_max_tokens": 2048, "provider_attempts": 1, "extra": True}
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        message = "invalid capability denial" if mutation == "denials" else "invalid keys"
        with pytest.raises(NativeWorkerPhaseError, match=message):
            worker.phase(
                "initialize",
                {
                    "advertisement": advertisement,
                    **_authority_payload(tmp_path),
                },
            )
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
@pytest.mark.parametrize(
    ("provider_id", "host"),
    [("openai", "openai"), ("azure", "azureOpenAI"), ("xiaomi-token-plan-cn", "xiaomi")],
)
def test_real_pinned_worker_rejects_known_host_provider_id(tmp_path: Path, provider_id: str, host: str) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        with pytest.raises(NativeWorkerPhaseError, match=rf"names pinned known host {host}\b"):
            worker.phase(
                "initialize",
                {
                    "task": "reject known-host provider id",
                    "model_config": _lease_model(),
                    "advertisement": {
                        "bounded_description_policy": _description_policy(),
                        "model_registry": {"provider_id": provider_id},
                        "capability_denials": _test_denials(),
                    },
                    **_authority_payload(tmp_path),
                },
            )
    finally:
        worker.stop()


@pytest.mark.skipif(
    _differential_bun() is None or not _differential_source_root().is_dir(),
    reason="bun or pinned OMP source is unavailable on this host",
)
def test_pinned_compat_treats_declared_route_provider_as_custom_host(tmp_path: Path) -> None:
    source_root = _differential_source_root()
    # catalog/src/build.ts reaches pi-utils only through utils.ts.
    shim = tmp_path / "resolve" / "@oh-my-pi" / "pi-utils"
    shim.mkdir(parents=True)
    (shim / "package.json").write_text('{"type":"module","exports":"./index.ts"}', encoding="utf-8")
    (shim / "index.ts").write_text(
        f"export {{ isRecord }} from {json.dumps(str(source_root / 'packages/utils/src/type-guards.ts'))};\n"
        f"export {{ wrapFetchForExtraCa }} from {json.dumps(str(source_root / 'packages/utils/src/tls-fetch.ts'))};\n",
        encoding="utf-8",
    )
    script = tmp_path / "compat.ts"
    script.write_text(_PINNED_COMPAT_SCRIPT, encoding="utf-8")
    lease = _lease_model()
    completed = subprocess.run(
        [str(_differential_bun()), str(script)],
        input=json.dumps({
            "root": str(source_root),
            # "openai" is the lease protocol family; pinned compat reads it as the OpenAI host.
            "providers": [_model_registry()["provider_id"], "openai"],
            "model": {key: lease[key] for key in ("id", "name", "reasoning", "input", "contextWindow", "maxTokens")},
            "baseUrl": lease["baseUrl"],
        }),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NODE_PATH": str(tmp_path / "resolve")},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [[False, False], [True, True]]


@pytest.mark.skipif(
    not Path(_INSTALLED_SPEC.bun).is_file()
    or not any((Path(_INSTALLED_SPEC.source_root) / "packages/natives/native").glob("pi_natives.linux-x64-*.node")),
    reason="installed /opt/omp runtime with pinned natives is unavailable on this host",
)
def test_installed_worker_renders_route_model_line_with_pinned_compat(tmp_path: Path) -> None:
    lease = _lease_model()
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_INSTALLED_SPEC)
    try:
        worker.start()
        initialized = worker.phase(
            "initialize",
            {
                "task": "render the bound model identity",
                "model_config": lease,
                "advertisement": {
                    "bounded_description_policy": _description_policy(),
                    "model_registry": _model_registry(),
                    "capability_denials": _test_denials(),
                },
                **_authority_payload(tmp_path),
            },
        )
        model_lines = [line for line in initialized["system_prompt"].splitlines() if line.startswith("- Model:")]
        assert model_lines == [f"- Model: {_model_registry()['provider_id']}/{lease['id']}"]
        request = worker.phase(
            "project_request",
            {"messages": [{"role": "user", "content": [{"type": "text", "text": "do task"}], "timestamp": 0}]},
        )
        # A custom host coalesces the system prompt into one system message.
        roles = [message["role"] for message in request["messages"]]
        assert roles.count("system") == 1 and "developer" not in roles
        assert request["messages"][0] == {"role": "system", "content": initialized["system_prompt"]}
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
@pytest.mark.parametrize(("tz", "instant_ms"), [
    ("Pacific/Kiritimati", 1_791_633_600_000),  # 2026-10-10T12:00Z = 2026-10-11 local (+14)
    ("Pacific/Pago_Pago", 1_791_608_400_000),  # 2026-10-10T05:00Z = 2026-10-09 local (-11)
])
def test_reminder_date_is_stock_local_date_of_admitted_instant(tmp_path: Path, tz: str, instant_ms: int) -> None:
    spec = _runtime_spec()
    stock = subprocess.run(
        [spec.bun, "-e", "const { formatLocalCalendarDate } = await import(process.argv[1]);"
         "console.log(formatLocalCalendarDate(new Date(Number(process.argv[2]))));",
         f"{spec.source_root}/packages/coding-agent/src/utils/local-date.ts", str(instant_ms)],
        env={**os.environ, "TZ": tz}, capture_output=True, text=True, check=True,
    ).stdout.strip()
    utc_date = datetime.fromtimestamp(instant_ms / 1000, timezone.utc).date().isoformat()
    assert stock != utc_date
    payload = _authority_payload(tmp_path)
    payload["runtime_inputs"]["message_timestamp_ms"] = str(instant_ms)
    worker = NativeToolWorker(cwd=str(tmp_path), env={"TZ": tz}, spec=spec)
    try:
        initialized = worker.phase("initialize", {
            "task": "date", "model_config": _lease_model(),
            "advertisement": {"bounded_description_policy": _description_policy(),
                              "model_registry": _model_registry(), "capability_denials": _test_denials()},
            **payload,
        })
        assert initialized["bootstrap"]["current_date"] == stock
        request = worker.phase("project_request", {"messages": [{"role": "user", "content": "do", "timestamp": 0}]})
    finally:
        worker.stop()
    reminders = re.findall(r"Today: (\d{4}-\d{2}-\d{2});", json.dumps(request["messages"]))
    assert reminders == [stock]

