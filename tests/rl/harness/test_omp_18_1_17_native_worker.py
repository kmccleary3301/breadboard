from __future__ import annotations

import os
from functools import cache
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
import pytest

from breadboard.rl.harness.omp_native_tools import (
    NativeToolWorker,
    PinnedNativeWorkerSpec,
    NativeWorkerPhaseError,
    deny_excluded_capabilities,
    deny_pinned_route,
    pinned_worker_spec,
    verified_tool_worker_path,
)

_PINNED_SOURCE_SHA256 = "67822418bad69de015d28a1bbd45fa7be689fdce367dfa3d584bdfcfbfcb5587"

@cache
def _runtime_spec() -> PinnedNativeWorkerSpec:
    source_root = os.environ.get("BB_OMP_TEST_SOURCE_ROOT")
    bun = os.environ.get("BB_OMP_TEST_BUN")
    if (source_root is None) != (bun is None):
        raise ValueError("both BB_OMP_TEST_SOURCE_ROOT and BB_OMP_TEST_BUN are required")
    if source_root is not None and bun is not None:
        return PinnedNativeWorkerSpec.for_test(bun=Path(bun), source_root=Path(source_root))
    return pinned_worker_spec()


def _source_zip_path() -> Path | None:
    configured = os.environ.get("BB_OMP_SOURCE_ZIP")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != _PINNED_SOURCE_SHA256:
        raise AssertionError(f"pinned OMP source archive digest mismatch: {digest}")
    return path


def _differential_source_root() -> Path:
    return Path(_runtime_spec().source_root)


def _differential_bun() -> Path | None:
    configured = os.environ.get("BB_BUN")
    if configured:
        return Path(configured)
    discovered = shutil.which("bun")
    if discovered:
        return Path(discovered)
    pinned = Path(_runtime_spec().bun)
    return pinned if pinned.is_file() else None


OMP_AVAILABLE = (
    _differential_bun() is not None
    and (
        _source_zip_path() is not None
        or _differential_source_root().is_dir()
    )
)



def _materialize_differential_source(tmp_path: Path) -> Path:
    source_zip = _source_zip_path()
    if source_zip is None:
        return _differential_source_root()
    source_root = tmp_path / "omp-source"
    with zipfile.ZipFile(source_zip) as archive:
        archive.extractall(source_root)
        roots = {
            name.split("/", 1)[0]
            for name in archive.namelist()
            if "/" in name
        }
    if len(roots) != 1:
        raise AssertionError(f"pinned source archive root is not unique: {roots}")
    source_root = source_root / roots.pop()
    for package, exports in {
        "@oh-my-pi/pi-natives": "export const glob = async () => [];\nexport const notebookToEditableText = (value) => value;\n",
        "@oh-my-pi/pi-utils": (
            "export const hasFsCode = () => false;\n"
            "export const isEnoent = (error) => error?.code === 'ENOENT';\n"
            "export const isEnotdir = (error) => error?.code === 'ENOTDIR';\n"
            "export const isWsl = () => false;\n"
            "export const stripWindowsExtendedLengthPathPrefix = (value) => value;\n"
            "export const windowsPathToWslMount = (value) => value;\n"
            "export const BINARY_SNIFF_BYTES = 512;\n"
            "export const isProbablyBinary = () => false;\n"
            "export const isProbablyBinaryHeader = () => false;\n"
            "export const logger = console;\n"
            "export const prompt = async () => undefined;\n"
            "export const readImageMetadata = async () => undefined;\n"
        ),
    }.items():
        package_root = source_root / "node_modules" / package
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "package.json").write_text(
            '{"type":"module","exports":"./index.ts"}',
            encoding="utf-8",
        )
        (package_root / "index.ts").write_text(exports, encoding="utf-8")
    handler_stubs = {
        "agent-protocol.ts": "export class AgentProtocolHandler { readonly scheme = 'agent'; }\n",
        "artifact-protocol.ts": "export class ArtifactProtocolHandler { readonly scheme = 'artifact'; }\n",
        "history-protocol.ts": "export class HistoryProtocolHandler { readonly scheme = 'history'; }\n",
        "issue-pr-protocol.ts": (
            "export class IssueProtocolHandler { readonly scheme = 'issue'; }\n"
            "export class PrProtocolHandler { readonly scheme = 'pr'; }\n"
        ),
        "local-protocol.ts": "export class LocalProtocolHandler { readonly scheme = 'local'; }\n",
        "mcp-protocol.ts": "export class McpProtocolHandler { readonly scheme = 'mcp'; }\n",
        "memory-protocol.ts": "export class MemoryProtocolHandler { readonly scheme = 'memory'; }\n",
        "omp-protocol.ts": "export class OmpProtocolHandler { readonly scheme = 'omp'; }\n",
        "rule-protocol.ts": "export class RuleProtocolHandler { readonly scheme = 'rule'; }\n",
        "security-protocol.ts": "export class SecurityProtocolHandler { readonly scheme = 'security'; }\n",
        "skill-protocol.ts": "export class SkillProtocolHandler { readonly scheme = 'skill'; }\n",
        "ssh-protocol.ts": "export class SshProtocolHandler { readonly scheme = 'ssh'; }\n",
        "vault-protocol.ts": "export class VaultProtocolHandler { readonly scheme = 'vault'; }\n",
        "xd-protocol.ts": "export class XdProtocolHandler { readonly scheme = 'xd'; }\n",
    }
    handler_root = source_root / "packages/coding-agent/src/internal-urls"
    for filename, contents in handler_stubs.items():
        (handler_root / filename).write_text(contents, encoding="utf-8")
    return source_root
def _pinned_source(relative: str) -> str:
    source_zip = _source_zip_path()
    if source_zip is not None:
        with zipfile.ZipFile(source_zip) as archive:
            matches = [name for name in archive.namelist() if name.endswith(f"/{relative}")]
            if len(matches) != 1:
                raise AssertionError(f"pinned source member is not unique: {relative} -> {matches}")
            return archive.read(matches[0]).decode("utf-8")
    return (Path(_runtime_spec().source_root) / relative).read_text(encoding="utf-8")


def _source_registry_sets() -> dict[str, set[str]]:
    router = _pinned_source("packages/coding-agent/src/internal-urls/router.ts")
    imports: dict[str, str] = {}
    for names, module in re.findall(r'import \{([^}]+)\} from "\./([^"]+)";', router):
        for name in names.split(","):
            imports[name.strip()] = module
    constructor = router.split("constructor() {", 1)[1].split("\n\t}", 1)[0]
    handlers = re.findall(r"this\.register\(new (\w+ProtocolHandler)\(\)\)", constructor)
    internal = set()
    for handler in handlers:
        handler_source = _pinned_source(f"packages/coding-agent/src/internal-urls/{imports[handler]}.ts")
        class_body = handler_source.split(f"class {handler}", 1)[1]
        scheme = re.search(r'readonly scheme = "([^"]+)"', class_body).group(1)
        if handler != "SshProtocolHandler":
            internal.add(scheme)

    archive = _pinned_source("packages/utils/src/ar/registry.ts")
    archive_block = archive.split("const FORMAT_EXTENSIONS", 1)[1].split("};", 1)[0]
    archive_extensions = {
        f".{extension}"
        for values in re.findall(r": \[([^\]]+)\]", archive_block)
        for extension in re.findall(r'"([^"]+)"', values)
    }

    video = _pinned_source("packages/coding-agent/src/utils/video.ts")
    video_block = video.split("const VIDEO_EXTENSION_LOOKUP", 1)[1].split("};", 1)[0]
    video_extensions = set(re.findall(r'"(\.[^"]+)": true', video_block))

    markit = _pinned_source("packages/coding-agent/src/utils/markit.ts")
    markit_values = re.search(r"CONVERTIBLE_EXTENSIONS[^=]*= new Set\(\[([^\]]+)\]\)", markit).group(1)
    document_extensions = set(re.findall(r'"(\.[^"]+)"', markit_values))

    mime = _pinned_source("packages/utils/src/mime.ts")
    mime_values = re.search(r"SUPPORTED_IMAGE_MIME_TYPES = new Set\(\[([^\]]+)\]\)", mime).group(1)
    image_mime_types = set(re.findall(r'"([^"]+)"', mime_values))
    read_tool = _pinned_source("packages/coding-agent/src/tools/read.ts")
    svg_match = re.search(r"only supports \.(\w+) and \.(\w+) files", read_tool)
    assert svg_match is not None
    image_extensions = {
        "." + mime_type.removeprefix("image/").replace("jpeg", "jpg")
        for mime_type in image_mime_types
    } | {".jpeg", *(f".{suffix}" for suffix in svg_match.groups())}

    sqlite = _pinned_source("packages/coding-agent/src/tools/sqlite-reader.ts")
    sqlite_pattern = re.search(r"SQLITE_PATH_PATTERN = /([^/]+)/", sqlite).group(1)
    sqlite_alternatives = re.search(r"\(\?:([^)]*)\)", sqlite_pattern).group(1).split("|")
    sqlite_extensions = set()
    for alternative in sqlite_alternatives:
        sqlite_extensions.add("." + alternative.replace("?", ""))
        if "?" in alternative:
            sqlite_extensions.add("." + alternative.replace("3?", ""))

    return {
        "internal-resource": internal,
        "archive": archive_extensions,
        "video": video_extensions,
        "document": document_extensions,
        "image": image_extensions,
        "sqlite": sqlite_extensions,
    }


def _source_route_patterns() -> tuple[list[str], list[str]]:
    source = _pinned_source("packages/coding-agent/src/tools/path-utils.ts")
    ssh_body = source.split("export function pathTargetsSsh", 1)[1].split("}", 1)[0]
    url_body = source.split("export function isReadableUrlPath", 1)[1].split("}", 1)[0]
    pattern = r"/((?:\\.|[^/])+)/i\.test"
    return (re.findall(pattern, url_body), re.findall(pattern, ssh_body))




def _description_policy() -> dict[str, dict[str, object]]:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    provenance = json.loads((root / "tool-surface.json").read_text(encoding="utf-8"))["native_description_provenance"]
    return {
        name: {
            "original_sha256": "sha256:" + provenance["original_sha256"][name],
            "bounded_sha256": "sha256:" + provenance["bounded_sha256"][name],
            "removed_spans": provenance["removed_spans"][name],
        }
        for name in ("read", "bash", "edit", "write")
    }


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


def _model_registry() -> dict[str, object]:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    return json.loads((root / "native-config.json").read_text(encoding="utf-8"))["model_registry"]


def _lease_model() -> dict[str, object]:
    """The OMP lease model_config shape bound by policy_provider."""
    return {
        "id": "capture",
        "name": "capture",
        "api": "openai-completions",
        "provider": "openai",
        "baseUrl": "http://127.0.0.1:9/v1",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 32768,
        "maxTokens": 2048,
        "compat": {
            "supportsStore": True,
            "supportsDeveloperRole": True,
            "supportsUsageInStreaming": True,
            "maxTokensField": "max_completion_tokens",
            "supportsStrictMode": False,
        },
    }


def _test_denials() -> dict[str, dict[str, str]]:
    return {
        capability: {
            "schema_version": "bb.omp-capability-denial.v1",
            "capability": capability,
            "message": f"OMP capability denied: {capability}",
            "source_ref": "test",
        }
        for capability in ("pty", "async")
    }



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
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
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


_PINNED_COMPAT_SCRIPT = """
const payload = JSON.parse(await Bun.stdin.text());
const { buildModel } = await import(`${payload.root}/packages/catalog/src/build.ts`);
process.stdout.write(JSON.stringify(payload.providers.map((provider) => {
  const { compat } = buildModel({ ...payload.model, provider, api: "openai-completions", baseUrl: payload.baseUrl });
  return [compat.supportsMultipleSystemMessages, compat.supportsDeveloperRole];
})));
"""


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


_INSTALLED_SPEC = pinned_worker_spec()


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


def _sample_conversation_messages() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "Help me refactor the database module."},
        {"role": "assistant", "content": [{"type": "text", "text": "Sure, let's inspect the files first."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": {"path": "db.py"}}], "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass\ndef query(): pass"}], "isError": False},
        {"role": "assistant", "content": [{"type": "text", "text": "I see the functions. Let's add transactions."}], "stopReason": "stop"},
        {"role": "user", "content": "Also add logging."},
    ]


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_forced_off_for_existing_target(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase(
            "initialize",
            {
                "task": "test compaction forced off",
                "model_config": _lease_model(),
                "advertisement": {
                    "bounded_description_policy": _description_policy(),
                    "model_registry": _model_registry(),
                    "capability_denials": _test_denials(),
                },
                **_authority_payload(tmp_path),
            },
        )
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "threshold",
                "checkpoint": "before_request",
                "context_window": 131072,
            },
        )
        assert res["kind"] == "compaction_unavailable"
        assert res["reason"] == "compaction_disabled"
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_phases_threshold_not_triggered(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test threshold not triggered",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "context_window": 131072,
            },
        )
        assert res["kind"] == "compaction_unavailable"
        assert res["reason"] == "not_triggered"
    finally:
        worker.stop()


def _run_stock_compaction_oracle(payload: dict[str, Any]) -> dict[str, Any]:
    bun = _differential_bun()
    assert bun is not None and bun.is_file()
    source_root = _differential_source_root()
    pkgs_dir = Path("/Users/kylemccleary/.cache/bb-compaction-e4/pkgs/@oh-my-pi__pi-coding-agent@18.1.17/node_modules")

    # Independent stock SDK session; no copied message or compaction implementation.
    oracle_script = """
    const input = JSON.parse(await Bun.stdin.text());
    const { source_root: root, model_config, messages, summaries = [] } = input;
    const { createAgentSession, Settings } = await import(`${root}/packages/coding-agent/src/sdk.ts`);
    const { SessionManager } = await import(`${root}/packages/coding-agent/src/session/session-manager.ts`);
    const { ModelRegistry } = await import(`${root}/packages/coding-agent/src/config/model-registry.ts`);
    const { AuthStorage } = await import(`${root}/packages/ai/src/auth-storage.ts`);
    const compaction = await import(`${root}/packages/agent/src/compaction/compaction.ts`);
    const { DEFAULT_SHAKE_CONFIG, RESCUE_SHAKE_CONFIG } = await import(`${root}/packages/agent/src/compaction/shake.ts`);
    const { convertMessages } = await import(`${root}/packages/ai/src/providers/openai-completions.ts`);
    const wireRequests = [];
    let summaryIndex = 0;
    // session-manager.ts:99, session-migrations.ts:7, shake.ts:448.
    let id = 0;
    let timestamp = 1000;
    const nativeRandomUUID = crypto.randomUUID;
    const replayUUID = () => `00000000-0000-4000-8000-${(++id).toString(16).padStart(12, "0")}`;
    crypto.randomUUID = replayUUID;
    Bun.randomUUIDv7 = () => "00000000-0000-7000-8000-000000000000";
    const NativeDate = Date;
    class ReplayDate extends NativeDate {
      constructor(value = timestamp) { super(value); }
      static now() { return timestamp; }
    }
    globalThis.Date = ReplayDate;
    const settings = await Settings.init({
      cwd: input.cwd, agentDir: input.cwd, inMemory: true, configFiles: [],
      overrides: {
        "retry.enabled": false, "retry.fallbackChains": {}, "autoContinue.enabled": false,
        "prewalk.enabled": false, "imageUrls.enabled": false, "title.refreshOnReplan": false,
        ...Object.fromEntries(Object.entries(input.settings).map(([key, value]) => [`compaction.${key}`, value])),
      },
    });
    const manager = SessionManager.inMemory(input.cwd);
    const authStorage = await AuthStorage.create(":memory:");
    const registry = new ModelRegistry(authStorage, `${input.cwd}/oracle-models.yml`, { settings });
    registry.registerProvider("breadboard-route", {
      baseUrl: model_config.baseUrl, api: "openai-completions", apiKey: "oracle-key", models: [model_config],
    });
    const model = registry.find("breadboard-route", model_config.id);
    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(init.body);
      wireRequests.push({ messages: body.messages, max_tokens: body.max_completion_tokens,
        ...(body.tool_choice === undefined ? {} : { tool_choice: body.tool_choice }) });
      if (summaryIndex >= summaries.length) throw new Error("oracle scripted answers exhausted");
      const chunk = { id: "oracle-response", object: "chat.completion.chunk", created: 2, model: model.id,
        choices: [{ index: 0, delta: { role: "assistant", content: summaries[summaryIndex++] }, finish_reason: "stop" }] };
      globalThis.Date = ReplayDate;
      return new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`,
        { headers: { "content-type": "text/event-stream" } });
    };
    const { session } = await createAgentSession({
      cwd: input.cwd, agentDir: input.cwd, authStorage, modelRegistry: registry, model,
      thinkingLevel: "off", toolNames: ["read", "bash", "edit", "write"], restrictToolNames: true,
      allowRestrictedCustomTools: false, settings, sessionManager: manager,
      contextFiles: [], skills: [], rules: [], promptTemplates: [], slashCommands: [],
      customTools: [], extensions: [], additionalExtensionPaths: [], disableExtensionDiscovery: true,
      enableMCP: false, enableLsp: false, enableIrc: false, skipPythonPreflight: true,
      hasUI: false, interactivePrompts: false, rebindModelAfterDiscovery: false,
      getApiKey: async () => "oracle-key",
    });
    const results = [];
    for (const pass of input.passes ?? [{ messages, action: input.action }]) {
      crypto.randomUUID = replayUUID;
      for (const message of pass.messages) manager.appendMessage(message);
      crypto.randomUUID = nativeRandomUUID;
      session.agent.replaceMessages(manager.buildSessionContext().messages);
      const entries = manager.getBranch().filter(entry => entry.type === "message" || entry.type === "compaction");
      timestamp = 1000 + id;
      crypto.randomUUID = replayUUID;
      let shakeResult;
      if (pass.action === "rescue_only") {
        shakeResult = await session.shake("elide", { config: RESCUE_SHAKE_CONFIG });
      } else if (pass.action === "shake") {
        shakeResult = await session.shake("elide", { config: { ...DEFAULT_SHAKE_CONFIG, ...input.settings.shake } });
      } else if (pass.action === "handoff") {
        globalThis.Date = class extends NativeDate {
          constructor(value = NativeDate.parse("2026-09-23T12:00:00")) { super(value); }
          static now() { return timestamp; }
        };
        await session.handoff(compaction.AUTO_HANDOFF_THRESHOLD_FOCUS);
      } else {
        await session.compact();
      }
      const entry = manager.getBranch().findLast(entry => entry.type === "compaction");
      if (pass.action === "rescue") await session.shake("elide", { config: RESCUE_SHAKE_CONFIG });
      globalThis.Date = class extends NativeDate {
        constructor(value = NativeDate.parse("2026-09-23T12:00:00")) { super(value); }
        static now() { return timestamp; }
      };
      const mainContext = await session.agent.buildSideRequestContext(
        await session.convertMessagesToLlm(manager.buildSessionContext().messages), session.agent.state.systemPrompt);
      globalThis.Date = ReplayDate;
      results.push({
        summary: entry?.summary, compactionMessage: { details: entry?.details },
        firstKeptIndex: entries.findIndex(item => item.id === entry?.firstKeptEntryId),
        finalMessages: manager.buildSessionContext().messages,
        mainMessages: convertMessages(model, mainContext, model.compat),
        shakeResult,
      });
    }
    await Bun.write(Bun.stdout, JSON.stringify({ wireRequests, ...results.at(-1), passes: results }));
    await session.dispose();
    """
    full_payload = {"cwd": str(Path.cwd()), **payload, "source_root": str(source_root)}
    env = {**os.environ}
    if pkgs_dir.is_dir():
        env["NODE_PATH"] = str(pkgs_dir)
    proc = subprocess.run(
        [str(bun), "-e", oracle_script],
        input=json.dumps(full_payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"Stock oracle failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_handoff_on_threshold(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        init_res = worker.phase("initialize", {
            "task": "test threshold handoff",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = _sample_conversation_messages()
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "handoff"

        # Compare against stock oracle
        stock = _run_stock_compaction_oracle({
            "action": "handoff",
            "cwd": str(tmp_path),
            "model_config": _lease_model(),
            "system_prompt": [init_res["system_prompt"]],
            "messages": messages,
            "settings": {"keepRecentTokens": 10},
            "summaries": ["Stock handoff document text."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]
        assert res["summary_request"]["tool_choice"] == stock["wireRequests"][0]["tool_choice"] == "none"

        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Stock handoff document text.",
                "preparation": prep,
            },
        )
        assert fin["kind"] == "compaction_finalized"
        assert fin["summary"] == stock["summary"]
        assert fin["first_kept_index"] == stock["firstKeptIndex"]
        assert fin["compaction_message"]["details"] == stock["compactionMessage"]["details"]
        assert fin["messages"] == stock["finalMessages"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_shake_sufficient(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test shake sufficient",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "detail " * 100},
            {"role": "assistant", "content": [{"type": "text", "text": "Inspecting files."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": json.loads("{\"path\": \"db.py\"}")}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass\n" * 2000}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "I see the functions."}]},
            {"role": "user", "content": "Also add logging: " + "more words " * 200},
        ]
        settings = {"methodOrder": ["shake", "soft"], "shake": {"protectTokens": 50, "minSavings": 50}}
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 11000, "completion_tokens": 100, "total_tokens": 11100},
                "context_window": 13000,
                "settings": settings,
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "shake"
        assert res["summary_request"] is None

        fin = worker.phase(
            "finalize_compaction",
            {
                "preparation": prep,
            },
        )
        assert fin["kind"] == "compaction_finalized"
        stock = _run_stock_compaction_oracle({
            "action": "shake",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": settings,
        })
        assert fin["messages"] == stock["finalMessages"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_shake_insufficient_falls_through(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        init_res = worker.phase("initialize", {
            "task": "test shake insufficient fallthrough",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        # Messages with NO toolResult (shake reclaims 0 tokens)
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "words " * 400},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, I can help."}]},
            {"role": "user", "content": "Also add logging: " + "detail " * 400},
            {"role": "assistant", "content": [{"type": "text", "text": "Will do."}]},
            {"role": "user", "content": "Proceed."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["shake", "soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"
        assert prep["fallbackFromShake"] is True

        stock = _run_stock_compaction_oracle({
            "action": "soft",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": {"keepRecentTokens": 10},
            "summaries": ["Stock fallback summary.", "Short summary."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]

        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Stock fallback summary.",
                "preparation": prep,
            },
        )
        if fin["kind"] == "compaction_followup_request":
            assert fin["request"]["messages"] == stock["wireRequests"][1]["messages"]
            fin = worker.phase(
                "finalize_compaction",
                {
                    "summary": "Stock fallback summary.",
                    "preparation": prep,
                    "followup_summaries": ["Short summary."],
                },
            )
        assert fin["kind"] == "compaction_finalized"
        assert fin["summary"] == stock["summary"]
        assert fin["first_kept_index"] == stock["firstKeptIndex"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_soft_on_overflow(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test soft on overflow",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "detail " * 50},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, inspecting."}]},
            {"role": "user", "content": "Proceed with implementation: " + "task " * 50},
            {"role": "assistant", "content": [{"type": "text", "text": "Implementation done."}]},
            {"role": "user", "content": "Next step."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 50},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        stock = _run_stock_compaction_oracle({
            "action": "soft",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": {"keepRecentTokens": 50},
            "summaries": ["Stock overflow summary.", "Short summary."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]
        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Stock overflow summary.",
                "preparation": prep,
            },
        )
        if fin["kind"] == "compaction_followup_request":
            fin = worker.phase(
                "finalize_compaction",
                {
                    "summary": "Stock overflow summary.",
                    "preparation": prep,
                    "followup_summaries": ["Short summary."],
                },
            )
        assert fin["kind"] == "compaction_finalized"
        assert fin["retry"] is True
        assert fin["summary"] == stock["summary"]
        assert fin["first_kept_index"] == stock["firstKeptIndex"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_split_turn(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test split turn",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Initial conversation turn: " + "detail " * 50},
            {"role": "assistant", "content": [{"type": "text", "text": "Initial answer."}]},
            {"role": "user", "content": "Help me refactor the database module."},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure, let us inspect files."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": json.loads(json.dumps({"path": "db.py"}))}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass"}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "I see functions."}]},
            {"role": "user", "content": "Also add logging."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        stock = _run_stock_compaction_oracle({
            "action": "soft",
            "model_config": _lease_model(),
            "messages": messages,
            "settings": {"keepRecentTokens": 10},
            "summaries": ["Split turn summary.", "Turn prefix summary.", "Short summary."],
        })
        assert res["summary_request"]["messages"] == stock["wireRequests"][0]["messages"]
        assert res["turn_prefix_request"]["messages"] == stock["wireRequests"][1]["messages"]

        fin = worker.phase(
            "finalize_compaction",
            {
                "summary": "Split turn summary.",
                "turn_prefix_summary": "Turn prefix summary.",
                "preparation": prep,
            },
        )
        assert fin["kind"] == "compaction_followup_request"
        assert fin["request"]["messages"] == stock["wireRequests"][2]["messages"]

        fin2 = worker.phase(
            "finalize_compaction",
            {
                "summary": "Split turn summary.",
                "turn_prefix_summary": "Turn prefix summary.",
                "preparation": prep,
                "followup_summaries": ["Short summary."],
            },
        )
        assert fin2["kind"] == "compaction_finalized"
        assert fin2["summary"] == stock["summary"]
        assert fin2["first_kept_index"] == stock["firstKeptIndex"]
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
@pytest.mark.parametrize("method", ["handoff", "soft"])
def test_stock_equality_update_previous_summary(tmp_path: Path, method: str) -> None:
    import copy
    from breadboard.rl.harness.runners.omp_semantics import OMPSemanticsState
    from breadboard_engine.provider.native_response import NativeProviderResponse, NativeToolCall

    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        initialized = worker.phase("initialize", {
            "task": "retain file journal",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        state = OMPSemanticsState(task="retain file journal", worker=worker, request_cap=8)
        state.begin_query()
        response = state.prepare_response(NativeProviderResponse(
            binding_digest="binding", request_digest="request", response_id="read-response", model="capture",
            content="Inspecting.", finish_reason="toolUse",
            tool_calls=(NativeToolCall("read-1", "read", '{"path":"journal.py"}'),),
            usage={"prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050},
        ), usage=worker.phase("parse_usage", {"usage": {
            "prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050,
        }})["usage"])
        prepared = state.prepare_tools(response.calls)
        response.assistant["content"][-1]["arguments"] = prepared["history_calls"][0]["arguments"]
        state.commit_tool_results(response.calls, [{"content": [{"type": "text", "text": "file contents"}]}])
        state.messages.append({"role": "user", "content": [{"type": "text", "text": "Finish the journal."}]})
        first_messages = copy.deepcopy(state.messages)
        settings = {"methodOrder": [method, "soft"], "keepRecentTokens": 10}
        summaries = []
        passes = []
        for index in range(2):
            if index:
                state.exit_status = None
                state.begin_query()
                state.prepare_response(NativeProviderResponse(
                    binding_digest="binding", request_digest="request", response_id="second-response", model="capture",
                    content="Continuing the implementation.", finish_reason="stop",
                    usage={"prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050},
                ), usage=worker.phase("parse_usage", {"usage": {
                    "prompt_tokens": 50000, "completion_tokens": 50, "total_tokens": 50050,
                }})["usage"])
                state.messages.append({"role": "user", "content": [{"type": "text", "text": "Run the remaining tests."}]})
            added = first_messages if not index else copy.deepcopy(state.messages[len(first_finalized):])
            passes.append({"messages": added, "action": method})
            prepared = worker.phase("prepare_compaction", {
                "messages": state.messages, "reason": "threshold", "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000},
                "context_window": 131072, "settings": settings,
            })
            assert prepared["kind"] == "compaction_prepared"
            payload = {"preparation": prepared["preparation"], "summary": f"Summary {index}."}
            summaries.append(payload["summary"])
            finalized = worker.phase("finalize_compaction", payload)
            if finalized["kind"] == "compaction_followup_request":
                payload["followup_summaries"] = [f"Short {index}."]
                summaries.extend(payload["followup_summaries"])
                finalized = worker.phase("finalize_compaction", payload)
            assert finalized["kind"] == "compaction_finalized"
            state.messages = copy.deepcopy(finalized["messages"])
            if not index:
                first_finalized = copy.deepcopy(state.messages)
            else:
                second_request = prepared["summary_request"]
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": _lease_model(), "messages": first_messages,
            "settings": settings, "summaries": summaries, "passes": passes,
        })
        assert second_request["messages"] == stock["wireRequests"][1 if method == "handoff" else 2]["messages"]
        assert finalized["messages"] == stock["finalMessages"]
        assert "journal.py" in finalized["summary"]
        assert "Summary 0." in json.dumps(second_request["messages"])
        # Stock conversion excludes usage; preserve the main request bytes.
        with_usage = worker.phase("project_request", {"messages": state.messages})
        no_usage = [{key: value for key, value in message.items() if key != "usage"} for message in state.messages]
        assert with_usage == worker.phase("project_request", {"messages": no_usage})
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_rescue_path(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        lease = _lease_model()
        worker.start()
        worker.phase("initialize", {
            "task": "test rescue path",
            "model_config": lease,
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [
            {"role": "user", "content": "Help me refactor the database module: " + "prefix context " * 600},
            {"role": "assistant", "content": [{"type": "text", "text": "Inspecting files."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": json.loads("{\"path\": \"db.py\"}")}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass\n" * 50}], "isError": False},
            {"role": "assistant", "content": [{"type": "text", "text": "Here is big output."}, {"type": "toolCall", "id": "tc2", "name": "bash", "arguments": json.loads("{\"command\": \"cat output.log\"}")}], "stopReason": "toolUse"},
            {"role": "toolResult", "toolCallId": "tc2", "toolName": "bash", "content": [{"type": "text", "text": "log line output " * 200}], "isError": False},
            {"role": "user", "content": "Please proceed."},
        ]
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 3000, "completion_tokens": 100, "total_tokens": 3100},
                "context_window": 131072,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 1000},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        # Context window configured so unshaken tail exceeds fit budget, but shaken tail fits
        stock = _run_stock_compaction_oracle({
            "action": "rescue",
            "model_config": lease,
            "messages": messages,
            "settings": {"keepRecentTokens": 1000},
            "summaries": ["Rescue summary text.", "Short summary."],
        })
        fin_payload = {
            "summary": "Rescue summary text.",
            "preparation": prep,
        }
        if res.get("turn_prefix_request"):
            fin_payload["turn_prefix_summary"] = "Rescue turn prefix."
        fin = worker.phase("finalize_compaction", fin_payload)
        followup_index = 0
        followup_answers = ["Short summary."]
        while fin["kind"] == "compaction_followup_request":
            fin = worker.phase(
                "finalize_compaction",
                {
                    **fin_payload,
                    "followup_summaries": followup_answers[:followup_index + 1],
                },
            )
            followup_index += 1
        assert fin["kind"] == "compaction_finalized"
        assert fin["retry"] is True
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_stock_equality_dead_end_path(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        # Model with tiny context window so history cannot possibly fit
        lease = _lease_model()
        tiny_lease = {**lease, "contextWindow": 50}
        worker.start()
        worker.phase("initialize", {
            "task": "test dead-end path",
            "model_config": tiny_lease,
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = _sample_conversation_messages()
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": messages,
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 50,
                "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] == "soft"

        fin_payload = {
            "summary": "Dead-end summary.",
            "preparation": prep,
        }
        if res.get("turn_prefix_request"):
            fin_payload["turn_prefix_summary"] = "Dead-end turn prefix."
        fin = worker.phase("finalize_compaction", fin_payload)
        followup_index = 0
        followup_answers = ["Short summary."]
        while fin["kind"] == "compaction_followup_request":
            fin = worker.phase(
                "finalize_compaction",
                {
                    **fin_payload,
                    "followup_summaries": followup_answers[:followup_index + 1],
                },
            )
            followup_index += 1
        assert fin["kind"] == "compaction_finalized"
        # Due to tiny context window, retryFits is False, resulting in dead-end: retry=False
        assert fin["retry"] is False
    finally:
        worker.stop()

@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_phases_overflow(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test overflow compaction",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        # Reason overflow must fire regardless of usage
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "overflow",
                "checkpoint": "overflow",
                "usage": {"prompt_tokens": 500, "completion_tokens": 50, "total_tokens": 550},
                "context_window": 131072,
                "settings": {"keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        prep = res["preparation"]
        assert prep["method"] in ("soft", "shake")
    finally:
        worker.stop()


@pytest.mark.skipif(
    not Path(_runtime_spec().bun).is_file()
    or not Path(_runtime_spec().source_root).is_dir(),
    reason="pinned OMP runtime is unavailable on this host",
)
def test_compaction_text_only_skips_snapcompact(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.start()
        worker.phase("initialize", {
            "task": "test text-only skips snapcompact",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        # Even with methodOrder explicitly starting with snapcompact:
        res = worker.phase(
            "prepare_compaction",
            {
                "messages": _sample_conversation_messages(),
                "reason": "threshold",
                "checkpoint": "before_request",
                "usage": {"prompt_tokens": 120000, "completion_tokens": 1000, "total_tokens": 121000},
                "context_window": 131072,
                "settings": {"methodOrder": ["snapcompact", "soft"], "keepRecentTokens": 10},
            },
        )
        assert res["kind"] == "compaction_prepared"
        # snapcompact was skipped because model.input does not contain "image"; soft was selected
        assert res["preparation"]["method"] == "soft"
    finally:
        worker.stop()


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
@pytest.mark.parametrize("terminal_text", [False, True])
def test_agent_end_stock_continuation_gate(tmp_path: Path, terminal_text: bool) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.phase("initialize", {
            "task": "continue after compaction",
            "model_config": _lease_model(),
            "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(),
                "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        messages = [*_sample_conversation_messages(), {
            "role": "assistant", "stopReason": "stop",
            "content": [{"type": "text", "text": "done"}] if terminal_text else [],
        }]
        prepared = worker.phase("prepare_compaction", {
            "messages": messages, "reason": "threshold", "checkpoint": "agent_end",
            "context_window": 131072, "usage": {"prompt_tokens": 120000, "completion_tokens": 1000},
            "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
        })
        assert prepared["kind"] == "compaction_prepared"
        payload = {"preparation": prepared["preparation"], "summary": "Summary.", "turn_prefix_summary": "Prefix."}
        finalized = worker.phase("finalize_compaction", payload)
        if finalized["kind"] == "compaction_followup_request":
            finalized = worker.phase("finalize_compaction", {**payload, "followup_summaries": ["Short summary."]})
        assert finalized["kind"] == "compaction_finalized"
        if terminal_text:
            assert "continuation" not in finalized
        else:
            prompt = _pinned_source("packages/coding-agent/src/prompts/system/auto-continue.md")
            assert finalized["continuation"] == [{
                "role": "developer", "content": [{"type": "text", "text": prompt}],
                "attribution": "agent", "synthetic": True, "timestamp": 1000 + len(messages),
            }]
        # Stock invalidates billed usage from an assistant before the new compaction
        # (session-maintenance.ts:2465-2477), retaining the stored-history floor.
        next_preparation = worker.phase("prepare_compaction", {
            "messages": [*finalized["messages"], *finalized.get("continuation", [])],
            "reason": "threshold", "checkpoint": "before_request", "context_window": 131072,
            "usage": {"prompt_tokens": 120000, "completion_tokens": 1000},
            "settings": {"methodOrder": ["soft"], "keepRecentTokens": 10},
        })
        assert next_preparation["kind"] == "compaction_unavailable"
        assert next_preparation["reason"] == "not_triggered"
    finally:
        worker.stop()


@pytest.mark.asyncio
async def test_handoff_tool_choice_reaches_transport_without_changing_main_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from breadboard.rl.harness.runners.base import PolicyRuntimeInvokeRequest, freeze_json_object, thaw_json
    from breadboard_engine.compilation.contracts import canonical_sha256
    from breadboard_engine.compilation.provider_response import OMP_RESPONSE_CONSUMER_ID
    from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
    from tests.rl.harness.test_policy_provider import _unbound_native_chat_client, _OPENHANDS_EPISODE, _digest
    from tests.rl.harness import test_policy_provider as fixtures

    profile_type = fixtures.OpenAICompletionsProviderProfile

    def streaming_profile(**kwargs: Any) -> Any:
        return profile_type(**{**kwargs, "capabilities": {
            **kwargs["capabilities"], "supports_streaming": True, "supports_store": True,
        }})

    monkeypatch.setattr(fixtures, "OpenAICompletionsProviderProfile", streaming_profile)

    factory = OpenAIChatRuntime.create_client_from_profile
    client, plan, _ = _unbound_native_chat_client(
        tmp_path, monkeypatch, target_id="oh-my-pi-r2@18.1.17",
        consumer_id=OMP_RESPONSE_CONSUMER_ID, model="model-a",
        request_policy={"mode": "streaming", "include_usage": True, "max_token_field": "max_completion_tokens",
                        "strict_tools": None, "enable_thinking": None},
        sampling={"n": 1}, max_token_feature="max_completion_tokens",
        request_features=["max_completion_tokens", "n", "store", "stream_options", "streaming"],
    )
    client._transport.close()
    client._transport = factory(client._runtime, client._profile)
    bodies: list[bytes] = []

    def send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
        bodies.append(request.content)
        chunk = {
            "id": f"response-{len(bodies)}", "object": "chat.completion.chunk", "created": 1, "model": "model-a",
            "choices": [{"index": 0, "finish_reason": "stop", "delta": {"role": "assistant", "content": "done"}}],
        }
        return httpx.Response(200, request=request, headers={"content-type": "text/event-stream"},
                              content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())

    monkeypatch.setattr(client._transport.http_client, "send", send)
    client.bind_compiled_plan(plan)
    tools = tuple(thaw_json(tool) for tool in client._target_projection.chat_tools)
    client.bind_native_stream("stock base system", tools, accept_truncated_stream=True)
    main = {
        "model": "model-a", "messages": [{"role": "system", "content": "stock base system"}, {"role": "user", "content": "task"}],
        "tools": list(tools),
    }
    summary = {**main, "tool_choice": "none"}
    try:
        for turn, body in enumerate((main, summary, main), 1):
            await client.invoke(PolicyRuntimeInvokeRequest(
                episode_id=_OPENHANDS_EPISODE, effective_plan_digest=plan.canonical_digest(),
                binding_digest=_digest("binding"), policy_slot_id="model:model-a",
                request_digest=canonical_sha256(body), request_payload=freeze_json_object(body, field_name="request"),
                turn=turn, attempt=1, compaction_summary=turn == 2,
            ))
    finally:
        await client.close()
    assert json.loads(bodies[1])["tool_choice"] == "none"
    assert "tool_choice" not in json.loads(bodies[0])
    assert bodies[0] == bodies[2]


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
@pytest.mark.asyncio
async def test_conductor_large_tool_batch_compacts_from_stored_floor_and_matches_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    from dataclasses import replace
    from breadboard.rl.harness import native_stream_profiles
    from breadboard.rl.harness.runners import conductor as conductor_module, omp_semantics
    from breadboard.rl.harness.runners.base import thaw_json
    from breadboard.rl.harness.runners.conductor import ConductorRunRequest
    from breadboard_engine.provider.native_response import NativeProviderResponse
    from tests.rl.harness.test_runner_conductor import _native_close_test_case, _open, _digest

    lease = {**_lease_model(), "id": "model-a", "name": "Model A"}
    settings = {"methodOrder": ["handoff", "soft"], "keepRecentTokens": 10}
    plan, client, tools = _native_close_test_case(
        monkeypatch, agent_end_compaction=True, action_timeout_ms=40000,
        runtime_profile_override={"context_window": lease["contextWindow"], "compaction_settings": settings},
        limit_updates_override={"max_turns": 3, "observation_bytes": 2_000_000, "transcript_bytes": 8_000_000, "response_bytes": 4_000_000},
    )
    authority = _authority_payload(tmp_path)
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    states = []
    histories = []
    prepared_results = []
    original_profile = next(iter(conductor_module.NATIVE_STREAM_PROFILES.values()))

    def state_factory(task: str, system_prompt: str, bootstrap: dict[str, Any]) -> Any:
        state = omp_semantics.OMPSemanticsState(
            task="previous context " * 30000, system_prompt=system_prompt, request_cap=4,
        )
        state.begin_query()
        state.prepare_response(NativeProviderResponse(
            binding_digest="binding", request_digest="request", response_id="prior-turn", model=lease["id"],
            content="Prior task completed.", finish_reason="stop",
        ), usage=worker.phase("parse_usage", {"usage": None})["usage"])
        state.resume_after_compaction([{"role": "user", "content": [{"type": "text", "text": task}]}])
        states.append(state)
        return state

    profile = replace(
        original_profile, phase_schema_version=omp_semantics.PHASE_SCHEMA_VERSION,
        state_factory=state_factory, state_module=omp_semantics,
        max_turns=plan.effective_capabilities.limits.max_turns,
        action_timeout_ms=plan.effective_capabilities.limits.action_timeout_ms,
        episode_timeout_seconds=120, compaction_checkpoints=frozenset({"before_request"}),
        assistant_usage_phase="parse_usage",
    )
    monkeypatch.setattr(native_stream_profiles, "NATIVE_STREAM_PROFILES", {profile.consumer_id: profile})
    monkeypatch.setattr(conductor_module, "NATIVE_STREAM_PROFILES", {profile.consumer_id: profile})
    monkeypatch.setattr(type(tools), "declared_workspace", property(lambda self: str(tmp_path)))
    monkeypatch.setattr(tools, "native_runtime_inputs", lambda **kwargs: dict(authority["runtime_inputs"]))
    original_phase = tools.invoke_native_phase

    async def phase(operation: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if operation == "initialize":
            return worker.phase(operation, {
                "task": "large batch", "model_config": lease, **authority,
                "compaction": True,
                "advertisement": {
                    "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                    "capability_denials": _test_denials(),
                },
            })
        if operation == "execute_batch":
            result = dict(await original_phase(operation, payload, **kwargs))
            result["schema_version"] = omp_semantics.PHASE_SCHEMA_VERSION
            # A scripted native tool batch, committed through production semantics.
            result["results"][0]["id"] = f"call-{main_count}"
            result["results"][0]["content"] = [{"type": "text", "text": "large payload " * 30000 if main_count == 1 else "Small result."}]
            return result
        if operation == "close":
            worker.stop()
            result = dict(await original_phase(operation, payload, **kwargs))
            result["schema_version"] = omp_semantics.PHASE_SCHEMA_VERSION
            return result
        if operation == "prepare_compaction":
            histories.append(copy.deepcopy(payload["messages"]))
            result = worker.phase(operation, payload)
            prepared_results.append((result.get("kind"), result.get("reason")))
            return result
        return worker.phase(operation, payload)

    monkeypatch.setattr(tools, "invoke_native_phase", phase)
    original_invoke = client.invoke
    main_count = 0
    client.responses.clear()

    async def invoke(request: Any) -> Any:
        nonlocal main_count
        body = thaw_json(request.request_payload)
        summary = request.compaction_summary
        if not summary:
            main_count += 1
        response = {
            "binding_digest": _digest("native-binding"), "request_digest": conductor_module.canonical_sha256(body).removeprefix("sha256:"),
            "request_body": body, "response_id": f"native-{len(client.requests)}",
            "finish_reason": "stop" if summary or main_count == 3 else "toolUse",
            "content": "Batch summary." if summary else ("done" if main_count == 3 else "Inspecting."),
            "tool_calls": [] if summary or main_count == 3 else [{"id": f"call-{main_count}", "name": "read", "arguments": '{"path":"journal.py"}'}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050},
            "stream_fragments": [],
        }
        client.responses.append({"native_response": response})
        return await original_invoke(request)

    monkeypatch.setattr(client, "invoke", invoke)
    session, *_ = await _open(observation=client.observation, plan=plan, client=client, tools=tools)
    try:
        await session.run(ConductorRunRequest({"prompt": "large batch"}))
        main_requests = [thaw_json(request.request_payload) for request in client.requests if not request.compaction_summary]
        assert len(main_requests) == 3
        assert len([request for request in client.requests if request.compaction_summary]) == 1, prepared_results
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": lease, "messages": histories[-1],
            "settings": settings, "summaries": ["Batch summary."], "action": "handoff",
        })
        assert main_requests[-1]["messages"] == stock["mainMessages"]
        assert states[0].messages[-1]["usage"]["input"] == 1000
    finally:
        await session.close()
        worker.stop()


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
def test_overflow_projection_uses_replay_timestamp(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    try:
        worker.phase("initialize", {
            "task": "replay overflow", "model_config": _lease_model(), "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        payload = {
            "http_status": 400, "messages": [],
            "provider_request_duration_ms": 12.5,
            "response_body_text": json.dumps({"error": {
                "message": "Your input exceeds the context window of this model.",
                "type": "invalid_request_error", "code": "context_length_exceeded",
            }}),
        }
        first = worker.phase("project_provider_failure", payload)
        second = worker.phase("project_provider_failure", payload)
        # The pinned manager/session initialization consumes two replay IDs.
        assert first["message"]["timestamp"] == 1002
        assert first == second
        assert first["message"]["duration"] == 12.5
        for invalid in (None, True, -1):
            with pytest.raises(NativeWorkerPhaseError, match="recorded provider request duration"):
                worker.phase("project_provider_failure", {
                    **payload, "provider_request_duration_ms": invalid,
                })
        with pytest.raises(NativeWorkerPhaseError, match="recorded provider request duration"):
            worker.phase("project_provider_failure", {
                key: value for key, value in payload.items() if key != "provider_request_duration_ms"
            })
    finally:
        worker.stop()


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
@pytest.mark.parametrize("method_order", [["handoff"], ["soft"], ["shake", "soft"]])
@pytest.mark.parametrize("tool_text", ["record payload value\n" * 1800, "x"], ids=["positive_savings", "zero_savings"])
@pytest.mark.asyncio
async def test_failed_preparation_preserves_native_rescue_history(
    tmp_path: Path, method_order: list[str], tool_text: str,
) -> None:
    from breadboard.rl.harness.runners.native_compaction import compact_history

    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    lease = {**_lease_model(), "contextWindow": 4000}
    messages = [
        {"role": "user", "content": "Inspect the records."},
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": "read-1", "name": "read", "arguments": {"path": "records.txt"}},
        ], "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": "read-1", "toolName": "read", "isError": False,
         "content": [{"type": "text", "text": tool_text}]},
    ]
    prepared_results = []
    try:
        worker.phase("initialize", {
            "task": "retain rescue edits", "model_config": lease, "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })

        async def phase(operation, payload):
            result = worker.phase(operation, dict(payload))
            prepared_results.append(result)
            return result

        async def no_summary(request):
            pytest.fail("Failed preparation must not issue a summary request")

        settings = {"methodOrder": method_order}
        if "shake" in method_order:
            settings["shake"] = {"protectTokens": 0, "minSavings": 0}
        usage = {"prompt_tokens": 50000, "completion_tokens": 0, "total_tokens": 50000}
        compacted = await compact_history(
            messages, reason="threshold", checkpoint="before_request", usage=usage,
            context_window=4000, settings=settings, model_id=lease["id"], tools=[],
            phase=phase, exchange=no_summary, trace_requests=[],
        )
        prepared = prepared_results[0]
        assert prepared["kind"] == "compaction_unavailable"
        assert prepared["reason"] == "nothing_to_compact"
        assert prepared["history_rewritten"] is True
        assert compacted is not None
        assert compacted.event["kind"] == "history_rewrite"
        assert compacted.retry is False
        assert compacted.continuation is None
        assert compacted.messages == prepared["messages"]
        assert compacted.messages != messages
        projected = worker.phase("project_request", {"messages": compacted.messages})
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": lease, "messages": messages,
            "settings": settings, "action": "shake" if "shake" in method_order else "rescue_only",
        })
        assert stock["wireRequests"] == []
        assert stock["finalMessages"] == compacted.messages
        assert stock["mainMessages"] == projected["messages"]
        if tool_text == "x":
            assert stock["shakeResult"]["tokensFreed"] == 0
            assert stock["shakeResult"]["toolResultsDropped"] + stock["shakeResult"]["blocksDropped"] > 0
        # Re-check the committed prefix: no old tool body may be restored.
        repeated = await compact_history(
            compacted.messages, reason="threshold", checkpoint="before_request",
            usage=usage, context_window=4000, settings=settings,
            model_id=lease["id"], tools=[], phase=phase, exchange=no_summary, trace_requests=[],
        )
        assert repeated is None
        assert worker.phase("project_request", {"messages": compacted.messages}) == projected
    finally:
        worker.stop()


def test_provider_failure_duration_capability_is_source_specific() -> None:
    from breadboard.rl.harness.runners import omp_semantics
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES

    assert [p.state_module for p in NATIVE_STREAM_PROFILES.values()
            if p.provider_failure_requires_duration] == [omp_semantics]


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime unavailable")
def test_zero_savings_shake_reclaims_overflow_without_falling_back(tmp_path: Path) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path), spec=_runtime_spec())
    messages = _sample_conversation_messages()[:3]
    messages[2]["content"] = [{"type": "text", "text": "x"}]
    settings = {"methodOrder": ["shake", "soft"], "shake": {"protectTokens": 0, "minSavings": 0}}
    lease = _lease_model()
    try:
        worker.phase("initialize", {
            "task": "retain zero-savings rewrite", "model_config": lease, "compaction": True,
            "advertisement": {
                "bounded_description_policy": _description_policy(), "model_registry": _model_registry(),
                "capability_denials": _test_denials(),
            },
            **_authority_payload(tmp_path),
        })
        prepared = worker.phase("prepare_compaction", {
            "messages": messages, "reason": "overflow", "checkpoint": "overflow",
            "context_window": lease["contextWindow"],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 0, "total_tokens": 1000},
            "settings": settings,
        })
        assert prepared["kind"] == "compaction_prepared"
        assert prepared["preparation"]["method"] == "shake"
        assert prepared["summary_request"] is None
        assert prepared["turn_prefix_request"] is None
        stock = _run_stock_compaction_oracle({
            "cwd": str(tmp_path), "model_config": lease, "messages": messages,
            "settings": settings, "action": "shake",
        })
        assert stock["shakeResult"]["tokensFreed"] == 0
        assert stock["shakeResult"]["toolResultsDropped"] == 1
        assert stock["finalMessages"] == prepared["preparation"]["shakenMessages"]
    finally:
        worker.stop()
