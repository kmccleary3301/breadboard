from __future__ import annotations

import os
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
    NativeWorkerPhaseError,
    deny_excluded_capabilities,
    deny_pinned_route,
    pinned_worker_spec,
    verified_tool_worker_path,
)

_PINNED_SOURCE_SHA256 = "67822418bad69de015d28a1bbd45fa7be689fdce367dfa3d584bdfcfbfcb5587"


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
    return Path(pinned_worker_spec().source_root)


def _differential_bun() -> Path | None:
    configured = os.environ.get("BB_BUN")
    if configured:
        return Path(configured)
    discovered = shutil.which("bun")
    if discovered:
        return Path(discovered)
    pinned = Path(pinned_worker_spec().bun)
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
    return (Path(pinned_worker_spec().source_root) / relative).read_text(encoding="utf-8")


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
    not Path(pinned_worker_spec().bun).is_file()
    or not Path(pinned_worker_spec().source_root).is_dir(),
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
    worker = NativeToolWorker(cwd=str(tmp_path))
    denials = {
        capability: {
            "schema_version": "bb.omp-capability-denial.v1",
            "capability": capability,
            "message": f"OMP capability denied: {capability}",
            "source_ref": "test",
        }
        for capability in ("pty", "async", "archive", "sqlite", "pdf", "image", "video", "document", "url", "ssh", "internal-resource")
    }
    try:
        worker.start()
        worker.phase(
            "initialize",
            {
                "task": "classify pinned read routes",
                "model_config": {},
                "advertisement": {
                    "system_prompt": "",
                    "tool_descriptions": {name: name for name in ("read", "bash", "edit", "write")},
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
                ],
            },
        )
        assert [call["route"]["route"] for call in prepared["calls"]] == ["archive", "sqlite"]
        assert [call["error"] for call in prepared["calls"]] == [
            "OMP capability denied: archive",
            "OMP capability denied: sqlite",
        ]
    finally:
        worker.stop()

@pytest.mark.skipif(
    not Path(pinned_worker_spec().bun).is_file()
    or not Path(pinned_worker_spec().source_root).is_dir(),
    reason="pinned OMP runtime/source is unavailable on this host",
)
def test_real_pinned_worker_rejects_tampered_classifier_module(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    config = json.loads((root / "native-config.json").read_text(encoding="utf-8"))
    classifier = json.loads(json.dumps(config["route_classifier"]))
    source_root = Path(classifier["source_root"])
    copied_root = tmp_path / "omp-source"
    for entry in [*classifier["modules"].values(), {"path": "bun.lock"}]:
        relative = entry["path"]
        destination = copied_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / relative).read_bytes())
    tampered = copied_root / next(iter(classifier["modules"].values()))["path"]
    tampered.write_bytes(tampered.read_bytes() + b"\n")
    classifier["source_root"] = str(copied_root)
    worker = NativeToolWorker(cwd=str(tmp_path))
    try:
        worker.start()
        with pytest.raises(NativeWorkerPhaseError, match="source verification failed"):
            worker.phase(
                "initialize",
                {
                    "task": "reject tampered source",
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
@pytest.mark.parametrize("capability", ["pty", "async"])
def test_real_pinned_worker_denies_excluded_bash_capabilities(
    tmp_path: Path,
    capability: str,
) -> None:
    worker = NativeToolWorker(cwd=str(tmp_path))
    advertisement = {
        "system_prompt": "",
        "tool_descriptions": {name: name for name in ("read", "bash", "edit", "write")},
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
                "model_config": {},
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
