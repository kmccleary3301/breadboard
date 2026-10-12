from __future__ import annotations
from datetime import datetime
import os
from functools import cache
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
import pytest
from breadboard.rl.harness.omp_native_tools import PinnedNativeWorkerSpec, pinned_worker_spec
from typing import Any

from tests.rl.harness.native_dependencies import resolve_omp

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
        # Local noon of 2026-09-23 in this process's (inherited) time zone.
        "message_timestamp_ms": str(int(datetime.fromisoformat("2026-09-23T12:00:00").timestamp() * 1000)),
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


_PINNED_COMPAT_SCRIPT = """
const payload = JSON.parse(await Bun.stdin.text());
const { buildModel } = await import(`${payload.root}/packages/catalog/src/build.ts`);
process.stdout.write(JSON.stringify(payload.providers.map((provider) => {
  const { compat } = buildModel({ ...payload.model, provider, api: "openai-completions", baseUrl: payload.baseUrl });
  return [compat.supportsMultipleSystemMessages, compat.supportsDeveloperRole];
})));
"""


_INSTALLED_SPEC = pinned_worker_spec()


def _sample_conversation_messages() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "Help me refactor the database module."},
        {"role": "assistant", "content": [{"type": "text", "text": "Sure, let's inspect the files first."}, {"type": "toolCall", "id": "tc1", "name": "read", "arguments": {"path": "db.py"}}], "stopReason": "toolUse"},
        {"role": "toolResult", "toolCallId": "tc1", "toolName": "read", "content": [{"type": "text", "text": "def connect(): pass\ndef query(): pass"}], "isError": False},
        {"role": "assistant", "content": [{"type": "text", "text": "I see the functions. Let's add transactions."}], "stopReason": "stop"},
        {"role": "user", "content": "Also add logging."},
    ]


def _run_stock_compaction_oracle(payload: dict[str, Any]) -> dict[str, Any]:
    bun = _differential_bun()
    if bun is None or not bun.is_file():
        pytest.skip("Bun is unavailable for pinned stock compaction")
    source_root = _differential_source_root()
    pkgs_dir = resolve_omp()
    if not pkgs_dir.is_dir() and not (source_root / "node_modules").is_dir():
        pytest.skip("pinned OMP stock dependencies are unavailable")

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

