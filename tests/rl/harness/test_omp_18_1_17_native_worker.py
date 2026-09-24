from __future__ import annotations

from pathlib import Path
import json
import re
import sys
import subprocess
import pytest

from breadboard.rl.harness.omp_native_tools import (
    NativeToolWorker,
    NativeWorkerPhaseError,
    classify_capability,
    deny_excluded_capabilities,
    pinned_worker_spec,
    verified_tool_worker_path,
)

OMP_AVAILABLE = (
    Path(pinned_worker_spec().bun).is_file()
    and Path(pinned_worker_spec().source_root).is_dir()
)
def _pinned_source(relative: str) -> str:
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


_TS_ROUTE_PROBE = r"""
import { expandPath, isReadableUrlPath, pathTargetsSsh } from "%s/packages/coding-agent/src/tools/path-utils.ts";

const input = JSON.parse(await Bun.stdin.text());
const policy = input.policy;

function routeExtension(value, extensions) {
  const base = value.toLowerCase();
  return extensions.some(extension => typeof extension === "string" && new RegExp(`${extension.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=[:?]|$)`, "i").test(base));
}

function splitImageQuestion(value) {
  const supportsQuestion = !value.includes("://") || value.startsWith("attachment://") || value.startsWith("local://");
  if (!supportsQuestion || /\.(?:sqlite3?|db3?)(?=(?::|\?|$))/i.test(value)) return value;
  const index = value.indexOf("?");
  if (index < 0) return value;
  const question = new URLSearchParams(value.slice(index + 1)).get("q");
  return question ? value.slice(0, index) : value;
}

function matches(capability, value) {
  const route = policy[capability]?.route;
  if (!route) return false;
  if ((route.patterns ?? []).some(pattern => new RegExp(pattern, "i").test(value))) return true;
  if ((route.prefixes ?? []).some(prefix => value.toLowerCase().startsWith(prefix.toLowerCase()))) return true;
  const scheme = value.match(/^([a-z][a-z0-9+.-]*):\/\//i)?.[1]?.toLowerCase();
  if (scheme && (route.schemes ?? []).some(item => item.toLowerCase() === scheme)) return true;
  return Array.isArray(route.extensions) && routeExtension(value, route.extensions);
}

function classify(value) {
  let candidate = expandPath(value);
  candidate = splitImageQuestion(candidate);
  if (isReadableUrlPath(candidate)) return "url";
  if (pathTargetsSsh(candidate)) return "ssh";
  const internal = candidate.match(/^([a-z][a-z0-9+.-]*):\/\//i)?.[1]?.toLowerCase();
  if (internal) {
    const schemes = new Set((policy["internal-resource"]?.route?.schemes ?? []).map(item => item.toLowerCase()));
    if (internal === "local" && schemes.has(internal)) {
      const parsed = new URL(candidate);
      if (parsed.pathname) candidate = decodeURIComponent(parsed.pathname);
      else return "internal-resource";
    } else if (schemes.has(internal)) {
      return "internal-resource";
    }
  }
  candidate = expandPath(candidate);
  for (const capability of ["archive", "sqlite", "pdf", "image", "video", "document"]) {
    if (matches(capability, candidate)) return capability;
  }
  return null;
}

console.log(JSON.stringify(input.values.map(value => ({ value, route: classify(value) }))));
""" % pinned_worker_spec().source_root


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



@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTP://example.invalid", "url"),
        ("https:/example.invalid", "url"),
        ("www.example.invalid/path", "url"),
        (" https://example.invalid", None),
        ("./db.sqlite:users", "sqlite"),
        ("db%2Esqlite:users", None),
        ("ssh://host/path", "ssh"),
        ("ssh:host/path", None),
        ("prefix/ssh://host/path", "ssh"),
        ("https%3A%2F%2Fexample.invalid", None),
        ("file://LOCALHOST/tmp/state%2Esqlite", "sqlite"),
        ("FILE://LOCALHOST/tmp/state%2Esqlite", "sqlite"),
        ("file:///tmp/x%3Fname.sqlite", "sqlite"),
        ("file:///tmp/x%23name.sqlite", "sqlite"),
        ("file:///tmp/x%2Fname.sqlite", "sqlite"),
        ("@agent://foo", "internal-resource"),
        ("@AGENT://foo", "internal-resource"),
    ],
)
def test_route_matchers_follow_source_path_forms(value: str, expected: str | None) -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    policy = json.loads((root / "native-config.json").read_text())["capability_denials"]
    assert classify_capability(value, denial_policy=policy) == expected


@pytest.mark.skipif(not OMP_AVAILABLE, reason="pinned OMP runtime is unavailable on this host")
def test_route_classification_differential_matches_pinned_bun(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    policy = json.loads((root / "native-config.json").read_text())["capability_denials"]
    assert classify_capability("file:///tmp/state.sqlite", denial_policy=policy) == "sqlite"
    values = [
        "file:///tmp/state.sqlite",
        "file:///tmp/state%2Esqlite:users",
        "file://LOCALHOST/tmp/state%2Esqlite",
        "FILE://LOCALHOST/tmp/state%2Esqlite",
        "file:///tmp/x%3Fname.sqlite",
        "file:///tmp/x%23name.sqlite",
        "file:///tmp/x%2Fname.sqlite",
        "file:///tmp/archive.tar.gz:member",
        "file:///tmp/read.pdf:1",
        "file:///tmp/image.png",
        "file:///tmp/movie.mp4",
        "file:///tmp/report.docx",
        "@/tmp/state.sqlite",
        "@local:///tmp/state.sqlite:users",
        "@agent://foo",
        "@AGENT://foo",
        "\u00a0/tmp/state.sqlite",
        "~/.cache/state.sqlite",
        ":/tmp/state.sqlite",
        "/tmp/image.png?q=describe",
        "/tmp/state.sqlite?q=ignored",
        "/tmp/image.png:img",
        "local:///tmp/state.sqlite:users",
        "local:///tmp/image.png:img",
        "local://opaque/resource",
        "https://example.invalid",
        "https:/example.invalid",
        "HTTPS://example.invalid",
        "www.example.invalid/path",
        "ssh://host/path",
        "prefix/ssh://host/path",
        "agent://item",
        "artifact://item",
        "history://item",
        "issue://item",
        "mcp://item",
        "memory://item",
        "omp://item",
        "pr://item",
        "rule://item",
        "security://item",
        "skill://item",
        "vault://item",
        "xd://item",
        "ordinary.txt",
        "state.sqlite.backup",
    ]
    expected = [
        {"value": value, "route": classify_capability(value, denial_policy=policy)}
        for value in values
    ]
    probe = tmp_path / "omp_route_probe.ts"
    probe.write_text(_TS_ROUTE_PROBE, encoding="utf-8")
    result = subprocess.run(
        [pinned_worker_spec().bun, str(probe)],
        input=json.dumps({"policy": policy, "values": values}),
        capture_output=True,
        text=True,
        cwd=pinned_worker_spec().source_root,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    assert observed == expected

@pytest.mark.skipif(
    not Path(pinned_worker_spec().source_root).is_dir(),
    reason="pinned OMP source is unavailable on this host",
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
