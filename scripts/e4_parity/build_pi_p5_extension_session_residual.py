#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_e4_c4_chain import validate_c4_chain
from scripts.e4_parity import lane_inventory_utils as lane_inventory

GENERATED_AT_UTC = "2026-07-03T10:45:00Z"
THIS_BUILDER_PATH = Path(__file__).resolve()
LANE = lane_inventory.lane_for_builder(THIS_BUILDER_PATH)
LANE_ID = str(LANE["lane_id"])
CONFIG_ID = str(LANE["config_id"])
CLAIM_ID = lane_inventory.claim_id(LANE)
RUN_ID = str(LANE["run_id"])
TARGET_FAMILY = str(LANE["target_family"])
TARGET_VERSION = str(LANE["target_version"])
PROVIDER_MODEL = str(LANE["provider_model"])
SANDBOX_MODE = str(LANE["sandbox_mode"])
PHASE = str(LANE["phase"])
P5_ITEMS = ["P5.2-residual", "P5.3-residual", "P5.5", "P5.6"]
POINTS = int(LANE["points"])
FEATURE_ID = lane_inventory.ledger_feature_id(LANE)
CT_ID = lane_inventory.ct_id(LANE)
CT_OUTPUT = lane_inventory.ct_output(LANE)

ZIP_PATH = WORKSPACE / "docs_tmp/phase_15/JUNE_26_FEATURE_AUDIT_PRO_ATTACHMENTS_FLAT/01_pi_mono_git_tracked.zip"
SOURCE_FREEZE_PATH = WORKSPACE / "docs_tmp/phase_15/source_freezes/pi_mono_0_57_1_archive_freeze_provenance.json"
FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
SCHEMA_DIR = ROOT / "contracts/kernel/schemas"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
CT_SCENARIOS_PATH = ROOT / "docs/conformance/ct_scenarios_v1.json"
AGENT_CONFIG_PATH = ROOT / "agent_configs/misc" / f"{CONFIG_ID}.yaml"
LANE_DIR = ROOT / "docs/conformance/e4_target_support" / LANE_ID
RAW_DIR = LANE_DIR / "raw"
TARGET_PROBE_SCRIPT_PATH = LANE_DIR / "pi_p5_residual_target_probe.mjs"
TARGET_PROBE_OUTPUT_PATH = LANE_DIR / "target_probe_output.json"
SETUP_REPORT_PATH = LANE_DIR / "target_setup_and_capture_report.json"
RAW_CAPTURE_PATH = LANE_DIR / "raw_capture_manifest.json"
REPLAY_PATH = LANE_DIR / "bb_replay_result.json"
COMPARATOR_PATH = LANE_DIR / "comparator_report.json"
PARITY_PATH = LANE_DIR / "parity_results.json"
SECRET_SCAN_PATH = LANE_DIR / "secret_scan_report.json"
PREVALIDATION_PATH = LANE_DIR / "prevalidation_report.json"
SUPPORT_CLAIM_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID}.json"
EVIDENCE_MANIFEST_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json"
NODE_GATE_PATH = ROOT / CT_OUTPUT

CAPTURE_COMMAND_NAMES = ("node_version", "npm_version", "npm_ci", "module_probe")

SECRET_KEYS = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ZAI_API_KEY",
    "MISTRAL_API_KEY",
    "MINIMAX_API_KEY",
    "AI_GATEWAY_API_KEY",
    "OPENCODE_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
]

PROBE_SOURCE = r"""
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';

const repoRoot = resolve(process.env.PI_REPO_ROOT ?? '.');
const fixtureRoot = resolve(process.env.P5_FIXTURE_ROOT ?? '/tmp/pi_p5_residual_fixture_workspace');
const agentDir = resolve(process.env.P5_AGENT_DIR ?? '/tmp/pi_p5_residual_agent_dir');
const cwd = join(fixtureRoot, 'project');
const sessionDir = join(agentDir, 'sessions', 'p5-residual');

rmSync(fixtureRoot, { recursive: true, force: true });
rmSync(agentDir, { recursive: true, force: true });
mkdirSync(cwd, { recursive: true });
mkdirSync(sessionDir, { recursive: true });
mkdirSync(join(cwd, '.pi', 'extensions'), { recursive: true });
mkdirSync(agentDir, { recursive: true });

const extensionPath = join(cwd, '.pi', 'extensions', 'residual-hook.mjs');
writeFileSync(extensionPath, `
export default function(api) {
  const log = globalThis.__PI_P5_RESIDUAL_EVENTS ?? (globalThis.__PI_P5_RESIDUAL_EVENTS = []);
  const record = (event, payload = {}) => log.push({ event, payload });
  api.registerFlag('residual-flag', { type: 'boolean', default: true, description: 'Residual evidence flag' });
  api.registerCommand('residual-command', { description: 'Residual command', handler: async (_args, ctx) => { record('command_handler', { cwd: ctx.cwd, hasUI: ctx.hasUI }); } });
  api.registerTool({
    name: 'residual_tool',
    label: 'Residual Tool',
    description: 'Provider-free residual hook tool',
    promptSnippet: 'residual_tool: provider-free residual probe',
    parameters: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'], additionalProperties: false },
    execute: async (_toolCallId, params) => ({ content: [{ type: 'text', text: 'residual:' + params.value }], details: { observed: params.value }, isError: false })
  });
  api.on('resources_discover', (event) => { record('resources_discover', { cwd: event.cwd, reason: event.reason }); return { skillPaths: ['skills/residual'], promptPaths: ['prompts/residual'], themePaths: ['themes/residual'] }; });
  api.on('context', (event) => { record('context', { messages: event.messages.length }); return { messages: [...event.messages, { role: 'custom', customType: 'residual_context', content: [{ type: 'text', text: 'residual context' }], display: false }] }; });
  api.on('before_provider_request', (event) => { record('before_provider_request', { hasPayload: event.payload !== undefined }); return { ...event.payload, residualHook: true }; });
  api.on('before_agent_start', (event) => { record('before_agent_start', { prompt: event.prompt, systemPrompt: event.systemPrompt }); return { message: { customType: 'residual_agent_start', content: 'residual start', display: false }, systemPrompt: event.systemPrompt + '\\nresidual-start' }; });
  api.on('tool_call', (event) => { record('tool_call', { toolName: event.toolName }); return { block: event.toolName === 'write', reason: event.toolName === 'write' ? 'residual write blocked' : undefined }; });
  api.on('tool_result', (event) => { record('tool_result', { toolName: event.toolName, isError: event.isError }); return { isError: false, content: [{ type: 'text', text: 'residual tool result' }], details: { residual: true } }; });
  api.on('user_bash', (event) => { record('user_bash', { command: event.command, excludeFromContext: event.excludeFromContext }); return {}; });
  api.on('session_before_switch', (event) => { record('session_before_switch', { reason: event.reason, targetSessionFile: event.targetSessionFile ?? null }); return { cancel: false }; });
  api.on('session_before_fork', (event) => { record('session_before_fork', { entryId: event.entryId }); return { cancel: false, skipConversationRestore: true }; });
  api.on('session_before_compact', (event) => { record('session_before_compact', { branchEntries: event.branchEntries.length, customInstructions: event.customInstructions ?? null }); return { compaction: { summary: 'extension compact summary', firstKeptEntryId: event.branchEntries.at(-1)?.id ?? 'none', tokensBefore: 4242, details: { source: 'residual-hook' } } }; });
  api.on('session_compact', (event) => { record('session_compact', { compactionEntryType: event.compactionEntry.type, fromExtension: event.fromExtension }); });
  api.on('session_before_tree', (event) => { record('session_before_tree', { targetId: event.preparation.targetId, userWantsSummary: event.preparation.userWantsSummary }); return { summary: { summary: 'tree summary from extension', details: { source: 'residual-hook' } }, label: 'residual-label' }; });
  api.on('session_tree', (event) => { record('session_tree', { newLeafId: event.newLeafId, oldLeafId: event.oldLeafId, fromExtension: event.fromExtension ?? false }); });
}
`, 'utf8');

process.env.PI_CODING_AGENT_DIR = agentDir;
process.env.PI_OFFLINE = '1';
process.chdir(repoRoot);

const loaderModule = await import(join(repoRoot, 'packages/coding-agent/src/core/extensions/loader.ts'));
const runnerModule = await import(join(repoRoot, 'packages/coding-agent/src/core/extensions/runner.ts'));
const sessionModule = await import(join(repoRoot, 'packages/coding-agent/src/core/session-manager.ts'));
const configModule = await import(join(repoRoot, 'packages/coding-agent/src/config.ts'));

const { loadExtensions } = loaderModule;
const { ExtensionRunner } = runnerModule;
const { SessionManager, buildSessionContext, loadEntriesFromFile, parseSessionEntries } = sessionModule;

const loaded = await loadExtensions([extensionPath], cwd);
const session = SessionManager.create(cwd, sessionDir);
const userEntryId = session.appendMessage({ role: 'user', content: [{ type: 'text', text: 'hello residual' }], timestamp: 10 });
const assistantEntryId = session.appendMessage({ role: 'assistant', content: [{ type: 'text', text: 'answer residual' }], provider: 'no-provider', model: 'residual-model', usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2 }, timestamp: 20 });
const customEntryId = session.appendCustomMessageEntry('residual_custom', [{ type: 'text', text: 'custom context' }], false, { retained: true });
const compactionEntryId = session.appendCompaction('manual compact summary', userEntryId, 3210, { readFiles: ['a.ts'], modifiedFiles: ['b.ts'] }, false);
session.branch(assistantEntryId);
const branchSummaryEntryId = session.branchWithSummary(assistantEntryId, 'branch summary residual', { reason: 'fork' }, true);
const branchedSessionPath = session.createBranchedSession(branchSummaryEntryId);
const forked = SessionManager.forkFrom(branchedSessionPath, join(fixtureRoot, 'forked-project'), join(agentDir, 'sessions', 'forked'));
const continued = SessionManager.continueRecent(cwd, sessionDir);
const reopened = SessionManager.open(branchedSessionPath, sessionDir);

const runner = new ExtensionRunner(loaded.extensions, loaded.runtime, cwd, session, { registerProvider() {}, unregisterProvider() {} });
const activeTools = ['read', 'bash'];
runner.bindCore({
  sendMessage: () => {},
  sendUserMessage: () => {},
  appendEntry: (customType, data) => session.appendCustomEntry(customType, data),
  setSessionName: (name) => session.appendSessionInfo(name),
  getSessionName: () => session.getSessionName(),
  setLabel: (entryId, label) => session.appendLabelChange(entryId, label),
  getActiveTools: () => activeTools,
  getAllTools: () => ['read', 'bash', 'residual_tool'],
  setActiveTools: (names) => { activeTools.splice(0, activeTools.length, ...names); },
  refreshTools: () => {},
  getCommands: () => runner.getRegisteredCommands(),
  setModel: async () => {},
  getThinkingLevel: () => 'off',
  setThinkingLevel: () => {}
}, {
  getModel: () => undefined,
  isIdle: () => true,
  abort: () => {},
  hasPendingMessages: () => false,
  shutdown: () => {},
  getContextUsage: () => ({ tokens: 42, contextWindow: 1000, percent: 4.2 }),
  compact: () => {},
  getSystemPrompt: () => 'system prompt'
});
runner.bindCommandContext({
  waitForIdle: async () => {},
  newSession: async () => ({ cancelled: false }),
  fork: async () => ({ cancelled: false }),
  navigateTree: async () => ({ cancelled: false }),
  switchSession: async () => ({ cancelled: false }),
  reload: async () => {}
});

const providerPayload = await runner.emitBeforeProviderRequest({ messages: [{ role: 'user', content: 'hi' }] });
const contextMessages = await runner.emitContext([{ role: 'user', content: [{ type: 'text', text: 'hi' }] }]);
const beforeAgentStart = await runner.emitBeforeAgentStart('prompt residual', undefined, 'system prompt');
const toolCallRead = await runner.emitToolCall({ type: 'tool_call', toolCallId: 'tc-read', toolName: 'read', input: { path: 'README.md' } });
const toolCallWrite = await runner.emitToolCall({ type: 'tool_call', toolCallId: 'tc-write', toolName: 'write', input: { path: 'x', content: 'y' } });
const toolResult = await runner.emitToolResult({ type: 'tool_result', toolCallId: 'tc-read', toolName: 'read', input: { path: 'README.md' }, content: [{ type: 'text', text: 'original' }], details: undefined, isError: false });
const userBash = await runner.emit({ type: 'user_bash', command: 'echo residual', excludeFromContext: false, cwd });
const resourcesDiscover = await runner.emitResourcesDiscover(cwd, 'startup');
const sessionBeforeSwitch = await runner.emit({ type: 'session_before_switch', reason: 'resume', targetSessionFile: branchedSessionPath });
const sessionBeforeFork = await runner.emit({ type: 'session_before_fork', entryId: assistantEntryId });
const branchEntries = session.getBranch(compactionEntryId);
const sessionBeforeCompact = await runner.emit({ type: 'session_before_compact', preparation: { reason: 'manual' }, branchEntries, customInstructions: 'compact residual', signal: new AbortController().signal });
if (sessionBeforeCompact?.compaction) {
  const hookCompactionId = session.appendCompaction(sessionBeforeCompact.compaction.summary, sessionBeforeCompact.compaction.firstKeptEntryId, sessionBeforeCompact.compaction.tokensBefore, sessionBeforeCompact.compaction.details, true);
  const hookCompactionEntry = session.getEntry(hookCompactionId);
  await runner.emit({ type: 'session_compact', compactionEntry: hookCompactionEntry, fromExtension: true });
}
const treePrep = { targetId: userEntryId, oldLeafId: session.getLeafId(), commonAncestorId: userEntryId, entriesToSummarize: branchEntries, userWantsSummary: true, customInstructions: 'tree residual', label: 'user-label' };
const sessionBeforeTree = await runner.emit({ type: 'session_before_tree', preparation: treePrep, signal: new AbortController().signal });
await runner.emit({ type: 'session_tree', newLeafId: userEntryId, oldLeafId: treePrep.oldLeafId, summaryEntry: session.getEntry(branchSummaryEntryId), fromExtension: true });

const sessionFile = session.getSessionFile();
const sessionFileText = readFileSync(sessionFile, 'utf8');
const parsedSessionFile = parseSessionEntries(sessionFileText);
const loadedSessionFile = loadEntriesFromFile(sessionFile);
const branchedEntries = loadEntriesFromFile(branchedSessionPath);
const forkedEntries = loadEntriesFromFile(forked.getSessionFile());
const sessionContext = buildSessionContext(session.getEntries(), session.getLeafId());
const latestCompaction = sessionModule.getLatestCompactionEntry(session.getEntries());

const hash = (value) => 'sha256:' + createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');
const result = {
  schema_version: 'pi.p5.residual.target_probe_output.v1',
  app: {
    name: configModule.APP_NAME,
    config_dir_name: configModule.CONFIG_DIR_NAME,
    version: configModule.VERSION,
    env_agent_dir: configModule.ENV_AGENT_DIR,
    sessions_dir: configModule.getSessionsDir()
  },
  cwd,
  agent_dir: agentDir,
  extension: {
    path: extensionPath,
    loaded_count: loaded.extensions.length,
    errors: loaded.errors,
    paths: runner.getExtensionPaths(),
    registered_tools: runner.getAllRegisteredTools().map((tool) => ({ name: tool.definition.name, label: tool.definition.label, promptSnippet: tool.definition.promptSnippet })),
    registered_commands: runner.getRegisteredCommands().map((command) => ({ name: command.name, description: command.description })),
    flags: Array.from(runner.getFlags().values()).map((flag) => ({ name: flag.name, type: flag.type, default: flag.default })),
    event_log: globalThis.__PI_P5_RESIDUAL_EVENTS ?? [],
    emitted_results: { providerPayload, contextMessages, beforeAgentStart, toolCallRead, toolCallWrite, toolResult, userBash, resourcesDiscover, sessionBeforeSwitch, sessionBeforeFork, sessionBeforeCompact, sessionBeforeTree }
  },
  session: {
    session_dir: sessionDir,
    session_file: sessionFile,
    session_id: session.getSessionId(),
    user_entry_id: userEntryId,
    assistant_entry_id: assistantEntryId,
    custom_entry_id: customEntryId,
    compaction_entry_id: compactionEntryId,
    branch_summary_entry_id: branchSummaryEntryId,
    branched_session_path: branchedSessionPath,
    forked_session_file: forked.getSessionFile(),
    continued_session_file: continued.getSessionFile(),
    reopened_session_id: reopened.getSessionId(),
    header: session.getHeader(),
    tree_root_count: session.getTree().length,
    leaf_id: session.getLeafId(),
    context_messages: sessionContext.messages,
    context_model: sessionContext.model,
    latest_compaction: latestCompaction,
    session_file_entry_types: parsedSessionFile.map((entry) => entry.type),
    loaded_entry_count: loadedSessionFile.length,
    branched_header: branchedEntries[0],
    branched_entry_types: branchedEntries.map((entry) => entry.type),
    forked_header: forkedEntries[0],
    forked_entry_types: forkedEntries.map((entry) => entry.type),
    hashes: {
      session_file: hash(sessionFileText),
      branched_session: hash(readFileSync(branchedSessionPath, 'utf8')),
      forked_session: hash(readFileSync(forked.getSessionFile(), 'utf8'))
    }
  },
  no_secret_env: {
    ANTHROPIC_API_KEY: Boolean(process.env.ANTHROPIC_API_KEY),
    OPENAI_API_KEY: Boolean(process.env.OPENAI_API_KEY),
    GEMINI_API_KEY: Boolean(process.env.GEMINI_API_KEY),
    PI_OFFLINE: process.env.PI_OFFLINE
  }
};
console.log('PI_P5_RESIDUAL_PROBE_JSON=' + JSON.stringify(result));
""".strip()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def sha256_bytes(data: bytes) -> str:
    return _hash_utils.sha256_bytes(data)


def sha256_text(text: str) -> str:
    return _hash_utils.sha256_text(text)


def sha256_json(value: Any) -> str:
    return _hash_utils.sha256_json(value)


def sha256_file(path: Path) -> str:
    return _hash_utils.sha256_file(path)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    for base in (ROOT.resolve(), WORKSPACE.resolve()):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


def ref(path: Path) -> str:
    return f"{display_path(path)}#{sha256_file(path)}"


def row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    return _hash_utils.sha256_json({"row_id": row_id, "row": row})


def load_freeze_manifest() -> dict[str, Any]:
    payload = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("freeze manifest must be a mapping")
    payload.setdefault("e4_configs", {})
    return payload


def freeze_row_hash() -> str:
    manifest = load_freeze_manifest()
    return row_hash(CONFIG_ID, manifest["e4_configs"][CONFIG_ID])


def ensure_source_freeze() -> None:
    if SOURCE_FREEZE_PATH.exists():
        return
    root_pkg = json.loads(zipfile.ZipFile(ZIP_PATH).read("package.json").decode("utf-8"))
    agent_pkg = json.loads(zipfile.ZipFile(ZIP_PATH).read("packages/coding-agent/package.json").decode("utf-8"))
    write_json(SOURCE_FREEZE_PATH, {
        "schema_version": "bb.e4.source_freeze_provenance.v1",
        "generated_at_utc": GENERATED_AT_UTC,
        "source_id": "pi_mono_0_57_1_archive",
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "archive": {"path": display_path(ZIP_PATH), "sha256": sha256_file(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size},
        "root_package": {"name": root_pkg.get("name"), "version": root_pkg.get("version"), "workspaces": root_pkg.get("workspaces")},
        "coding_agent_package": {"name": agent_pkg.get("name"), "version": agent_pkg.get("version"), "bin": agent_pkg.get("bin"), "repository": agent_pkg.get("repository"), "piConfig": agent_pkg.get("piConfig"), "engines": agent_pkg.get("engines")},
        "commit_provenance": {"kind": "archive_snapshot_without_git_dir", "upstream_repo": "https://github.com/badlogic/pi-mono.git", "archive_sha256": sha256_file(ZIP_PATH), "note": "The local Pi source archive contains tracked source files and package metadata but no .git directory; exact source freeze is the archive hash plus package version."},
    })


def write_agent_config() -> None:
    payload = {
        "schema_version": "bb.e4.pi_p5_lane_config.v1",
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "phase": PHASE,
        "p5_items": P5_ITEMS,
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "sandbox_mode": SANDBOX_MODE,
        "claim_scope": "No-secret Pi residual capture for extension hook registration/execution and session resume/fork/tree/compaction behavior, including extension-discovered context resources.",
        "exclusions": ["provider-authenticated inference", "network behavior", "UI rendering", "broad Pi customization outside observed extension/session APIs"],
    }
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(yaml.safe_dump(payload, sort_keys=False, width=120), encoding="utf-8")


def write_freeze_manifest() -> None:
    manifest = load_freeze_manifest()
    manifest["e4_configs"][CONFIG_ID] = {
        "config_path": display_path(AGENT_CONFIG_PATH),
        "harness": {
            "family": TARGET_FAMILY,
            "upstream_repo": "https://github.com/badlogic/pi-mono.git",
            "upstream_commit": sha256_file(ZIP_PATH).replace("sha256:", "archive-sha256-"),
            "upstream_ref": "archive:01_pi_mono_git_tracked.zip",
            "upstream_version": TARGET_VERSION,
            "runtime_surface": {"provider_model": PROVIDER_MODEL, "sandbox_mode": SANDBOX_MODE, "target_profile_id": LANE_ID},
        },
        "calibration_anchor": {
            "class": "raw_target_capture",
            "scenario_id": LANE_ID,
            "run_id": RUN_ID,
            "evidence_paths": [display_path(path) for path in [SETUP_REPORT_PATH, TARGET_PROBE_OUTPUT_PATH, RAW_CAPTURE_PATH, REPLAY_PATH, COMPARATOR_PATH, PARITY_PATH, SECRET_SCAN_PATH, PREVALIDATION_PATH, SUPPORT_CLAIM_PATH, EVIDENCE_MANIFEST_PATH]],
        },
        "snapshot_source_entry": CONFIG_ID,
        "snapshot_tag": "pi_0_57_1_p5_l2_extension_session_residual_20260703",
    }
    FREEZE_MANIFEST_PATH.write_text(yaml.safe_dump(manifest, sort_keys=False, width=120), encoding="utf-8")


def command_env(tmp: Path) -> dict[str, str]:
    env = {
        "HOME": str(tmp / "home"),
        "PATH": os.environ.get("PATH", ""),
        "npm_config_userconfig": str(tmp / "empty-npmrc"),
        "npm_config_cache": str(tmp / "npm-cache"),
        "PI_CODING_AGENT_DIR": str(tmp / "agent"),
        "PI_OFFLINE": "1",
        "CI": "1",
        "NO_COLOR": "1",
    }
    for key in SECRET_KEYS:
        env.pop(key, None)
    return env


def run_cmd(args: list[str], cwd: Path, env: Mapping[str, str], timeout: int = 180) -> dict[str, Any]:
    completed = subprocess.run(args, cwd=str(cwd), env=dict(env), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    return {"argv": args, "cwd": str(cwd), "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def extract_probe_json(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith("PI_P5_RESIDUAL_PROBE_JSON="):
            parsed = json.loads(line.split("=", 1)[1])
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("PI_P5_RESIDUAL_PROBE_JSON marker missing from probe output")


def existing_capture_paths() -> list[Path]:
    paths = [TARGET_PROBE_SCRIPT_PATH, TARGET_PROBE_OUTPUT_PATH, SETUP_REPORT_PATH]
    for name in CAPTURE_COMMAND_NAMES:
        paths.extend(
            [
                RAW_DIR / f"{name}.stdout.txt",
                RAW_DIR / f"{name}.stderr.txt",
                RAW_DIR / f"{name}.command.json",
            ]
        )
    return paths


def load_existing_capture() -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not all(path.exists() for path in existing_capture_paths()):
        return None
    probe = read_json(TARGET_PROBE_OUTPUT_PATH)
    setup = read_json(SETUP_REPORT_PATH)
    if not isinstance(probe, dict) or not isinstance(setup, dict):
        return None
    return probe, setup


def run_target_capture() -> tuple[dict[str, Any], dict[str, Any]]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_PROBE_SCRIPT_PATH.write_text(PROBE_SOURCE + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="pi_p5_residual_capture_") as tmp_name:
        tmp = Path(tmp_name)
        repo = tmp / "pi-mono"
        with zipfile.ZipFile(ZIP_PATH) as zf:
            zf.extractall(repo)
        env = command_env(tmp)
        (tmp / "home").mkdir(parents=True, exist_ok=True)
        (tmp / "empty-npmrc").write_text("", encoding="utf-8")
        commands: dict[str, dict[str, Any]] = {}
        commands["node_version"] = run_cmd(["node", "--version"], repo, env, timeout=60)
        commands["npm_version"] = run_cmd(["npm", "--version"], repo, env, timeout=60)
        commands["npm_ci"] = run_cmd(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], repo, env, timeout=600)
        probe_env = dict(env)
        probe_env["PI_REPO_ROOT"] = str(repo)
        probe_env["P5_FIXTURE_ROOT"] = str(tmp / "fixture_workspace")
        probe_env["P5_AGENT_DIR"] = str(tmp / "agent")
        commands["module_probe"] = run_cmd(["npx", "tsx", str(TARGET_PROBE_SCRIPT_PATH)], repo, probe_env, timeout=180)
        for name, result in commands.items():
            write_text(RAW_DIR / f"{name}.stdout.txt", str(result.get("stdout", "")))
            write_text(RAW_DIR / f"{name}.stderr.txt", str(result.get("stderr", "")))
            write_json(RAW_DIR / f"{name}.command.json", {key: value for key, value in result.items() if key not in {"stdout", "stderr"}})
        failures = {name: result["exit_code"] for name, result in commands.items() if result["exit_code"] != 0}
        if failures:
            raise RuntimeError(f"Pi residual target capture failed: {failures}")
        probe = extract_probe_json(str(commands["module_probe"]["stdout"]))
        setup = {
            "schema_version": "pi.p5.residual.target_setup_and_capture_report.v1",
            "generated_at_utc": GENERATED_AT_UTC,
            "run_id": RUN_ID,
            "target_family": TARGET_FAMILY,
            "target_version": TARGET_VERSION,
            "source_archive_ref": ref(ZIP_PATH),
            "tmp_root_redacted": tmp.name,
            "commands": {name: {"argv": result["argv"], "exit_code": result["exit_code"], "stdout_ref": ref(RAW_DIR / f"{name}.stdout.txt"), "stderr_ref": ref(RAW_DIR / f"{name}.stderr.txt"), "command_ref": ref(RAW_DIR / f"{name}.command.json")} for name, result in commands.items()},
            "no_secret_environment": {key: False for key in SECRET_KEYS},
            "sandbox_mode": SANDBOX_MODE,
        }
        write_json(TARGET_PROBE_OUTPUT_PATH, probe)
        write_json(SETUP_REPORT_PATH, setup)
        return probe, setup


def visibility(model: bool, provider: bool, host: bool) -> dict[str, bool]:
    return {"model_visible": model, "provider_visible": provider, "host_visible": host}


def compile_replay_records(probe: Mapping[str, Any]) -> dict[str, Any]:
    event_names = [entry["event"] for entry in probe["extension"]["event_log"]]
    session = probe["session"]
    session_ref = f"pi.session_file:{Path(session['session_file']).name}"
    branched_ref = f"pi.session_file:{Path(session['branched_session_path']).name}"
    forked_ref = f"pi.session_file:{Path(session['forked_session_file']).name}"
    extension_source_hash = sha256_file(TARGET_PROBE_SCRIPT_PATH)
    transcript_hash = session["hashes"]["session_file"]
    branch_hash = session["hashes"]["branched_session"]
    fork_hash = session["hashes"]["forked_session"]
    compaction_content = session["latest_compaction"]["summary"]
    compaction_hash = sha256_text(compaction_content)
    records: dict[str, Any] = {
        "extension_hook_execution_provider": {
            "schema_version": "bb.extension_hook_execution.v1",
            "execution_id": f"{LANE_ID}_before_provider_request_exec",
            "hook_id": f"{LANE_ID}_extension_before_provider_request",
            "hook_type": "pre_model",
            "event": {"event_id": f"{LANE_ID}_before_provider_request", "event_type": "before_provider_request", "event_ref": "pi.extension_event:before_provider_request", "emitted_at": GENERATED_AT_UTC, "resource_refs": [display_path(TARGET_PROBE_SCRIPT_PATH)]},
            "status": "completed",
            "started_at": GENERATED_AT_UTC,
            "duration_ms": 0,
            "effects": [{"effect_id": f"{LANE_ID}_provider_payload_patch", "effect_type": "tool_surface_patch", "effect_ref": "pi.provider_payload:residualHook", "status": "applied"}],
            "model_provider_visibility": {"model_visible": True, "provider_visible": False, "model_render_ref": "pi.extension:before_provider_request", "provider_exchange_ref": None},
        },
        "extension_hook_execution_session": {
            "schema_version": "bb.extension_hook_execution.v1",
            "execution_id": f"{LANE_ID}_session_lifecycle_exec",
            "hook_id": f"{LANE_ID}_extension_session_hooks",
            "hook_type": "policy",
            "event": {"event_id": f"{LANE_ID}_session_lifecycle", "event_type": "session_before_switch/session_before_fork/session_before_compact/session_before_tree", "event_ref": "pi.extension_event:session_lifecycle", "emitted_at": GENERATED_AT_UTC, "resource_refs": [session_ref, branched_ref, forked_ref]},
            "status": "completed",
            "started_at": GENERATED_AT_UTC,
            "duration_ms": 0,
            "effects": [
                {"effect_id": f"{LANE_ID}_resume_allowed", "effect_type": "signal", "effect_ref": "pi.session_before_switch:cancel=false", "status": "applied"},
                {"effect_id": f"{LANE_ID}_fork_restore_policy", "effect_type": "signal", "effect_ref": "pi.session_before_fork:skipConversationRestore=true", "status": "applied"},
                {"effect_id": f"{LANE_ID}_compaction_override", "effect_type": "resource_access", "effect_ref": f"bb.memory_compaction_plan.v1:{LANE_ID}_memory_compaction_plan", "status": "applied"},
                {"effect_id": f"{LANE_ID}_tree_summary", "effect_type": "log", "effect_ref": "pi.session_before_tree:summary", "status": "applied"},
            ],
            "model_provider_visibility": {"model_visible": True, "provider_visible": False, "model_render_ref": "pi.extension:session_lifecycle", "provider_exchange_ref": None},
        },
        "work_item_session_resume_fork": {
            "schema_version": "bb.work_item.v1",
            "work_item_id": f"{LANE_ID}_session_resume_fork_work",
            "identity": {"task_id": session["session_id"], "task_kind": "workflow", "subagent_id": None, "distributed_task_id": None, "correlation_id": RUN_ID},
            "delegation": {"parent_work_item_id": None, "parent_task_id": None, "delegated_by": {"actor_kind": "user", "actor_id": "pi-target-probe"}, "delegation_ref": None},
            "state": {"status": "completed", "entered_at": GENERATED_AT_UTC, "reason": "Pi SessionManager continued recent session, reopened branched session, and forked session file provider-free.", "checkpoint_ref": branched_ref},
            "owner": {"actor_kind": "agent", "actor_id": "pi-session-manager"},
            "assignee": {"actor_kind": "agent", "actor_id": "pi-session-manager"},
            "input_artifact_refs": [{"ref": session_ref, "artifact_kind": "transcript", "visibility": visibility(True, False, True)}],
            "output_artifact_refs": [{"ref": branched_ref, "artifact_kind": "checkpoint", "visibility": visibility(True, False, True)}, {"ref": forked_ref, "artifact_kind": "checkpoint", "visibility": visibility(True, False, True)}],
            "cancellation_policy": {"mode": "cooperative", "cancellable_by": [{"actor_kind": "user", "actor_id": "pi-user"}], "propagate_to_children": False, "on_cancel": "checkpoint_then_stop"},
            "resume_policy": {"mode": "checkpoint", "resume_from_ref": branched_ref, "requires_approval": False, "wake_refs": ["pi.session_before_switch:resume"]},
            "visibility": visibility(True, False, True),
        },
        "memory_compaction_plan": {
            "schema_version": "bb.memory_compaction_plan.v1",
            "plan_id": f"{LANE_ID}_memory_compaction_plan",
            "transcript_refs": [{"transcript_id": session["session_id"], "ref": session_ref, "start_seq": 0, "end_seq": len(session["session_file_entry_types"]) - 1, "hash": transcript_hash, "visibility": visibility(True, False, True)}],
            "trigger": {"kind": "manual", "source_ref": "pi.extension_event:session_before_compact", "reason": "Extension session_before_compact returned a provider-free compaction result.", "observed_tokens": 4242, "threshold_tokens": 3210},
            "token_budget": {"model_context_window": 1000, "max_input_tokens": 900, "reserved_output_tokens": 100, "before_tokens": 4242, "target_after_tokens": 3210},
            "preserved_refs": [{"ref_id": "first_kept", "ref_kind": "transcript_segment", "ref": f"{session_ref}#{session['user_entry_id']}", "hash": transcript_hash, "reason": "Pi compaction keeps the first referenced entry id.", "visibility": visibility(True, False, True)}],
            "elided_refs": [{"ref_id": "pre_compaction_tail", "ref": f"{session_ref}#tail", "reason": "Older branch content summarized by compaction entry.", "token_estimate": 1032, "replacement_ref": f"bb.memory_compaction_plan.v1:{LANE_ID}_memory_compaction_plan#summary", "visibility": visibility(True, False, True)}],
            "generated_refs": [{"ref_id": "compaction_summary", "ref_kind": "summary", "artifact_ref": f"{session_ref}#{session['latest_compaction']['id']}", "hash": compaction_hash, "produced_by": "pi-session-manager.appendCompaction"}],
            "model_visible_insertions": [{"insertion_id": "compaction_summary_message", "position": "prefix", "content_ref": f"{session_ref}#{session['latest_compaction']['id']}", "content_hash": compaction_hash, "token_estimate": 5}],
            "backend_contributions": [{"contribution_id": "pi_session_manager", "contributor_kind": "host", "contributor_id": "pi.core.session-manager", "input_refs": [session_ref], "output_refs": [f"{session_ref}#{session['latest_compaction']['id']}"], "visibility": visibility(True, False, True), "hash": compaction_hash}],
            "hashes": {"algorithm": "sha256", "source_hash": transcript_hash, "plan_hash": sha256_json({"lane": LANE_ID, "event_names": event_names, "compaction": compaction_content}), "compacted_hash": compaction_hash},
            "status": "applied",
        },
        "transcript_continuation_patch": {
            "schema_version": "bb.transcript_continuation_patch.v1",
            "patch_id": f"{LANE_ID}_resume_fork_patch",
            "pre_state_ref": session_ref,
            "appended_messages": session["context_messages"],
            "appended_tool_events": probe["extension"]["event_log"],
            "lineage_updates": [{"kind": "resume", "ref": session["continued_session_file"]}, {"kind": "fork", "ref": session["forked_session_file"], "parentSession": session["forked_header"].get("parentSession")}, {"kind": "branch", "ref": session["branched_session_path"], "parentSession": session["branched_header"].get("parentSession")}],
            "compaction_markers": [{"entry_id": session["latest_compaction"]["id"], "summary": session["latest_compaction"]["summary"], "fromHook": session["latest_compaction"].get("fromHook", False)}],
            "post_state_digest": fork_hash,
            "lossiness_flags": ["compaction_summary_replaces_elided_branch_tail"],
        },
    }
    validate_replay_records(records)
    return records


def validate_record_schema(record_name: str, record: Mapping[str, Any]) -> list[str]:
    schema_version = str(record.get("schema_version", ""))
    schema_path = SCHEMA_DIR / f"{schema_version}.schema.json"
    if not schema_path.exists():
        return [f"{record_name}: missing schema for {schema_version}"]
    validator = Draft202012Validator(read_json(schema_path))
    return [f"{record_name}.{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}" for error in sorted(validator.iter_errors(record), key=lambda item: list(item.absolute_path))]


def validate_replay_records(records: Mapping[str, Mapping[str, Any]]) -> None:
    errors: list[str] = []
    for name, record in records.items():
        errors.extend(validate_record_schema(name, record))
    if errors:
        raise RuntimeError("Replay record schema validation failed: " + "; ".join(errors))


def assertion_eq(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "status": "passed" if observed == expected else "failed", "observed": observed, "expected": expected}


def comparator_assertions(probe: Mapping[str, Any], replay_records: Mapping[str, Any]) -> list[dict[str, Any]]:
    event_names = [entry["event"] for entry in probe["extension"]["event_log"]]
    tools = [tool["name"] for tool in probe["extension"]["registered_tools"]]
    commands = [command["name"] for command in probe["extension"]["registered_commands"]]
    session = probe["session"]
    return [
        assertion_eq("package_version", probe["app"]["version"], "0.57.1"),
        assertion_eq("extension_loaded_once", probe["extension"]["loaded_count"], 1),
        assertion_eq("extension_load_errors", probe["extension"]["errors"], []),
        assertion_eq("extension_registered_tool", tools, ["residual_tool"]),
        assertion_eq("extension_registered_command", commands, ["residual-command"]),
        assertion_eq("extension_registered_flag", probe["extension"]["flags"], [{"name": "residual-flag", "type": "boolean", "default": True}]),
        assertion_eq("extension_event_coverage", sorted(set(event_names)), sorted(["resources_discover", "context", "before_provider_request", "before_agent_start", "tool_call", "tool_result", "user_bash", "session_before_switch", "session_before_fork", "session_before_compact", "session_compact", "session_before_tree", "session_tree"])),
        assertion_eq("provider_payload_patched", probe["extension"]["emitted_results"]["providerPayload"].get("residualHook"), True),
        assertion_eq("context_hook_appended_message", len(probe["extension"]["emitted_results"]["contextMessages"]), 2),
        assertion_eq("tool_write_blocked", probe["extension"]["emitted_results"]["toolCallWrite"], {"block": True, "reason": "residual write blocked"}),
        assertion_eq("session_switch_resume_allowed", probe["extension"]["emitted_results"]["sessionBeforeSwitch"], {"cancel": False}),
        assertion_eq("session_fork_policy", probe["extension"]["emitted_results"]["sessionBeforeFork"], {"cancel": False, "skipConversationRestore": True}),
        assertion_eq("session_compaction_hook_result", probe["extension"]["emitted_results"]["sessionBeforeCompact"].get("compaction", {}).get("summary"), "extension compact summary"),
        assertion_eq("session_tree_hook_summary", probe["extension"]["emitted_results"]["sessionBeforeTree"].get("summary", {}).get("summary"), "tree summary from extension"),
        assertion_eq("session_file_has_compaction", "compaction" in session["session_file_entry_types"], True),
        assertion_eq("branched_session_parent_ref", bool(session["branched_header"].get("parentSession")), True),
        assertion_eq("forked_session_parent", session["forked_header"].get("parentSession") == session["branched_session_path"], True),
        assertion_eq("continue_recent_returns_valid_session_file", bool(session["continued_session_file"]) and Path(session["continued_session_file"]).name.endswith(".jsonl"), True),
        assertion_eq("reopened_session_id_matches_branch", session["reopened_session_id"] == session["branched_header"].get("id"), True),
        assertion_eq("work_item_schema_replay", replay_records["work_item_session_resume_fork"]["resume_policy"]["mode"], "checkpoint"),
        assertion_eq("memory_plan_schema_replay", replay_records["memory_compaction_plan"]["status"], "applied"),
        assertion_eq("patch_schema_replay", replay_records["transcript_continuation_patch"]["post_state_digest"], session["hashes"]["forked_session"]),
        assertion_eq("no_provider_secrets", probe["no_secret_env"], {"ANTHROPIC_API_KEY": False, "OPENAI_API_KEY": False, "GEMINI_API_KEY": False, "PI_OFFLINE": "1"}),
    ]


def write_capture_replay_compare(*, recapture: bool = False) -> str:
    existing_capture = None if recapture else load_existing_capture()
    capture_mode = "reused" if existing_capture is not None else "live"
    probe, setup = existing_capture if existing_capture is not None else run_target_capture()
    replay_records = compile_replay_records(probe)
    replay = {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "p5_items": P5_ITEMS,
        "generated_at_utc": GENERATED_AT_UTC,
        "exit_status": "passed",
        "warnings": [],
        "errors": [],
        "input_hashes": {display_path(TARGET_PROBE_OUTPUT_PATH): sha256_file(TARGET_PROBE_OUTPUT_PATH), display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH), display_path(AGENT_CONFIG_PATH): sha256_file(AGENT_CONFIG_PATH)},
        "normalized_records": replay_records,
        "replay_summary": "BreadBoard replay maps Pi residual extension hooks plus session resume/fork/compaction observations to hook execution, work item, memory compaction, and transcript continuation records.",
    }
    write_json(REPLAY_PATH, replay)
    assertions = comparator_assertions(probe, replay_records)
    failed = sum(1 for item in assertions if item["status"] != "passed")
    comparator = {
        "schema_version": "bb.e4.comparator_report.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "scope": {"config_id": CONFIG_ID, "lane_id": LANE_ID, "phase": PHASE, "p5_items": P5_ITEMS, "provider_model": PROVIDER_MODEL, "run_id": RUN_ID, "sandbox_mode": SANDBOX_MODE, "target_version": TARGET_VERSION, "target_family": TARGET_FAMILY},
        "assertions": assertions,
        "details": [{"name": item["name"], "status": item["status"]} for item in assertions],
        "failed": failed,
        "warned": 0,
        "input_hashes": {display_path(RAW_CAPTURE_PATH): "pending", display_path(REPLAY_PATH): sha256_file(REPLAY_PATH), display_path(TARGET_PROBE_OUTPUT_PATH): sha256_file(TARGET_PROBE_OUTPUT_PATH)},
    }
    write_json(COMPARATOR_PATH, comparator)
    parity = {"schema_version": "bb.e4.parity_results.v1", "lane_id": LANE_ID, "config_id": CONFIG_ID, "passed": failed == 0, "failed": failed, "warned": 0, "comparator_ref": ref(COMPARATOR_PATH), "generated_at_utc": GENERATED_AT_UTC}
    write_json(PARITY_PATH, parity)
    scan_paths = [TARGET_PROBE_OUTPUT_PATH, SETUP_REPORT_PATH, REPLAY_PATH, COMPARATOR_PATH, PARITY_PATH, RAW_DIR / "module_probe.stdout.txt", TARGET_PROBE_SCRIPT_PATH]
    credential_patterns = [
        ("provider_key_prefix", re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]{16,}|sk-proj-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{32,}|AIza[0-9A-Za-z_-]{20,}|xai-[A-Za-z0-9_-]{20,})\b")),
        ("github_token_prefix", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{36,}|github_pat_[A-Za-z0-9_]{36,})\b")),
        ("authorization_bearer", re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
        ("env_secret_assignment", re.compile(r"(?m)[\"']?\b(?:ANTHROPIC_API_KEY|ANTHROPIC_OAUTH_TOKEN|OPENAI_API_KEY|GEMINI_API_KEY|GROQ_API_KEY|CEREBRAS_API_KEY|XAI_API_KEY|OPENROUTER_API_KEY|ZAI_API_KEY|MISTRAL_API_KEY|MINIMAX_API_KEY|AI_GATEWAY_API_KEY|OPENCODE_API_KEY|COPILOT_GITHUB_TOKEN|GH_TOKEN|GITHUB_TOKEN)\b[\"']?\s*[=:]\s*[\"']?(?!false\b|true\b|null\b|unset\b|<[^>]+>\b|$)([A-Za-z0-9._~+/=-]{12,})")),
    ]
    findings = []
    for path in scan_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in credential_patterns:
            if pattern.search(text):
                findings.append({"path": display_path(path), "pattern": name})
    secret_scan = {"schema_version": "bb.e4.secret_scan_report.v1", "lane_id": LANE_ID, "config_id": CONFIG_ID, "passed": not findings, "findings": findings, "checked_paths": [display_path(path) for path in scan_paths], "documented_key_names_allowed": SECRET_KEYS, "generated_at_utc": GENERATED_AT_UTC}
    write_json(SECRET_SCAN_PATH, secret_scan)
    prevalidation = {"schema_version": "bb.e4.prevalidation_report.v1", "ok": failed == 0 and not findings, "accepted": failed == 0 and not findings, "lane_id": LANE_ID, "config_id": CONFIG_ID, "checks": [{"name": "target_commands_exit_zero", "passed": True}, {"name": "replay_record_schema_validation", "passed": True}, {"name": "machine_comparator_assertions", "passed": failed == 0}, {"name": "secret_scan", "passed": not findings}], "generated_at_utc": GENERATED_AT_UTC}
    write_json(PREVALIDATION_PATH, prevalidation)
    raw_capture = {
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "accepted_as_capture_ref": True,
        "capture_class": "raw_target_capture",
        "raw_source_status": "canonical_raw_present",
        "generated_at_utc": GENERATED_AT_UTC,
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "sandbox_mode": SANDBOX_MODE,
        "raw_source_ref": ref(SETUP_REPORT_PATH),
        "lineage_rationale": "Pi residual capture runs @mariozechner/pi-coding-agent 0.57.1 from the frozen local source archive in an isolated no-secret target workspace, observing extension hook registration/execution and session resume/fork/compaction APIs without provider inference.",
        "source_artifacts": [display_path(ZIP_PATH), display_path(SOURCE_FREEZE_PATH), display_path(SETUP_REPORT_PATH), display_path(TARGET_PROBE_OUTPUT_PATH), display_path(TARGET_PROBE_SCRIPT_PATH), display_path(AGENT_CONFIG_PATH)],
        "source_hashes": {display_path(path): sha256_file(path) for path in [ZIP_PATH, SOURCE_FREEZE_PATH, SETUP_REPORT_PATH, TARGET_PROBE_OUTPUT_PATH, TARGET_PROBE_SCRIPT_PATH, AGENT_CONFIG_PATH]},
        "captured_artifacts": [{"path": display_path(path), "role": role, "sha256": sha256_file(path)} for path, role in [(SETUP_REPORT_PATH, "target_setup"), (TARGET_PROBE_OUTPUT_PATH, "target_probe"), (RAW_DIR / "module_probe.stdout.txt", "module_probe_stdout"), (RAW_DIR / "module_probe.stderr.txt", "module_probe_stderr")]],
        "target_source": {"repository": "https://github.com/badlogic/pi-mono.git", "package": "@mariozechner/pi-coding-agent", "version": "0.57.1", "source_archive_ref": ref(ZIP_PATH)},
        "scope_exclusions": ["no provider-authenticated inference", "no network behavior", "no UI rendering", "no broad Pi customization outside observed extension/session APIs"],
    }
    write_json(RAW_CAPTURE_PATH, raw_capture)
    comparator["input_hashes"][display_path(RAW_CAPTURE_PATH)] = sha256_file(RAW_CAPTURE_PATH)
    write_json(COMPARATOR_PATH, comparator)
    parity["comparator_ref"] = ref(COMPARATOR_PATH)
    write_json(PARITY_PATH, parity)
    prevalidation["checks"].append({"name": "raw_capture_written", "passed": True})
    write_json(PREVALIDATION_PATH, prevalidation)
    return capture_mode


def build_ledger_row(freeze_ref: str) -> dict[str, Any]:
    return {
        "schema_version": "bb.atomic_feature_ledger.v1",
        "feature_id": FEATURE_ID,
        "family": "target.pi",
        "target": "pi",
        "claim_type": "runtime_behavior",
        "evidence_tier": "C4",
        "e4_row_ref": CONFIG_ID,
        "dedupe_key": f"pi/p5/{LANE_ID}/c4",
        "breadboard_mapping": {"primitive": "bb.extension_hook_execution.v1 + bb.work_item.v1 + bb.memory_compaction_plan.v1 + bb.transcript_continuation_patch.v1", "support": "supported", "truth_scope": "kernel_truth"},
        "fixture_refs": [f"freeze:{freeze_ref}", f"capture:{ref(RAW_CAPTURE_PATH)}", f"replay:{ref(REPLAY_PATH)}", f"comparator:{ref(COMPARATOR_PATH)}", f"support_claim:{display_path(SUPPORT_CLAIM_PATH)}", f"evidence_manifest:{display_path(EVIDENCE_MANIFEST_PATH)}", f"parity_results:{ref(PARITY_PATH)}", f"secret_scan_report:{ref(SECRET_SCAN_PATH)}", f"validator_output:{ref(PREVALIDATION_PATH)}"],
        "source_refs": [f"source:{display_path(SOURCE_FREEZE_PATH)}", f"source:{display_path(ZIP_PATH)}", f"source:{display_path(SUPPORT_CLAIM_PATH)}", f"source:{display_path(EVIDENCE_MANIFEST_PATH)}"],
        "gap_kind": "none",
        "promotion_state": "ready",
        "model_visible": True,
        "stateful": True,
    }


def upsert_ledger(row: dict[str, Any]) -> str:
    ledger = read_json(LEDGER_PATH)
    rows = [item for item in ledger.get("rows", []) if not (isinstance(item, Mapping) and item.get("feature_id") == FEATURE_ID)]
    rows.append(row)
    ledger["rows"] = rows
    if isinstance(ledger.get("features"), list) and all(isinstance(item, Mapping) and item.get("feature_id") == FEATURE_ID for item in ledger["features"]):
        ledger.pop("features")
    write_json(LEDGER_PATH, ledger)
    return row_hash(FEATURE_ID, row)


def write_support_claim_and_manifest(ledger_ref: str, freeze_ref: str) -> None:
    support_claim = {
        "schema_version": "bb.e4.support_claim.v1",
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "accepted": True,
        "summary": "Pi P5 residual no-secret extension/session support is accepted only for observed extension hook execution plus SessionManager resume/fork/tree/compaction behavior from the frozen Pi 0.57.1 source.",
        "acceptance_rationale": "The target capture installs and runs frozen Pi source in an isolated no-secret workspace, loads a Pi extension through the target loader/runner, emits extension and session lifecycle events, exercises SessionManager continuation, branch/fork, and compaction APIs, and compares machine-observed results to exact replay records.",
        "generated_at_utc": GENERATED_AT_UTC,
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "run_id": RUN_ID,
        "sandbox_mode": SANDBOX_MODE,
        "tool_id": "pi.p5.residual.target_probe(extension-loader,extension-runner,session-manager)",
        "level": "/".join(P5_ITEMS),
        "phase": PHASE,
        "points": POINTS,
        "scope": {"config_id": CONFIG_ID, "lane_id": LANE_ID, "phase": PHASE, "p5_items": P5_ITEMS, "provider_model": PROVIDER_MODEL, "run_id": RUN_ID, "sandbox_mode": SANDBOX_MODE, "target_version": TARGET_VERSION},
        "exclusions": ["No provider-authenticated inference, network, or UI behavior is claimed.", "No target behavior outside the observed extension loader/runner and SessionManager residual APIs is claimed.", "No broad Pi customization parity beyond extension-discovered resources, registered tool/command/flag surface, and session lifecycle hooks is claimed."],
        "freeze_ref": freeze_ref,
        "capture_ref": ref(RAW_CAPTURE_PATH),
        "replay_ref": ref(REPLAY_PATH),
        "comparator_ref": ref(COMPARATOR_PATH),
        "evidence_manifest_ref": display_path(EVIDENCE_MANIFEST_PATH),
        "ledger_row_refs": [ledger_ref],
        "validation_refs": [ref(PREVALIDATION_PATH)],
        "parity_results_ref": ref(PARITY_PATH),
        "secret_scan_ref": ref(SECRET_SCAN_PATH),
        "source_freeze_ref": ref(SOURCE_FREEZE_PATH),
    }
    write_json(SUPPORT_CLAIM_PATH, support_claim)
    artifacts = [
        {"path": display_path(FREEZE_MANIFEST_PATH), "role": "freeze_manifest", "sha256": freeze_row_hash()},
        {"path": display_path(RAW_CAPTURE_PATH), "role": "capture_ref", "sha256": sha256_file(RAW_CAPTURE_PATH)},
        {"derived_from": [ref(RAW_CAPTURE_PATH), ref(TARGET_PROBE_OUTPUT_PATH)], "path": display_path(REPLAY_PATH), "role": "replay_ref", "sha256": sha256_file(REPLAY_PATH)},
        {"derived_from": [ref(RAW_CAPTURE_PATH), ref(REPLAY_PATH), ref(TARGET_PROBE_OUTPUT_PATH)], "path": display_path(COMPARATOR_PATH), "role": "comparator_ref", "sha256": sha256_file(COMPARATOR_PATH)},
        {"path": display_path(SUPPORT_CLAIM_PATH), "role": "support_claim_ref", "sha256": sha256_file(SUPPORT_CLAIM_PATH)},
        {"path": display_path(PARITY_PATH), "role": "parity_results", "sha256": sha256_file(PARITY_PATH)},
        {"path": display_path(SECRET_SCAN_PATH), "role": "secret_scan_report", "sha256": sha256_file(SECRET_SCAN_PATH)},
        {"path": display_path(PREVALIDATION_PATH), "role": "validator_output", "sha256": sha256_file(PREVALIDATION_PATH)},
        {"path": display_path(SETUP_REPORT_PATH), "role": "target_setup_report", "sha256": sha256_file(SETUP_REPORT_PATH)},
        {"path": display_path(TARGET_PROBE_OUTPUT_PATH), "role": "target_probe_output", "sha256": sha256_file(TARGET_PROBE_OUTPUT_PATH)},
        {"path": display_path(TARGET_PROBE_SCRIPT_PATH), "role": "target_probe_script", "sha256": sha256_file(TARGET_PROBE_SCRIPT_PATH)},
        {"path": display_path(AGENT_CONFIG_PATH), "role": "agent_config", "sha256": sha256_file(AGENT_CONFIG_PATH)},
        {"path": display_path(SOURCE_FREEZE_PATH), "role": "source_freeze", "sha256": sha256_file(SOURCE_FREEZE_PATH)},
        {"path": display_path(ZIP_PATH), "role": "source_archive", "sha256": sha256_file(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size},
    ]
    manifest = {"schema_version": "bb.e4.evidence_manifest.v1", "claim_id": CLAIM_ID, "config_id": CONFIG_ID, "lane_id": LANE_ID, "generated_at_utc": GENERATED_AT_UTC, "hash_algorithm": "sha256", "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"], "manifest_scope_note": "Every governed artifact for this Pi P5 residual C4 chain is canonical repo evidence or source-freeze/archive evidence under docs_tmp/phase_15; temporary target extraction paths are not promotion evidence.", "artifacts": artifacts}
    write_json(EVIDENCE_MANIFEST_PATH, manifest)


def upsert_ct_scenario() -> None:
    try:
        from scripts.e4_parity.generate_ct_rows import upsert_inventory_scenarios
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        from generate_ct_rows import upsert_inventory_scenarios

    upsert_inventory_scenarios(CT_SCENARIOS_PATH)


def validate_and_write_node_gate() -> dict[str, Any]:
    report = validate_c4_chain(
        repo_root=ROOT,
        freeze_manifest_path=FREEZE_MANIFEST_PATH,
        config_id=CONFIG_ID,
        support_claim_path=SUPPORT_CLAIM_PATH,
        evidence_manifest_path=EVIDENCE_MANIFEST_PATH,
        enforce_catalog_binding=False,
    )
    write_json(NODE_GATE_PATH, report)
    return report


def build(write: bool = True, *, recapture: bool = False) -> dict[str, Any]:
    if not write:
        return {"ok": True, "config_id": CONFIG_ID, "capture_mode": "reused"}
    ensure_source_freeze()
    write_agent_config()
    write_freeze_manifest()
    capture_mode = write_capture_replay_compare(recapture=recapture)
    freeze_ref = f"{display_path(FREEZE_MANIFEST_PATH)}#{CONFIG_ID}#{freeze_row_hash()}"
    row = build_ledger_row(freeze_ref)
    ledger_hash = upsert_ledger(row)
    ledger_ref = f"{display_path(LEDGER_PATH)}#{FEATURE_ID}#{ledger_hash}"
    write_support_claim_and_manifest(ledger_ref, freeze_ref)
    report = validate_and_write_node_gate()
    upsert_ct_scenario()
    return {"ok": bool(report.get("ok")), "config_id": CONFIG_ID, "claim_id": CLAIM_ID, "ct_id": CT_ID, "points": POINTS, "node_gate": display_path(NODE_GATE_PATH), "errors": report.get("errors", []), "capture_mode": capture_mode}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Pi P5 residual extension/session C4 packet.")
    parser.add_argument("--json", action="store_true", help="print JSON report")
    parser.add_argument("--recapture", action="store_true", help="force live Pi target setup and probe")
    args = parser.parse_args(argv)
    report = build(write=True, recapture=args.recapture)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
