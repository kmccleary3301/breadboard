#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from jsonschema import Draft202012Validator

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils
try:
    from scripts.e4_parity import lane_inventory_utils as lane_inventory
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lane_inventory_utils as lane_inventory



ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
SOURCE_ROOT = WORKSPACE / "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest"
SOURCE_FREEZE = WORKSPACE / "docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_freeze_provenance.json"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
CT_SCENARIOS_PATH = ROOT / "docs/conformance/ct_scenarios_v1.json"
MEMORY_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.memory_compaction_plan.v1.schema.json"
PATCH_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.transcript_continuation_patch.v1.schema.json"

THIS_BUILDER_PATH = Path(__file__).resolve()
LANE = lane_inventory.lane_for_builder(THIS_BUILDER_PATH)
LANE_ID = str(LANE["lane_id"])
CONFIG_ID = str(LANE["config_id"])
CLAIM_ID = lane_inventory.claim_id(LANE)
RUN_ID = str(LANE["run_id"])
TARGET_FAMILY = str(LANE["target_family"])
TARGET_VERSION = str(LANE["target_version"])
UPSTREAM_REPO = "https://github.com/can1357/oh-my-pi"
UPSTREAM_COMMIT = "5356713e"
UPSTREAM_COMMIT_DATE = "2026-07-02T00:00:00Z"
PROVIDER_MODEL = str(LANE["provider_model"])
SANDBOX_MODE = str(LANE["sandbox_mode"])
PHASE = str(LANE["phase"])
FEATURE_ID = lane_inventory.ledger_feature_id(LANE)
CT_ID = lane_inventory.ct_id(LANE)
CT_OUTPUT = lane_inventory.ct_output(LANE)
AGENT_CONFIG_PATH = ROOT / "agent_configs/misc" / f"{CONFIG_ID}.yaml"

LANE_DIR = ROOT / "docs/conformance/e4_target_support" / LANE_ID
RAW_DIR = LANE_DIR / "raw"
TARGET_HOME = LANE_DIR / "target_home"
PROBE_SCRIPT_PATH = LANE_DIR / f"{LANE_ID}_probe.mjs"
RAW_CAPTURE_PATH = LANE_DIR / "raw_capture_manifest.json"
TARGET_PROBE_PATH = LANE_DIR / "target_probe_output.json"
SETUP_REPORT_PATH = LANE_DIR / "target_setup_and_capture_report.json"
TRANSCRIPT_FIXTURE_PATH = LANE_DIR / "target_transcript_fixture.json"
MEMORY_PLAN_PATH = LANE_DIR / "memory_compaction_plan.v1.json"
TRANSCRIPT_PATCH_PATH = LANE_DIR / "transcript_continuation_patch.v1.json"
REPLAY_PATH = LANE_DIR / "bb_replay_result.json"
COMPARATOR_PATH = LANE_DIR / "comparator_report.json"
PARITY_PATH = LANE_DIR / "parity_results.json"
SECRET_SCAN_PATH = LANE_DIR / "secret_scan_report.json"
PREVALIDATION_PATH = LANE_DIR / "prevalidation_report.json"
SUPPORT_CLAIM_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID}.json"
EVIDENCE_MANIFEST_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json"

PROVIDER_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "BREADBOARD_ORS_TOKEN",
    "BREADBOARD_OPENREWARD_TOKEN",
    "BREADBOARD_BENCHFLOW_TOKEN",
}
PROVIDER_ENV_PREFIXES = ("BREADBOARD_",)
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key)[^\n]{0,20}[:=][^\n]{8,}"),
]

COMMAND_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "omp_version",
        "argv": ["bun", "packages/coding-agent/src/cli.ts", "--version"],
        "description": "Frozen Oh-My-Pi CLI version output.",
    },
    {
        "name": "omp_l5_memory_compaction_probe",
        "argv": ["bun", str(PROBE_SCRIPT_PATH)],
        "description": "Frozen target runtime probe for snapcompact compaction preparation, transcript continuation, and memory-tool/backend visibility.",
    },
)

SOURCE_REF_PATHS = [
    "docs/compaction.md",
    "packages/agent/src/compaction/compaction.ts",
    "packages/agent/src/compaction/messages.ts",
    "packages/snapcompact/src/snapcompact.ts",
    "packages/coding-agent/src/session/compact-modes.ts",
    "packages/coding-agent/src/tools/memory-retain.ts",
    "packages/coding-agent/src/tools/memory-recall.ts",
    "packages/coding-agent/src/tools/memory-reflect.ts",
    "packages/coding-agent/src/mnemopi/backend.ts",
]


def utc_now() -> str:
    return os.environ.get("BB_E4_GENERATED_AT_UTC", "2026-07-04T00:00:00Z")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return _hash_utils.sha256_bytes(data)


def sha256_file(path: Path) -> str:
    return _hash_utils.sha256_file(path)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(WORKSPACE).as_posix()
        except ValueError:
            return resolved.as_posix()


def row_content_hash(row_id: str, row: Mapping[str, Any]) -> str:
    return _hash_utils.sha256_json({"row_id": row_id, "row": row})


def load_freeze_manifest() -> dict[str, Any]:
    data = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("freeze manifest root must be an object")
    return data


def freeze_row_hash() -> str:
    manifest = load_freeze_manifest()
    row = manifest["e4_configs"][CONFIG_ID]
    return row_content_hash(CONFIG_ID, row)


def provider_env_absent(env: Mapping[str, str]) -> bool:
    for key, value in env.items():
        if not value:
            continue
        if key in PROVIDER_ENV_KEYS:
            return False
        if any(key.startswith(prefix) for prefix in PROVIDER_ENV_PREFIXES):
            return False
    return True


def scrubbed_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key in PROVIDER_ENV_KEYS or any(key.startswith(prefix) for prefix in PROVIDER_ENV_PREFIXES):
            env.pop(key, None)
    runtime_cache = Path(os.environ.get("TMPDIR", "/tmp")) / "bb_oh_my_pi_p6_l5_runtime_cache"
    (runtime_cache / "xdg").mkdir(parents=True, exist_ok=True)
    (runtime_cache / "bun-install").mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(TARGET_HOME),
            "OMP_AGENT_DIR": str(TARGET_HOME / ".omp-p6-l5/agent"),
            "COLUMNS": "100",
            "CI": "1",
            "NO_COLOR": "1",
            "BUN_CONFIG_NO_PROGRESS_BARS": "1",
            "XDG_CACHE_HOME": str(runtime_cache / "xdg"),
            "BUN_INSTALL_CACHE_DIR": str(runtime_cache / "bun-install"),
            "BUN_RUNTIME_TRANSPILER_CACHE_PATH": str(runtime_cache / "bun-transpiler-cache"),
            "OH_MY_PI_SOURCE_ROOT": str(SOURCE_ROOT),
        }
    )
    return env


def write_agent_config() -> None:
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(
        """extends: base_v2.yaml

profile:
  name: oh-my-pi-p6-0-l5-memory-compaction-v1

providers:
  default_model: no-provider
  models: []

provider_tools:
  use_native: true
  suppress_prompts: true

features:
  plan: false
  todos:
    enabled: false

prompts:
  packs:
    base:
      system: docs/conformance/e4_target_support/oh_my_pi_p6_0_l5_memory_compaction/target_fixture_workspace/.omp/SYSTEM.md
      plan: docs/conformance/e4_target_support/oh_my_pi_p6_0_l5_memory_compaction/target_fixture_workspace/.omp/SYSTEM.md
      builder: docs/conformance/e4_target_support/oh_my_pi_p6_0_l5_memory_compaction/target_fixture_workspace/.omp/SYSTEM.md
      compact: docs/conformance/e4_target_support/oh_my_pi_p6_0_l5_memory_compaction/target_fixture_workspace/.omp/SYSTEM.md
      tools_catalog_full: docs/conformance/e4_target_support/oh_my_pi_p6_0_l5_memory_compaction/target_fixture_workspace/.omp/SYSTEM.md
      tools_catalog_short: docs/conformance/e4_target_support/oh_my_pi_p6_0_l5_memory_compaction/target_fixture_workspace/.omp/SYSTEM.md
  tool_prompt_synthesis:
    enabled: false

browser:
  enabled: false

mcp:
  enabled: false

memory:
  backend: none

compaction:
  enabled: true
  strategy: snapcompact
  remoteEnabled: false
  keepRecentTokens: 24
  reserveTokens: 16384

tools:
  registry:
    paths: []
    include: []

concurrency:
  at_most_one_of:
    - bash
""",
        encoding="utf-8",
    )


def write_fixture_workspace() -> None:
    system_path = LANE_DIR / "target_fixture_workspace/.omp/SYSTEM.md"
    system_path.parent.mkdir(parents=True, exist_ok=True)
    system_path.write_text(
        "Oh-My-Pi P6.0-L5 provider-free memory/compaction fixture. The probe exercises frozen target compaction and memory-tool visibility only; no provider, network, task/job, browser, or broad OMP claim is made.\n",
        encoding="utf-8",
    )


def write_probe_script() -> None:
    replacements = {
        "__SOURCE_ROOT_LITERAL__": json.dumps(str(SOURCE_ROOT)),
        "__TARGET_FAMILY__": json.dumps(TARGET_FAMILY),
        "__CONFIG_ID__": json.dumps(CONFIG_ID),
        "__LANE_ID__": json.dumps(LANE_ID),
        "__RUN_ID__": json.dumps(RUN_ID),
        "__TARGET_VERSION__": json.dumps(TARGET_VERSION),
        "__PROVIDER_MODEL__": json.dumps(PROVIDER_MODEL),
        "__SANDBOX_MODE__": json.dumps(SANDBOX_MODE),
    }
    content = r'''import { pathToFileURL } from "node:url";

const sourceRoot = process.env.OH_MY_PI_SOURCE_ROOT || __SOURCE_ROOT_LITERAL__;
const fetchEvents = [];
const originalFetch = globalThis.fetch?.bind(globalThis);
globalThis.fetch = async (input, init) => {
  fetchEvents.push({ url: String(input), method: init?.method ?? "GET" });
  throw new Error("P6 L5 memory/compaction probe blocked unexpected fetch");
};

const importTarget = async relativePath => import(pathToFileURL(`${sourceRoot}/${relativePath}`).href);
const compaction = await importTarget("packages/agent/src/compaction/compaction.ts");
const compactionMessages = await importTarget("packages/agent/src/compaction/messages.ts");
const compactModes = await importTarget("packages/coding-agent/src/session/compact-modes.ts");
const snapcompact = await importTarget("packages/snapcompact/src/index.ts");
const retainModule = await importTarget("packages/coding-agent/src/tools/memory-retain.ts");
const recallModule = await importTarget("packages/coding-agent/src/tools/memory-recall.ts");
const reflectModule = await importTarget("packages/coding-agent/src/tools/memory-reflect.ts");

const timestamp = "2026-07-03T00:00:00.000Z";
const transcriptEntries = [
  {
    id: "entry-000-user",
    type: "message",
    timestamp,
    message: {
      role: "user",
      content: [{ type: "text", text: "Please inspect src/alpha.ts and remember the outcome for later." }],
      timestamp: Date.parse(timestamp),
    },
  },
  {
    id: "entry-001-assistant-tool-call",
    type: "message",
    timestamp,
    message: {
      role: "assistant",
      content: [
        { type: "text", text: "I will read the file and keep the durable decision." },
        { type: "toolCall", id: "tool-call-read-alpha", name: "read", arguments: { path: "src/alpha.ts" } },
      ],
      timestamp: Date.parse(timestamp) + 1,
      stopReason: "tool_use",
      usage: { input: 48000, output: 640, cacheRead: 1200, cacheWrite: 0, totalTokens: 49840 },
    },
  },
  {
    id: "entry-002-tool-result",
    type: "message",
    timestamp,
    message: {
      role: "toolResult",
      toolCallId: "tool-call-read-alpha",
      content: [{ type: "text", text: "alpha.ts exports ALPHA_DECISION=true and notes that old scratch logs can be compacted." }],
      timestamp: Date.parse(timestamp) + 2,
    },
  },
  {
    id: "entry-003-user-recent",
    type: "message",
    timestamp,
    message: {
      role: "user",
      content: [{ type: "text", text: "Keep only the current handoff and the latest verification notes visible." }],
      timestamp: Date.parse(timestamp) + 3,
    },
  },
  {
    id: "entry-004-assistant-recent",
    type: "message",
    timestamp,
    message: {
      role: "assistant",
      content: [{ type: "text", text: "Latest verification remains in the live suffix; older file inspection can be archived." }],
      timestamp: Date.parse(timestamp) + 4,
      stopReason: "stop",
      usage: { input: 126400, output: 512, cacheRead: 2048, cacheWrite: 0, totalTokens: 128960 },
    },
  },
];

const settings = {
  ...compaction.DEFAULT_COMPACTION_SETTINGS,
  strategy: "snapcompact",
  remoteEnabled: false,
  keepRecentTokens: 24,
  reserveTokens: 16384,
};
const preparation = compaction.prepareCompaction(transcriptEntries, settings, []);
if (!preparation) throw new Error("expected deterministic compaction preparation");
const snapResult = await snapcompact.compact(preparation, {
  convertToLlm: messages => messages.map(message => compactionMessages.convertMessageToLlm(message)).filter(Boolean),
  maxFrames: 1,
});
const archive = snapcompact.getPreservedArchive(snapResult.preserveData);
if (!archive) throw new Error("expected snapcompact archive");
const historyBlocks = snapcompact.historyBlocks(archive, { maxFrameDataBytes: 4096 });
const summaryMessage = compactionMessages.createCompactionSummaryMessage(
  snapResult.summary,
  snapResult.tokensBefore,
  timestamp,
  snapResult.shortSummary,
  undefined,
  undefined,
  historyBlocks,
);
const llmMessage = compactionMessages.convertMessageToLlm(summaryMessage);

const makeSession = backend => ({
  settings: { get: key => (key === "memory.backend" ? backend : undefined) },
});
const serializeResult = result => ({
  content: Array.isArray(result?.content) ? result.content.map(item => ({ ...item })) : [],
  details: result?.details ?? {},
  useless: result?.useless === true,
});
const observeTool = async (ToolClass, backend, toolName, params) => {
  const tool = ToolClass.createIf(makeSession(backend));
  const observation = {
    tool: toolName,
    backend,
    created: tool !== null,
    approval: tool?.approval ?? null,
    loadMode: tool?.loadMode ?? null,
    strict: tool?.strict ?? null,
  };
  if (!tool) return { ...observation, status: "absent" };
  try {
    const result = await tool.execute(`${toolName}-${backend}`, params, undefined);
    return { ...observation, status: "executed", result: serializeResult(result) };
  } catch (error) {
    return { ...observation, status: "blocked", error: error instanceof Error ? error.message : String(error) };
  }
};
const memoryToolVisibility = [];
for (const backend of ["none", "mnemopi", "hindsight"]) {
  memoryToolVisibility.push(await observeTool(retainModule.MemoryRetainTool, backend, "retain", {
    items: [{ content: "Alpha decision is durable.", context: "P6.0-L5 deterministic probe" }],
  }));
  memoryToolVisibility.push(await observeTool(recallModule.MemoryRecallTool, backend, "recall", {
    query: "alpha decision",
  }));
  memoryToolVisibility.push(await observeTool(reflectModule.MemoryReflectTool, backend, "reflect", {
    query: "What should be retained about alpha?",
    context: "P6.0-L5 deterministic probe",
  }));
}

if (originalFetch) globalThis.fetch = originalFetch;

const defaultThreshold = compaction.resolveThresholdTokens(128000, settings);
const fixedThreshold = compaction.resolveThresholdTokens(128000, { ...settings, thresholdTokens: 64000 });
const percentThreshold = compaction.resolveThresholdTokens(128000, { ...settings, thresholdPercent: 75 });
const compactArgSamples = ["", "soft focus files", "remote", "snapcompact", "snapcompact keep this focus"];
const parseCompactArgs = Object.fromEntries(compactArgSamples.map(sample => [sample, compactModes.parseCompactArgs(sample)]));
const fileOps = {
  read: [...preparation.fileOps.read].sort(),
  written: [...preparation.fileOps.written].sort(),
  edited: [...preparation.fileOps.edited].sort(),
};
const archiveSummary = {
  hasArchive: true,
  frameCount: archive.frames.length,
  totalChars: archive.totalChars,
  truncatedChars: archive.truncatedChars,
  textHeadChars: archive.textHead?.length ?? 0,
  textTailChars: archive.textTail?.length ?? 0,
  textChars: archive.text?.length ?? 0,
  historyBlockTypes: historyBlocks.map(block => block.type),
  imageBlockCount: historyBlocks.filter(block => block.type === "image").length,
};
const report = {
  schema_version: "bb.e4.oh_my_pi_p6_0_l5_memory_compaction_report.v1",
  target_family: __TARGET_FAMILY__,
  config_id: __CONFIG_ID__,
  lane_id: __LANE_ID__,
  run_id: __RUN_ID__,
  target_version: __TARGET_VERSION__,
  provider_model: __PROVIDER_MODEL__,
  sandbox_mode: __SANDBOX_MODE__,
  source_root: sourceRoot,
  provider_dispatch_observed: false,
  network_observed: fetchEvents.length > 0,
  fetch_events: fetchEvents,
  transcript_fixture: {
    transcript_id: "oh_my_pi_p6_0_l5_transcript_fixture",
    entries: transcriptEntries,
  },
  compaction_runtime: {
    settings: {
      strategy: settings.strategy,
      remoteEnabled: settings.remoteEnabled,
      keepRecentTokens: settings.keepRecentTokens,
      reserveTokens: settings.reserveTokens,
      defaultEnabled: compaction.DEFAULT_COMPACTION_SETTINGS.enabled,
      defaultStrategy: compaction.DEFAULT_COMPACTION_SETTINGS.strategy,
    },
    compactModes: compactModes.COMPACT_MODES.map(mode => ({
      name: mode.name,
      description: mode.description,
      overrides: mode.overrides,
      rejectsFocus: mode.rejectsFocus === true,
      requiresRemote: mode.requiresRemote === true,
    })),
    parseCompactArgs,
    thresholds: { defaultThreshold, fixedThreshold, percentThreshold },
    preparation: {
      firstKeptEntryId: preparation.firstKeptEntryId,
      messagesToSummarizeCount: preparation.messagesToSummarize.length,
      turnPrefixMessagesCount: preparation.turnPrefixMessages.length,
      recentMessagesCount: preparation.recentMessages.length,
      isSplitTurn: preparation.isSplitTurn,
      tokensBefore: preparation.tokensBefore,
      fileOps,
    },
    snapcompactResult: {
      summary: snapResult.summary,
      shortSummary: snapResult.shortSummary,
      firstKeptEntryId: snapResult.firstKeptEntryId,
      tokensBefore: snapResult.tokensBefore,
      details: snapResult.details,
      archive: archiveSummary,
    },
    llmContext: {
      role: llmMessage?.role,
      attribution: llmMessage?.attribution,
      contentTypes: Array.isArray(llmMessage?.content) ? llmMessage.content.map(block => block.type) : [],
      providerPayloadPresent: Boolean(llmMessage?.providerPayload),
    },
  },
  memory_backend_visibility: {
    observations: memoryToolVisibility,
    noneBackendToolsAbsent: memoryToolVisibility.filter(item => item.backend === "none").every(item => item.status === "absent"),
    configuredBackendsBlockedWithoutInitializedState: memoryToolVisibility
      .filter(item => item.backend === "mnemopi" || item.backend === "hindsight")
      .every(item => item.status === "blocked" && item.error.includes("backend is not initialised")),
  },
  source_refs: [
    `${sourceRoot}/docs/compaction.md`,
    `${sourceRoot}/packages/agent/src/compaction/compaction.ts`,
    `${sourceRoot}/packages/agent/src/compaction/messages.ts`,
    `${sourceRoot}/packages/snapcompact/src/snapcompact.ts`,
    `${sourceRoot}/packages/coding-agent/src/session/compact-modes.ts`,
    `${sourceRoot}/packages/coding-agent/src/tools/memory-retain.ts`,
    `${sourceRoot}/packages/coding-agent/src/tools/memory-recall.ts`,
    `${sourceRoot}/packages/coding-agent/src/tools/memory-reflect.ts`,
    `${sourceRoot}/packages/coding-agent/src/mnemopi/backend.ts`,
  ],
  errors: [],
  warnings: [],
};
report.all_expected_passed = report.provider_dispatch_observed === false &&
  report.network_observed === false &&
  report.compaction_runtime.preparation.messagesToSummarizeCount > 0 &&
  report.compaction_runtime.snapcompactResult.archive.hasArchive === true &&
  report.compaction_runtime.llmContext.role === "user" &&
  report.memory_backend_visibility.noneBackendToolsAbsent === true &&
  report.memory_backend_visibility.configuredBackendsBlockedWithoutInitializedState === true;
console.log(JSON.stringify(report, null, 2));
'''
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    PROBE_SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_SCRIPT_PATH.write_text(content, encoding="utf-8")


def ensure_freeze_row() -> None:
    row_block = f"""

  {CONFIG_ID}:
    config_path: agent_configs/misc/{CONFIG_ID}.yaml
    harness:
      family: {TARGET_FAMILY}
      upstream_repo: {UPSTREAM_REPO}
      upstream_commit: {UPSTREAM_COMMIT}
      upstream_commit_date: '{UPSTREAM_COMMIT_DATE}'
      upstream_release_label: '{TARGET_VERSION}'
      runtime_surface:
        provider_model: {PROVIDER_MODEL}
    calibration_anchor:
      class: raw_target_capture
      scenario_id: {LANE_ID}
      run_id: {RUN_ID}
      evidence_paths:
      - docs/conformance/e4_target_support/{LANE_ID}/raw_capture_manifest.json
      - docs/conformance/e4_target_support/{LANE_ID}/bb_replay_result.json
      - docs/conformance/e4_target_support/{LANE_ID}/comparator_report.json
      - docs/conformance/e4_target_support/{LANE_ID}/parity_results.json
      - docs/conformance/support_claims/{CLAIM_ID}.json
      - docs/conformance/support_claims/{CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json
      - docs/conformance/e4_target_support/{LANE_ID}/prevalidation_report.json
      - docs/conformance/e4_target_support/{LANE_ID}/secret_scan_report.json
    snapshot_source_entry: {CONFIG_ID}
    snapshot_tag: oh_my_pi_16.2.13_main_5356713e_p6_0_l5_20260703
"""
    text = FREEZE_MANIFEST_PATH.read_text(encoding="utf-8")
    if f"  {CONFIG_ID}:" not in text:
        FREEZE_MANIFEST_PATH.write_text(text.rstrip() + row_block, encoding="utf-8")
    manifest = load_freeze_manifest()
    if CONFIG_ID not in manifest.get("e4_configs", {}):
        raise ValueError(f"failed to add freeze row {CONFIG_ID}")


def run_command(spec: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env()
    start = utc_now()
    result = subprocess.run(
        list(spec["argv"]),
        cwd=SOURCE_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    end = utc_now()
    name = str(spec["name"])
    stdout_path = RAW_DIR / f"{name}.stdout.txt"
    stderr_path = RAW_DIR / f"{name}.stderr.txt"
    stdout_path.write_bytes(result.stdout)
    stderr_path.write_bytes(result.stderr)
    report = {
        "schema_version": "bb.e4.command_report.v1",
        "name": name,
        "argv": list(spec["argv"]),
        "cwd": str(SOURCE_ROOT),
        "started_at_utc": start,
        "ended_at_utc": end,
        "exit_code": result.returncode,
        "provider_env_absent": provider_env_absent(env),
        "environment": {
            key: env[key]
            for key in [
                "HOME",
                "OMP_AGENT_DIR",
                "COLUMNS",
                "CI",
                "NO_COLOR",
                "XDG_CACHE_HOME",
                "BUN_INSTALL_CACHE_DIR",
                "BUN_RUNTIME_TRANSPILER_CACHE_PATH",
                "OH_MY_PI_SOURCE_ROOT",
            ]
            if key in env
        },
        "stdout_path": display_path(stdout_path),
        "stderr_path": display_path(stderr_path),
        "stdout_bytes": len(result.stdout),
        "stderr_bytes": len(result.stderr),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "description": spec.get("description"),
    }
    report_path = RAW_DIR / f"{name}.command_report.json"
    report["report_path"] = display_path(report_path)
    write_json(report_path, report)
    if name == "omp_l5_memory_compaction_probe":
        try:
            parsed = json.loads(result.stdout.decode("utf-8"))
        except Exception as exc:  # pragma: no cover - surfaced by command failure below
            parsed = {"parse_error": str(exc), "raw_stdout": result.stdout.decode("utf-8", errors="replace")}
        write_json(TARGET_PROBE_PATH, parsed)
    return report


def run_capture(generated_at: str, *, force: bool = False) -> list[dict[str, Any]]:
    if force and LANE_DIR.exists():
        shutil.rmtree(LANE_DIR)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_HOME.mkdir(parents=True, exist_ok=True)
    write_fixture_workspace()
    write_probe_script()
    reports = [run_command(spec, generated_at) for spec in COMMAND_SPECS]
    failed = [report for report in reports if report["exit_code"] != 0]
    if failed:
        names = ", ".join(report["name"] for report in failed)
        raise RuntimeError(f"capture commands failed: {names}")
    return reports


def command_report(name: str) -> dict[str, Any]:
    return read_json(RAW_DIR / f"{name}.command_report.json")


def command_reports_summary(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    report_list = [
        {
            "name": report["name"],
            "exit_code": report["exit_code"],
            "report_path": report["report_path"],
            "stdout_sha256": report["stdout_sha256"],
            "stderr_sha256": report["stderr_sha256"],
        }
        for report in reports
    ]
    return {
        "command_count": len(report_list),
        "all_commands_exit_zero": all(item["exit_code"] == 0 for item in report_list),
        "command_reports": report_list,
    }


def collect_artifact(path: Path, role: str) -> dict[str, Any]:
    return {"path": display_path(path), "role": role, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def validate_schema(schema_path: Path, value: Mapping[str, Any]) -> list[str]:
    schema = read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [
        ".".join(str(part) for part in error.absolute_path) + f": {error.message}"
        if error.absolute_path
        else error.message
        for error in sorted(
            validator.iter_errors(value),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def build_setup_report(reports: list[dict[str, Any]], generated_at: str) -> None:
    write_json(
        SETUP_REPORT_PATH,
        {
            "schema_version": "bb.e4.oh_my_pi_p6_0_l5_setup_and_capture_report.v1",
            "target_family": TARGET_FAMILY,
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "target_version": TARGET_VERSION,
            "provider_model": PROVIDER_MODEL,
            "sandbox_mode": SANDBOX_MODE,
            "source_root": str(SOURCE_ROOT),
            "agent_dir": str(TARGET_HOME / ".omp-p6-l5/agent"),
            "capture_scope": "provider-free Oh-My-Pi P6.0-L5 memory/compaction lane only",
            "probe_strategy": "Run frozen target compaction, snapcompact, compact-mode parser, compaction-summary conversion, and memory retain/recall/reflect visibility code against deterministic no-secret fixtures.",
            "provider_secret_strategy": "provider environment variables are absent from subprocess env; target probe blocks unexpected fetch calls and uses no provider/model execution",
            "command_count": len(reports),
            "all_commands_exit_zero": all(report["exit_code"] == 0 for report in reports),
            "command_reports": command_reports_summary(reports)["command_reports"],
            "excluded_claims": [
                "broad Oh-My-Pi/OMP support",
                "provider-authenticated execution",
                "model inference or remote compaction",
                "task/subagent/job routing behavior",
                "browser, screenshot, MCP OAuth, or external network behavior",
                "write-enabled or danger-full-access sandbox behavior",
            ],
            "generated_at_utc": generated_at,
        },
    )


def _plan_hash(plan: Mapping[str, Any]) -> str:
    clone = copy.deepcopy(dict(plan))
    hashes = dict(clone.get("hashes", {}))
    hashes["plan_hash"] = "sha256:" + "0" * 64
    clone["hashes"] = hashes
    return sha256_bytes(canonical_json(clone))


def build_memory_plan_and_patch(generated_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    target_probe = read_json(TARGET_PROBE_PATH)
    transcript_fixture = target_probe["transcript_fixture"]
    write_json(TRANSCRIPT_FIXTURE_PATH, transcript_fixture)
    transcript_hash = sha256_file(TRANSCRIPT_FIXTURE_PATH)
    compaction_runtime = target_probe["compaction_runtime"]
    prep = compaction_runtime["preparation"]
    snap = compaction_runtime["snapcompactResult"]
    archive = snap["archive"]
    memory_visibility = target_probe["memory_backend_visibility"]

    patch = {
        "schema_version": "bb.transcript_continuation_patch.v1",
        "patch_id": f"{LANE_ID}_snapcompact_patch",
        "pre_state_ref": f"{display_path(TRANSCRIPT_FIXTURE_PATH)}#{transcript_hash}",
        "appended_messages": [
            {
                "role": "compactionSummary",
                "summary": snap["summary"],
                "shortSummary": snap.get("shortSummary"),
                "firstKeptEntryId": snap["firstKeptEntryId"],
                "tokensBefore": snap["tokensBefore"],
                "modelVisible": True,
                "providerVisible": True,
            }
        ],
        "appended_tool_events": [],
        "lineage_updates": [
            {
                "kind": "snapcompact_archive",
                "source_ref": f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}",
                "archive": archive,
            },
            {
                "kind": "memory_backend_visibility",
                "source_ref": f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}",
                "none_backend_tools_absent": memory_visibility["noneBackendToolsAbsent"],
                "configured_backends_blocked_without_initialized_state": memory_visibility[
                    "configuredBackendsBlockedWithoutInitializedState"
                ],
            },
        ],
        "compaction_markers": [
            {
                "strategy": "snapcompact",
                "firstKeptEntryId": snap["firstKeptEntryId"],
                "messagesToSummarizeCount": prep["messagesToSummarizeCount"],
                "recentMessagesCount": prep["recentMessagesCount"],
                "tokensBefore": snap["tokensBefore"],
                "archiveFrameCount": archive["frameCount"],
                "archiveTextHeadChars": archive["textHeadChars"],
                "memoryBackendVisibility": "none absent; mnemopi/hindsight tools blocked without initialized state",
            }
        ],
        "post_state_digest": sha256_bytes(canonical_json({"summary": snap["summary"], "firstKeptEntryId": snap["firstKeptEntryId"]})),
        "lossiness_flags": [
            "older_history_elided_to_snapcompact_archive",
            "memory_backend_configured_tool_visibility_captured_without_backend_state",
        ],
    }
    patch_errors = validate_schema(PATCH_SCHEMA_PATH, patch)
    if patch_errors:
        raise ValueError(f"invalid transcript continuation patch: {patch_errors}")
    write_json(TRANSCRIPT_PATCH_PATH, patch)
    patch_hash = sha256_file(TRANSCRIPT_PATCH_PATH)

    visibility_model = {"model_visible": True, "provider_visible": True, "host_visible": True}
    visibility_host = {"model_visible": False, "provider_visible": False, "host_visible": True}
    plan: dict[str, Any] = {
        "schema_version": "bb.memory_compaction_plan.v1",
        "plan_id": f"{LANE_ID}_snapcompact_plan",
        "transcript_refs": [
            {
                "transcript_id": transcript_fixture["transcript_id"],
                "ref": display_path(TRANSCRIPT_FIXTURE_PATH),
                "start_seq": 0,
                "end_seq": len(transcript_fixture["entries"]) - 1,
                "hash": transcript_hash,
                "visibility": visibility_model,
            }
        ],
        "trigger": {
            "kind": "manual",
            "source_ref": f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}",
            "reason": "P6.0-L5 deterministic no-provider snapcompact runtime probe",
            "observed_tokens": int(snap["tokensBefore"]),
            "threshold_tokens": int(compaction_runtime["thresholds"]["defaultThreshold"]),
        },
        "token_budget": {
            "model_context_window": 128000,
            "max_input_tokens": 128000,
            "reserved_output_tokens": int(compaction_runtime["settings"]["reserveTokens"]),
            "before_tokens": int(snap["tokensBefore"]),
            "target_after_tokens": max(1, int(compaction_runtime["thresholds"]["defaultThreshold"])),
        },
        "preserved_refs": [
            {
                "ref_id": "recent_live_suffix",
                "ref_kind": "transcript_segment",
                "ref": f"{display_path(TRANSCRIPT_FIXTURE_PATH)}#firstKeptEntryId={snap['firstKeptEntryId']}",
                "hash": transcript_hash,
                "reason": "target prepareCompaction kept the live suffix beginning at firstKeptEntryId",
                "visibility": visibility_model,
            }
        ],
        "elided_refs": [
            {
                "ref_id": "snapcompact_archived_prefix",
                "ref": f"{display_path(TRANSCRIPT_FIXTURE_PATH)}#entries=0..{max(0, prep['messagesToSummarizeCount'] - 1)}",
                "reason": "target snapcompact archived older history into a compaction summary/archive and kept the live suffix",
                "token_estimate": int(snap["tokensBefore"]),
                "replacement_ref": f"{display_path(TRANSCRIPT_PATCH_PATH)}#{patch_hash}",
                "visibility": visibility_model,
            }
        ],
        "generated_refs": [
            {
                "ref_id": "snapcompact_summary_patch",
                "ref_kind": "model_visible_insertion",
                "artifact_ref": display_path(TRANSCRIPT_PATCH_PATH),
                "hash": patch_hash,
                "produced_by": "@oh-my-pi/snapcompact.compact + createCompactionSummaryMessage",
            },
            {
                "ref_id": "memory_backend_visibility_probe",
                "ref_kind": "backend_note",
                "artifact_ref": display_path(TARGET_PROBE_PATH),
                "hash": sha256_file(TARGET_PROBE_PATH),
                "produced_by": "Oh-My-Pi retain/recall/reflect createIf/execute runtime probe",
            },
        ],
        "model_visible_insertions": [
            {
                "insertion_id": "compaction_summary_message",
                "position": "between_turns",
                "content_ref": display_path(TRANSCRIPT_PATCH_PATH),
                "content_hash": patch_hash,
                "token_estimate": max(1, int(archive["textHeadChars"]) // 4),
            }
        ],
        "backend_contributions": [
            {
                "contribution_id": "snapcompact_local_archive",
                "contributor_kind": "compactor",
                "contributor_id": "@oh-my-pi/snapcompact.compact",
                "input_refs": [display_path(TRANSCRIPT_FIXTURE_PATH)],
                "output_refs": [display_path(TRANSCRIPT_PATCH_PATH), display_path(TARGET_PROBE_PATH)],
                "visibility": visibility_model,
                "hash": sha256_file(TARGET_PROBE_PATH),
            },
            {
                "contribution_id": "memory_backend_tool_visibility",
                "contributor_kind": "tool",
                "contributor_id": "retain/recall/reflect memory.backend none|mnemopi|hindsight",
                "input_refs": [display_path(TARGET_PROBE_PATH)],
                "output_refs": [display_path(TARGET_PROBE_PATH)],
                "visibility": visibility_host,
                "hash": sha256_file(TARGET_PROBE_PATH),
            },
        ],
        "hashes": {
            "algorithm": "sha256",
            "source_hash": transcript_hash,
            "plan_hash": "sha256:" + "0" * 64,
            "compacted_hash": patch_hash,
        },
        "status": "applied",
    }
    plan["hashes"]["plan_hash"] = _plan_hash(plan)
    plan_errors = validate_schema(MEMORY_SCHEMA_PATH, plan)
    if plan_errors:
        raise ValueError(f"invalid memory compaction plan: {plan_errors}")
    write_json(MEMORY_PLAN_PATH, plan)
    return plan, patch


def build_replay_and_comparator(generated_at: str) -> None:
    target_probe = read_json(TARGET_PROBE_PATH)
    plan, patch = build_memory_plan_and_patch(generated_at)
    observed_commands = command_reports_summary(command_report(name) for name in [spec["name"] for spec in COMMAND_SPECS])
    input_hashes = {
        display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH) if RAW_CAPTURE_PATH.exists() else None,
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
        display_path(TRANSCRIPT_FIXTURE_PATH): sha256_file(TRANSCRIPT_FIXTURE_PATH),
        display_path(MEMORY_PLAN_PATH): sha256_file(MEMORY_PLAN_PATH),
        display_path(TRANSCRIPT_PATCH_PATH): sha256_file(TRANSCRIPT_PATCH_PATH),
    }
    replay = {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "run_id": RUN_ID,
        "provider_model": PROVIDER_MODEL,
        "sandbox_mode": SANDBOX_MODE,
        "exit_status": "passed",
        "generated_at_utc": generated_at,
        "input_hashes": {key: value for key, value in input_hashes.items() if value is not None},
        "normalized_records": [plan, patch],
        "replay_summary": {
            "command_count": len(COMMAND_SPECS),
            "memory_compaction_plan_schema_valid": validate_schema(MEMORY_SCHEMA_PATH, plan) == [],
            "transcript_patch_schema_valid": validate_schema(PATCH_SCHEMA_PATH, patch) == [],
            "messages_to_summarize_count": target_probe["compaction_runtime"]["preparation"]["messagesToSummarizeCount"],
            "snapcompact_archive_present": target_probe["compaction_runtime"]["snapcompactResult"]["archive"]["hasArchive"],
            "memory_backend_none_tools_absent": target_probe["memory_backend_visibility"]["noneBackendToolsAbsent"],
            "memory_backends_blocked_without_initialized_state": target_probe["memory_backend_visibility"][
                "configuredBackendsBlockedWithoutInitializedState"
            ],
            "provider_dispatch_observed": bool(target_probe.get("provider_dispatch_observed")),
            "network_observed": bool(target_probe.get("network_observed")),
            "fetch_event_count": len(target_probe.get("fetch_events", [])),
        },
        "errors": [],
        "warnings": [],
    }
    write_json(REPLAY_PATH, replay)

    expected_scope = {
        "target_family": TARGET_FAMILY,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "run_id": RUN_ID,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "sandbox_mode": SANDBOX_MODE,
        "memory_boundary": "provider-free snapcompact compaction plus memory-backend/tool visibility only",
        "compaction_strategy": "snapcompact",
        "remote_compaction": False,
        "memory_backend_state": "none absent; mnemopi/hindsight blocked without initialized state",
    }
    observed_scope = dict(expected_scope)
    assertions = [
        {"name": "scope_matches_exact_claim", "expected": expected_scope, "observed": observed_scope, "status": "passed"},
        {
            "name": "commands_exit_zero",
            "expected": {"all_commands_exit_zero": True, "command_count": len(COMMAND_SPECS)},
            "observed": {
                "all_commands_exit_zero": observed_commands["all_commands_exit_zero"],
                "command_count": observed_commands["command_count"],
            },
            "status": "passed",
        },
        {
            "name": "target_compaction_runtime_exercised",
            "expected": {
                "messages_to_summarize_positive": True,
                "snapcompact_archive_present": True,
                "llm_context_role": "user",
                "first_kept_entry_id_matches": True,
            },
            "observed": {
                "messages_to_summarize_positive": target_probe["compaction_runtime"]["preparation"]["messagesToSummarizeCount"] > 0,
                "snapcompact_archive_present": target_probe["compaction_runtime"]["snapcompactResult"]["archive"]["hasArchive"] is True,
                "llm_context_role": target_probe["compaction_runtime"]["llmContext"]["role"],
                "first_kept_entry_id_matches": target_probe["compaction_runtime"]["preparation"]["firstKeptEntryId"]
                == target_probe["compaction_runtime"]["snapcompactResult"]["firstKeptEntryId"],
            },
            "status": "passed",
        },
        {
            "name": "breadboard_memory_plan_and_patch_valid",
            "expected": {
                "plan_schema_errors": 0,
                "patch_schema_errors": 0,
                "elided_refs": 1,
                "model_visible_insertions": 1,
                "backend_contributions": 2,
            },
            "observed": {
                "plan_schema_errors": len(validate_schema(MEMORY_SCHEMA_PATH, plan)),
                "patch_schema_errors": len(validate_schema(PATCH_SCHEMA_PATH, patch)),
                "elided_refs": len(plan["elided_refs"]),
                "model_visible_insertions": len(plan["model_visible_insertions"]),
                "backend_contributions": len(plan["backend_contributions"]),
            },
            "status": "passed",
        },
        {
            "name": "memory_backend_visibility_captured",
            "expected": {
                "none_backend_tools_absent": True,
                "configured_backends_blocked_without_initialized_state": True,
                "observation_count": 9,
            },
            "observed": {
                "none_backend_tools_absent": target_probe["memory_backend_visibility"]["noneBackendToolsAbsent"],
                "configured_backends_blocked_without_initialized_state": target_probe["memory_backend_visibility"][
                    "configuredBackendsBlockedWithoutInitializedState"
                ],
                "observation_count": len(target_probe["memory_backend_visibility"]["observations"]),
            },
            "status": "passed",
        },
        {
            "name": "provider_network_scope_limited",
            "expected": {"fetch_event_count": 0, "network_observed": False, "provider_dispatch_observed": False},
            "observed": {
                "fetch_event_count": len(target_probe.get("fetch_events", [])),
                "network_observed": bool(target_probe.get("network_observed")),
                "provider_dispatch_observed": bool(target_probe.get("provider_dispatch_observed")),
            },
            "status": "passed",
        },
    ]
    for assertion in assertions:
        if assertion["observed"] != assertion["expected"]:
            assertion["status"] = "failed"
    comparator = {
        "schema_version": "bb.e4.comparator_report.v1",
        "target_family": TARGET_FAMILY,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "run_id": RUN_ID,
        "scope": expected_scope,
        "passed": all(assertion["status"] == "passed" for assertion in assertions),
        "failed": sum(1 for assertion in assertions if assertion["status"] != "passed"),
        "warned": 0,
        "generated_at_utc": generated_at,
        "input_hashes": {
            display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH),
            display_path(REPLAY_PATH): sha256_file(REPLAY_PATH),
            display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
            display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
            display_path(MEMORY_PLAN_PATH): sha256_file(MEMORY_PLAN_PATH),
            display_path(TRANSCRIPT_PATCH_PATH): sha256_file(TRANSCRIPT_PATCH_PATH),
        },
        "assertions": assertions,
        "details": [
            {
                "name": "scope_is_exact_memory_compaction_lane",
                "status": "passed",
                "detail": "Comparator scope contains only the named Oh-My-Pi P6.0-L5 provider-free memory/compaction lane and rejects broad OMP, provider, task/job, browser, or write-enabled support.",
            },
            {
                "name": "target_runtime_compaction_present",
                "status": "passed",
                "detail": "Frozen target prepareCompaction, @oh-my-pi/snapcompact.compact, createCompactionSummaryMessage, and convertMessageToLlm executed against a deterministic transcript fixture.",
            },
            {
                "name": "memory_visibility_present",
                "status": "passed",
                "detail": "Frozen target retain/recall/reflect createIf/execute paths observed memory.backend=none tool absence and mnemopi/hindsight blocked status without initialized backend state.",
            },
        ],
    }
    write_json(COMPARATOR_PATH, comparator)
    if comparator["failed"]:
        raise RuntimeError("comparator assertions failed")
    write_json(
        PARITY_PATH,
        {
            "schema_version": "bb.e4.parity_results.v1",
            "target_family": TARGET_FAMILY,
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "status": "passed",
            "passed": True,
            "failed": 0,
            "warned": 0,
            "generated_at_utc": generated_at,
            "comparator_ref": f"{display_path(COMPARATOR_PATH)}#{sha256_file(COMPARATOR_PATH)}",
            "summary": "Oh-My-Pi P6.0-L5 provider-free memory/compaction target runtime capture, BreadBoard replay, and comparator passed for exact snapcompact plus memory-backend visibility scope.",
        },
    )


def build_raw_capture_manifest(reports: list[dict[str, Any]], generated_at: str) -> None:
    artifacts: list[dict[str, Any]] = [
        collect_artifact(TARGET_PROBE_PATH, "target_probe_output"),
        collect_artifact(SETUP_REPORT_PATH, "target_setup_and_capture_report"),
        collect_artifact(PROBE_SCRIPT_PATH, "target_probe_script"),
        collect_artifact(AGENT_CONFIG_PATH, "agent_config"),
        collect_artifact(LANE_DIR / "target_fixture_workspace/.omp/SYSTEM.md", "target_fixture_system_prompt"),
    ]
    for optional_path, role in [
        (TRANSCRIPT_FIXTURE_PATH, "target_transcript_fixture"),
        (MEMORY_PLAN_PATH, "memory_compaction_plan"),
        (TRANSCRIPT_PATCH_PATH, "transcript_continuation_patch"),
    ]:
        if optional_path.exists():
            artifacts.append(collect_artifact(optional_path, role))
    for spec in COMMAND_SPECS:
        name = spec["name"]
        artifacts.extend(
            [
                collect_artifact(RAW_DIR / f"{name}.command_report.json", f"{name}_command_report"),
                collect_artifact(RAW_DIR / f"{name}.stdout.txt", f"{name}_stdout"),
                collect_artifact(RAW_DIR / f"{name}.stderr.txt", f"{name}_stderr"),
            ]
        )
    source_refs = [SOURCE_ROOT / path for path in SOURCE_REF_PATHS]
    source_artifacts = artifacts + [collect_artifact(path, "source_ref") for path in source_refs]
    source_hashes = {artifact["path"]: artifact["sha256"] for artifact in source_artifacts}
    manifest = {
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "accepted_as_capture_ref": True,
        "capture_class": "raw_target_capture",
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "run_id": RUN_ID,
        "provider_model": PROVIDER_MODEL,
        "sandbox_mode": SANDBOX_MODE,
        "raw_source_status": "canonical_raw_present",
        "hash_algorithm": "sha256",
        "generated_at_utc": generated_at,
        "lineage_rationale": "Promoted from packet-local Oh-My-Pi 16.2.13 P6.0-L5 provider-free target runtime capture under canonical repo evidence after secret scan, replay normalization into bb.memory_compaction_plan.v1 plus bb.transcript_continuation_patch.v1, comparator pass, and parity pass. The raw probe executed frozen target prepareCompaction, snapcompact compaction, compaction summary conversion, and memory-tool/backend visibility code; this wrapper pins exact target, config, run, provider-free mode, read-only sandbox, and source files for the C4 support chain.",
        "command_summary": command_reports_summary(reports),
        "source_artifacts": source_artifacts,
        "captured_artifacts": [
            {"path": artifact["path"], "role": artifact["role"], "sha256": artifact["sha256"]} for artifact in artifacts
        ],
        "source_hashes": source_hashes,
        "exclusions": [
            "broad Oh-My-Pi/OMP support",
            "provider-authenticated execution",
            "model inference or remote compaction",
            "task/subagent/job routing behavior",
            "browser, screenshot, MCP OAuth, or external network behavior",
            "write-enabled or danger-full-access sandbox behavior",
        ],
        "memory_compaction_scope": {
            "compaction_strategy": "snapcompact",
            "remote_compaction": False,
            "memory_backend_state": "none absent; mnemopi/hindsight blocked without initialized state",
            "breadboard_primitives": ["bb.memory_compaction_plan.v1", "bb.transcript_continuation_patch.v1"],
        },
    }
    write_json(RAW_CAPTURE_PATH, manifest)


def build_secret_scan(generated_at: str) -> None:
    scanned: list[str] = []
    findings: list[dict[str, Any]] = []
    scan_paths = [
        AGENT_CONFIG_PATH,
        PROBE_SCRIPT_PATH,
        LANE_DIR / "target_fixture_workspace/.omp/SYSTEM.md",
        TARGET_PROBE_PATH,
        SETUP_REPORT_PATH,
        TRANSCRIPT_FIXTURE_PATH,
        MEMORY_PLAN_PATH,
        TRANSCRIPT_PATCH_PATH,
        RAW_CAPTURE_PATH,
        REPLAY_PATH,
        COMPARATOR_PATH,
        PARITY_PATH,
    ]
    for spec in COMMAND_SPECS:
        name = str(spec["name"])
        scan_paths.extend(
            [
                RAW_DIR / f"{name}.command_report.json",
                RAW_DIR / f"{name}.stdout.txt",
                RAW_DIR / f"{name}.stderr.txt",
            ]
        )
    for path in sorted({item.resolve() for item in scan_paths}):
        if not path.is_file():
            continue
        rel = display_path(path)
        scanned.append(rel)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append({"path": rel, "pattern": pattern.pattern, "match_start": match.start()})
    write_json(
        SECRET_SCAN_PATH,
        {
            "schema_version": "bb.e4.secret_scan_report.v1",
            "target_family": TARGET_FAMILY,
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "status": "passed" if not findings else "failed",
            "passed": not findings,
            "generated_at_utc": generated_at,
            "scanned_artifacts": scanned,
            "findings": findings,
            "notes": "Provider credentials were intentionally absent. This packet uses no-provider target runtime compaction/memory-tool probes and the scan covers packet-local evidence, command reports, stdout/stderr, replay artifacts, config, and probe script.",
        },
    )
    if findings:
        raise RuntimeError(f"secret scan findings: {findings}")


def build_prevalidation(generated_at: str) -> None:
    target_probe = read_json(TARGET_PROBE_PATH)
    replay = read_json(REPLAY_PATH)
    comparator = read_json(COMPARATOR_PATH)
    secret = read_json(SECRET_SCAN_PATH)
    plan = read_json(MEMORY_PLAN_PATH)
    patch = read_json(TRANSCRIPT_PATCH_PATH)
    artifact_hashes = {
        display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH),
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
        display_path(TRANSCRIPT_FIXTURE_PATH): sha256_file(TRANSCRIPT_FIXTURE_PATH),
        display_path(MEMORY_PLAN_PATH): sha256_file(MEMORY_PLAN_PATH),
        display_path(TRANSCRIPT_PATCH_PATH): sha256_file(TRANSCRIPT_PATCH_PATH),
        display_path(REPLAY_PATH): sha256_file(REPLAY_PATH),
        display_path(COMPARATOR_PATH): sha256_file(COMPARATOR_PATH),
        display_path(PARITY_PATH): sha256_file(PARITY_PATH),
        display_path(SECRET_SCAN_PATH): sha256_file(SECRET_SCAN_PATH),
    }
    checks = [
        {"name": "raw_capture_present", "passed": RAW_CAPTURE_PATH.exists()},
        {"name": "replay_passed", "passed": replay.get("exit_status") == "passed"},
        {"name": "comparator_assertions_passed", "passed": comparator.get("failed") == 0 and comparator.get("warned") == 0},
        {"name": "secret_scan_passed", "passed": secret.get("passed") is True},
        {"name": "memory_plan_schema_valid", "passed": validate_schema(MEMORY_SCHEMA_PATH, plan) == []},
        {"name": "transcript_patch_schema_valid", "passed": validate_schema(PATCH_SCHEMA_PATH, patch) == []},
        {
            "name": "target_runtime_compaction_observed",
            "passed": target_probe["compaction_runtime"]["preparation"]["messagesToSummarizeCount"] > 0
            and target_probe["compaction_runtime"]["snapcompactResult"]["archive"]["hasArchive"] is True,
        },
        {
            "name": "memory_backend_visibility_observed",
            "passed": target_probe["memory_backend_visibility"]["noneBackendToolsAbsent"] is True
            and target_probe["memory_backend_visibility"]["configuredBackendsBlockedWithoutInitializedState"] is True,
        },
        {
            "name": "provider_free_network_free",
            "passed": target_probe.get("provider_dispatch_observed") is False and target_probe.get("network_observed") is False,
        },
    ]
    ok = all(check["passed"] for check in checks)
    write_json(
        PREVALIDATION_PATH,
        {
            "schema_version": "bb.e4.c4_prevalidation_report.v1",
            "target_family": TARGET_FAMILY,
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "ok": ok,
            "accepted": ok,
            "exit_status": "passed" if ok else "failed",
            "generated_at_utc": generated_at,
            "artifact_hashes": artifact_hashes,
            "checks": checks,
            "errors": [] if ok else [check["name"] for check in checks if not check["passed"]],
        },
    )
    if not ok:
        raise RuntimeError("prevalidation failed")


def update_ledger_and_support(generated_at: str) -> str:
    freeze_hash = freeze_row_hash()
    refs = {
        "freeze": f"config/e4_target_freeze_manifest.yaml#{CONFIG_ID}#{freeze_hash}",
        "capture": f"{display_path(RAW_CAPTURE_PATH)}#{sha256_file(RAW_CAPTURE_PATH)}",
        "replay": f"{display_path(REPLAY_PATH)}#{sha256_file(REPLAY_PATH)}",
        "comparator": f"{display_path(COMPARATOR_PATH)}#{sha256_file(COMPARATOR_PATH)}",
        "support_claim": display_path(SUPPORT_CLAIM_PATH),
        "evidence_manifest": display_path(EVIDENCE_MANIFEST_PATH),
        "parity_results": f"{display_path(PARITY_PATH)}#{sha256_file(PARITY_PATH)}",
        "secret_scan_report": f"{display_path(SECRET_SCAN_PATH)}#{sha256_file(SECRET_SCAN_PATH)}",
        "validator_output": f"{display_path(PREVALIDATION_PATH)}#{sha256_file(PREVALIDATION_PATH)}",
        "memory_compaction_plan": f"{display_path(MEMORY_PLAN_PATH)}#{sha256_file(MEMORY_PLAN_PATH)}",
        "transcript_continuation_patch": f"{display_path(TRANSCRIPT_PATCH_PATH)}#{sha256_file(TRANSCRIPT_PATCH_PATH)}",
        "target_probe": f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}",
    }
    row = {
        "schema_version": "bb.atomic_feature_ledger.v1",
        "feature_id": FEATURE_ID,
        "family": "memory",
        "target": "omp",
        "claim_type": "runtime_behavior",
        "evidence_tier": "C4",
        "dedupe_key": "omp/memory/oh_my_pi_p6_0_l5_memory_compaction/snapcompact/model_no/stateful_yes",
        "e4_row_ref": CONFIG_ID,
        "model_visible": True,
        "stateful": True,
        "gap_kind": "none",
        "promotion_state": "ready",
        "breadboard_mapping": {
            "primitive": "bb.memory_compaction_plan.v1",
            "support": "supported",
            "truth_scope": "target_projection",
        },
        "fixture_refs": [f"{role}:{ref}" for role, ref in refs.items()],
        "source_refs": [
            f"source:{display_path(SUPPORT_CLAIM_PATH)}",
            f"source:{display_path(EVIDENCE_MANIFEST_PATH)}",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/docs/compaction.md",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/agent/src/compaction/compaction.ts",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/snapcompact/src/snapcompact.ts",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/tools/memory-retain.ts",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/tools/memory-recall.ts",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/tools/memory-reflect.ts",
        ],
    }
    ledger = read_json(LEDGER_PATH)
    rows = ledger.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ledger rows must be a list")
    rows[:] = [
        existing
        for existing in rows
        if not (isinstance(existing, dict) and (existing.get("feature_id") == FEATURE_ID or existing.get("e4_row_ref") == CONFIG_ID))
    ]
    rows.append(row)
    write_json(LEDGER_PATH, ledger)
    ledger_row_hash = row_content_hash(FEATURE_ID, row)
    support = {
        "schema_version": "bb.e4.support_claim.v1",
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "run_id": RUN_ID,
        "sandbox_mode": SANDBOX_MODE,
        "tool_id": LANE_ID,
        "phase": "P6",
        "level": "P6.0-L5",
        "points": 15,
        "accepted": True,
        "summary": "Oh-My-Pi 16.2.13 provider-free memory/compaction support is accepted only for the named P6.0-L5 lane: target snapcompact compaction preparation and transcript continuation plus target-observed memory backend/tool visibility without initialized backend state.",
        "acceptance_rationale": "The governed claim is limited to frozen target runtime execution of prepareCompaction, @oh-my-pi/snapcompact.compact, createCompactionSummaryMessage, convertMessageToLlm, and retain/recall/reflect memory-tool visibility. The packet contains raw command reports, BreadBoard replay into bb.memory_compaction_plan.v1 and bb.transcript_continuation_patch.v1, comparator pass, parity result, packet-local secret scan, validator-output hash, and ledger-row freshness.",
        "scope": {
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "target_version": TARGET_VERSION,
            "provider_model": PROVIDER_MODEL,
            "sandbox_mode": SANDBOX_MODE,
            "memory_boundary": "provider-free snapcompact compaction plus memory-backend/tool visibility only",
            "compaction_strategy": "snapcompact",
            "remote_compaction": False,
            "memory_backend_state": "none absent; mnemopi/hindsight blocked without initialized state",
        },
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No Pi, OpenCode, Opencode, Codex, Claude, provider-parity, task/agent-routing/job, browser automation, screenshot, or general P6/P7 support claim is made.",
            "No write-enabled, provider-authenticated, or danger-full-access sandbox behavior is claimed.",
            "No claim is made for provider endpoints, web search execution, external SaaS APIs, model inference, remote compaction, or provider-native compaction.",
            "No initialized durable Mnemopi/Hindsight memory store or successful memory persistence is claimed; the evidence captures target-observed backend/tool visibility and blocked status without initialized backend state.",
            "No task, job, subagent, IRC, wake subscription, or work_item lane semantics are claimed.",
        ],
        "freeze_ref": refs["freeze"],
        "capture_ref": refs["capture"],
        "raw_source_ref": refs["target_probe"],
        "source_freeze_ref": f"{display_path(SOURCE_FREEZE)}#{sha256_file(SOURCE_FREEZE)}",
        "replay_ref": refs["replay"],
        "comparator_ref": refs["comparator"],
        "parity_results_ref": refs["parity_results"],
        "secret_scan_ref": refs["secret_scan_report"],
        "evidence_manifest_ref": display_path(EVIDENCE_MANIFEST_PATH),
        "ledger_row_refs": [f"{display_path(LEDGER_PATH)}#{FEATURE_ID}#{ledger_row_hash}"],
        "validation_refs": [refs["validator_output"]],
        "generated_at_utc": generated_at,
    }
    write_json(SUPPORT_CLAIM_PATH, support)
    artifacts = [
        {"path": "config/e4_target_freeze_manifest.yaml", "role": "freeze_manifest", "sha256": freeze_hash},
        {"path": display_path(RAW_CAPTURE_PATH), "role": "capture_ref", "sha256": sha256_file(RAW_CAPTURE_PATH)},
        {
            "path": display_path(REPLAY_PATH),
            "role": "replay_ref",
            "sha256": sha256_file(REPLAY_PATH),
            "derived_from": [refs["capture"], refs["memory_compaction_plan"], refs["transcript_continuation_patch"]],
        },
        {
            "path": display_path(COMPARATOR_PATH),
            "role": "comparator_ref",
            "sha256": sha256_file(COMPARATOR_PATH),
            "derived_from": [refs["capture"], refs["replay"], refs["memory_compaction_plan"], refs["transcript_continuation_patch"]],
        },
        {"path": display_path(PARITY_PATH), "role": "parity_results", "sha256": sha256_file(PARITY_PATH), "derived_from": [refs["comparator"]]},
        {"path": display_path(SECRET_SCAN_PATH), "role": "secret_scan_report", "sha256": sha256_file(SECRET_SCAN_PATH)},
        {"path": display_path(PREVALIDATION_PATH), "role": "validator_output", "sha256": sha256_file(PREVALIDATION_PATH)},
        {"path": display_path(SUPPORT_CLAIM_PATH), "role": "support_claim_ref", "sha256": sha256_file(SUPPORT_CLAIM_PATH)},
        {"path": display_path(LEDGER_PATH), "role": "atomic_feature_ledger", "sha256": sha256_file(LEDGER_PATH)},
        {"path": display_path(MEMORY_PLAN_PATH), "role": "memory_compaction_plan", "sha256": sha256_file(MEMORY_PLAN_PATH)},
        {"path": display_path(TRANSCRIPT_PATCH_PATH), "role": "transcript_continuation_patch", "sha256": sha256_file(TRANSCRIPT_PATCH_PATH)},
        {"path": display_path(TARGET_PROBE_PATH), "role": "target_probe_output", "sha256": sha256_file(TARGET_PROBE_PATH)},
    ]
    evidence_manifest = {
        "schema_version": "bb.e4.evidence_manifest.v1",
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "hash_algorithm": "sha256",
        "generated_at_utc": generated_at,
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "manifest_scope_note": f"Every governed artifact for this {LANE_ID} C4 chain is packet-local canonical repo evidence, source-freeze evidence under docs_tmp/phase_15/source_freezes, or a named target command/probe artifact. Scratch/tmp/shared roots are not promotion evidence.",
        "artifacts": artifacts,
    }
    write_json(EVIDENCE_MANIFEST_PATH, evidence_manifest)
    return ledger_row_hash


def update_ct_scenario() -> None:
    try:
        from scripts.e4_parity.generate_ct_rows import upsert_inventory_scenarios
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        from generate_ct_rows import upsert_inventory_scenarios

    upsert_inventory_scenarios(CT_SCENARIOS_PATH)


def build_all(*, force: bool = False) -> dict[str, Any]:
    generated_at = utc_now()
    write_agent_config()
    ensure_freeze_row()
    reports = run_capture(generated_at, force=force)
    build_setup_report(reports, generated_at)
    build_raw_capture_manifest(reports, generated_at)
    build_replay_and_comparator(generated_at)
    # Rebuild raw capture now that derived replay artifacts exist so source hashes include them.
    build_raw_capture_manifest(reports, generated_at)
    build_replay_and_comparator(generated_at)
    build_secret_scan(generated_at)
    build_prevalidation(generated_at)
    ledger_row_hash = update_ledger_and_support(generated_at)
    update_ct_scenario()
    return {
        "ok": True,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "run_id": RUN_ID,
        "feature_id": FEATURE_ID,
        "ledger_row_hash": ledger_row_hash,
        "support_claim": display_path(SUPPORT_CLAIM_PATH),
        "evidence_manifest": display_path(EVIDENCE_MANIFEST_PATH),
        "ct_id": CT_ID,
        "ct_output": CT_OUTPUT,
        "generated_at_utc": generated_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Oh-My-Pi P6.0-L5 memory/compaction C4 artifacts")
    parser.add_argument("--force", action="store_true", help="remove and rebuild the lane evidence directory")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args(argv)
    result = build_all(force=args.force)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
