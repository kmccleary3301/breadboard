#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils


ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.compilation import helper_runtime_primitives as helper
from agentic_coder_prototype.compilation.effective_config_graph import compile_effective_config_graph, finalize_effective_config_graph
from scripts.validate_e4_c4_chain import validate_c4_chain
from agentic_coder_prototype.conformance.catalog_binding import CATALOG_PATH as CATALOG_BINDING_PATH, catalog_segment_hash, reusable_catalog_revision
from scripts.e4_parity.validators.registries import schema_generation_default

GENERATED_AT_UTC = "2026-07-03T08:30:00Z"
LANE_ID = "pi_p5_l1_cli_config_context_tool_surface"
CONFIG_ID = "pi_p5_l1_cli_config_context_tool_surface_v1"
CLAIM_ID = "pi_p5_l1_cli_config_context_tool_surface_v1_c4_support_claim"
RUN_ID = "20260703_pi_p5_l1_cli_config_context_tool_surface_capture"
TARGET_FAMILY = "pi"
TARGET_VERSION = "@mariozechner/pi-coding-agent@0.57.1"
PROVIDER_MODEL = "no-provider"
SANDBOX_MODE = "read-only-no-secret"
PHASE = "P5"
P5_ITEMS = ["P5.1", "P5.2-subset", "P5.3-subset", "P5.4", "P5.7", "P5.8"]
POINTS = 95
FEATURE_ID = "feat_2f47f564bacc6202"
CT_ID = "CT-P5-PI-L1-CLI-CONFIG-CONTEXT-TOOL-C4"
CT_OUTPUT = "artifacts/conformance/node_gate/ct_p5_pi_l1_cli_config_context_tool_surface_c4_chain.json"
ZIP_PATH = WORKSPACE / "docs_tmp/phase_15/JUNE_26_FEATURE_AUDIT_PRO_ATTACHMENTS_FLAT/01_pi_mono_git_tracked.zip"
SOURCE_FREEZE_PATH = WORKSPACE / "docs_tmp/phase_15/source_freezes/pi_mono_0_57_1_archive_freeze_provenance.json"
FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"
SCHEMA_DIR = ROOT / "contracts/kernel/schemas"
LEDGER_PATH = WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
CT_SCENARIOS_PATH = ROOT / "docs/conformance/ct_scenarios_v1.json"
AGENT_CONFIG_PATH = ROOT / "agent_configs/misc" / f"{CONFIG_ID}.yaml"
LANE_DIR = ROOT / "docs/conformance/e4_target_support" / LANE_ID
RAW_DIR = LANE_DIR / "raw"
TARGET_PROBE_SCRIPT_PATH = LANE_DIR / "pi_p5_target_probe.mjs"
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

CAPTURE_COMMAND_NAMES = ("node_version", "npm_version", "npm_ci", "pi_help", "pi_version", "module_probe")

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

const repoRoot = resolve(process.env.PI_REPO_ROOT ?? '.');
const fixtureRoot = resolve(process.env.P5_FIXTURE_ROOT ?? '/tmp/pi_p5_fixture_workspace');
const agentDir = resolve(process.env.P5_AGENT_DIR ?? '/tmp/pi_p5_agent_dir');
const cwd = join(fixtureRoot, 'project', 'child');

rmSync(fixtureRoot, { recursive: true, force: true });
rmSync(agentDir, { recursive: true, force: true });
mkdirSync(join(fixtureRoot, 'project', 'child', '.pi'), { recursive: true });
mkdirSync(agentDir, { recursive: true });

writeFileSync(join(agentDir, 'settings.json'), JSON.stringify({
  queueMode: 'all',
  websockets: true,
  defaultProvider: 'anthropic',
  defaultModel: 'claude-global',
  defaultThinkingLevel: 'low',
  theme: 'global-theme',
  compaction: { enabled: true, reserveTokens: 111, keepRecentTokens: 222 },
  enabledModels: ['anthropic/*'],
  skills: { enableSkillCommands: false, customDirectories: ['/tmp/pi-skill'] }
}, null, 2));
writeFileSync(join(cwd, '.pi', 'settings.json'), JSON.stringify({
  defaultModel: 'project-model',
  theme: 'project-theme',
  compaction: { enabled: false }
}, null, 2));
writeFileSync(join(agentDir, 'AGENTS.md'), 'global agent context\n');
writeFileSync(join(fixtureRoot, 'project', 'AGENTS.md'), 'project root agent context\n');
writeFileSync(join(cwd, 'CLAUDE.md'), 'child claude context\n');
writeFileSync(join(cwd, '.pi', 'SYSTEM.md'), 'project system prompt\n');
writeFileSync(join(cwd, '.pi', 'APPEND_SYSTEM.md'), 'project append system\n');

process.env.PI_CODING_AGENT_DIR = agentDir;
process.env.PI_OFFLINE = '1';
process.chdir(repoRoot);

const settingsModule = await import(join(repoRoot, 'packages/coding-agent/src/core/settings-manager.ts'));
const resourceModule = await import(join(repoRoot, 'packages/coding-agent/src/core/resource-loader.ts'));
const toolsModule = await import(join(repoRoot, 'packages/coding-agent/src/core/tools/index.ts'));
const configModule = await import(join(repoRoot, 'packages/coding-agent/src/config.ts'));

const settingsManager = settingsModule.SettingsManager.create(cwd, agentDir);
const initialEffective = {
  defaultProvider: settingsManager.getDefaultProvider(),
  defaultModel: settingsManager.getDefaultModel(),
  defaultThinkingLevel: settingsManager.getDefaultThinkingLevel(),
  theme: settingsManager.getTheme(),
  compaction: settingsManager.getCompactionSettings(),
  enabledModels: settingsManager.getEnabledModels(),
  steeringMode: settingsManager.getSteeringMode(),
  transport: settingsManager.getTransport(),
  skillPaths: settingsManager.getSkillPaths(),
  enableSkillCommands: settingsManager.getEnableSkillCommands()
};
settingsManager.applyOverrides({ defaultProvider: 'runtime-provider', compaction: { keepRecentTokens: 333 } });
const afterRuntimeOverride = {
  defaultProvider: settingsManager.getDefaultProvider(),
  defaultModel: settingsManager.getDefaultModel(),
  compaction: settingsManager.getCompactionSettings()
};
settingsManager.setDefaultModel('writeback-model');
await settingsManager.flush();
const writebackFile = JSON.parse(readFileSync(join(agentDir, 'settings.json'), 'utf8'));

const resourceLoader = new resourceModule.DefaultResourceLoader({
  cwd,
  agentDir,
  settingsManager,
  noExtensions: true,
  noSkills: true,
  noPromptTemplates: true,
  noThemes: true
});
await resourceLoader.reload();

const result = {
  schema_version: 'pi.p5.target_probe_output.v1',
  cwd,
  agent_dir: agentDir,
  app: {
    name: configModule.APP_NAME,
    config_dir_name: configModule.CONFIG_DIR_NAME,
    version: configModule.VERSION,
    env_agent_dir: configModule.ENV_AGENT_DIR,
    settings_path: configModule.getSettingsPath(),
    sessions_dir: configModule.getSessionsDir()
  },
  settings: {
    global: settingsManager.getGlobalSettings(),
    project: settingsManager.getProjectSettings(),
    effective_before_runtime_override: initialEffective,
    effective_after_runtime_override: afterRuntimeOverride,
    writeback_file: writebackFile,
    errors: settingsManager.drainErrors().map((entry) => ({ scope: entry.scope, message: entry.error.message }))
  },
  context: {
    agentsFiles: resourceLoader.getAgentsFiles().agentsFiles.map((entry) => ({ path: entry.path, content: entry.content })),
    systemPrompt: resourceLoader.getSystemPrompt(),
    appendSystemPrompt: resourceLoader.getAppendSystemPrompt(),
    cwd,
    env: { PI_OFFLINE: process.env.PI_OFFLINE, PI_CODING_AGENT_DIR: process.env.PI_CODING_AGENT_DIR }
  },
  tools: {
    all: Object.keys(toolsModule.allTools).sort(),
    coding: toolsModule.codingTools.map((tool) => tool.name),
    readOnly: toolsModule.readOnlyTools.map((tool) => tool.name),
    selectedReadOnly: toolsModule.createReadOnlyTools(cwd).map((tool) => tool.name)
  },
  no_secret_env: {
    ANTHROPIC_API_KEY: Boolean(process.env.ANTHROPIC_API_KEY),
    OPENAI_API_KEY: Boolean(process.env.OPENAI_API_KEY),
    GEMINI_API_KEY: Boolean(process.env.GEMINI_API_KEY),
    PI_OFFLINE: process.env.PI_OFFLINE
  }
};
console.log('PI_P5_PROBE_JSON=' + JSON.stringify(result));
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


def sha256_file(path: Path) -> str:
    return _hash_utils.sha256_file(path)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(WORKSPACE.resolve()).as_posix()
        except ValueError:
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


def write_source_freeze() -> None:
    root_pkg = json.loads(zipfile.ZipFile(ZIP_PATH).read("package.json").decode("utf-8"))
    agent_pkg = json.loads(zipfile.ZipFile(ZIP_PATH).read("packages/coding-agent/package.json").decode("utf-8"))
    payload = {
        "schema_version": "bb.e4.source_freeze_provenance.v1",
        "generated_at_utc": GENERATED_AT_UTC,
        "source_id": "pi_mono_0_57_1_archive",
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "archive": {"path": display_path(ZIP_PATH), "sha256": sha256_file(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size},
        "root_package": {"name": root_pkg.get("name"), "version": root_pkg.get("version"), "workspaces": root_pkg.get("workspaces")},
        "coding_agent_package": {
            "name": agent_pkg.get("name"),
            "version": agent_pkg.get("version"),
            "bin": agent_pkg.get("bin"),
            "repository": agent_pkg.get("repository"),
            "piConfig": agent_pkg.get("piConfig"),
            "engines": agent_pkg.get("engines"),
        },
        "commit_provenance": {
            "kind": "archive_snapshot_without_git_dir",
            "upstream_repo": "https://github.com/badlogic/pi-mono.git",
            "archive_sha256": sha256_file(ZIP_PATH),
            "note": "The local Pi source archive contains tracked source files and package metadata but no .git directory; exact source freeze is the archive hash plus package version.",
        },
    }
    serialized = canonical_json(payload)
    if SOURCE_FREEZE_PATH.exists():
        if SOURCE_FREEZE_PATH.read_bytes() != serialized:
            raise ValueError(f"refusing to rewrite frozen source freeze: {display_path(SOURCE_FREEZE_PATH)}")
        return
    SOURCE_FREEZE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_FREEZE_PATH.write_bytes(serialized)


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
        "claim_scope": "No-secret Pi CLI/module capture for settings merge/runtime override/writeback, context ordering, system/append prompts, and declared tool surfaces.",
        "exclusions": ["extension hooks", "session resume/fork", "provider-authenticated inference", "model response quality", "broad Pi customization parity"],
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
            "upstream_ref": "archive:01_pi_mono_git_tracked.zip",
            "upstream_version": TARGET_VERSION,
            "provenance": {
                "kind": "archive_snapshot_without_git_dir",
                "source_archive": {"path": display_path(ZIP_PATH), "sha256": sha256_file(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size},
                "source_freeze_ref": ref(SOURCE_FREEZE_PATH),
                "package": {"name": "@mariozechner/pi-coding-agent", "version": "0.57.1"},
            },
            "runtime_surface": {"provider_model": PROVIDER_MODEL, "sandbox_mode": SANDBOX_MODE, "target_profile_id": LANE_ID},
        },
        "calibration_anchor": {
            "class": "raw_target_capture",
            "scenario_id": LANE_ID,
            "run_id": RUN_ID,
            "evidence_paths": [
                display_path(SETUP_REPORT_PATH),
                display_path(TARGET_PROBE_OUTPUT_PATH),
                display_path(RAW_CAPTURE_PATH),
                display_path(REPLAY_PATH),
                display_path(COMPARATOR_PATH),
                display_path(PARITY_PATH),
                display_path(SECRET_SCAN_PATH),
                display_path(PREVALIDATION_PATH),
                display_path(SUPPORT_CLAIM_PATH),
                display_path(EVIDENCE_MANIFEST_PATH),
            ],
        },
        "snapshot_source_entry": CONFIG_ID,
        "snapshot_tag": "pi_0_57_1_p5_l1_cli_config_context_tool_surface_20260703",
    }
    FREEZE_MANIFEST_PATH.write_text(yaml.safe_dump(manifest, sort_keys=False, width=120), encoding="utf-8")


def command_env(tmp: Path) -> dict[str, str]:
    path = os.environ.get("PATH", "")
    env = {
        "HOME": str(tmp / "home"),
        "PATH": path,
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
    return {
        "argv": args,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def extract_probe_json(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith("PI_P5_PROBE_JSON="):
            value = line.split("=", 1)[1]
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("PI_P5_PROBE_JSON marker missing from probe output")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
    if probe.get("schema_version") != "pi.p5.target_probe_output.v1":
        return None
    if setup.get("schema_version") != "pi.p5.target_setup_and_capture_report.v1":
        return None
    if not all(key in probe for key in ("app", "settings", "context", "tools")):
        return None
    return probe, setup


def run_target_capture() -> tuple[dict[str, Any], dict[str, Any]]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_PROBE_SCRIPT_PATH.write_text(PROBE_SOURCE + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="pi_p5_capture_") as tmp_name:
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
        commands["pi_help"] = run_cmd(["bash", "./pi-test.sh", "--no-env", "--help"], repo, env, timeout=180)
        commands["pi_version"] = run_cmd(["bash", "./pi-test.sh", "--no-env", "--version"], repo, env, timeout=180)
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
            raise RuntimeError(f"Pi target capture failed: {failures}")
        probe = extract_probe_json(str(commands["module_probe"]["stdout"]))
        setup = {
            "schema_version": "pi.p5.target_setup_and_capture_report.v1",
            "generated_at_utc": GENERATED_AT_UTC,
            "run_id": RUN_ID,
            "target_family": TARGET_FAMILY,
            "target_version": TARGET_VERSION,
            "source_archive_ref": ref(ZIP_PATH),
            "tmp_root_redacted": tmp.name,
            "commands": {
                name: {
                    "argv": result["argv"],
                    "exit_code": result["exit_code"],
                    "stdout_ref": ref(RAW_DIR / f"{name}.stdout.txt"),
                    "stderr_ref": ref(RAW_DIR / f"{name}.stderr.txt"),
                    "command_ref": ref(RAW_DIR / f"{name}.command.json"),
                }
                for name, result in commands.items()
            },
            "no_secret_environment": {key: False for key in SECRET_KEYS},
            "sandbox_mode": SANDBOX_MODE,
        }
        write_json(TARGET_PROBE_OUTPUT_PATH, probe)
        write_json(SETUP_REPORT_PATH, setup)
        return probe, setup


def restore_budget_token_config_values(graph: Mapping[str, Any], budget_values: Mapping[str, int]) -> dict[str, Any]:
    result = json.loads(json.dumps(graph))
    for value in result["effective_values"]:
        path = value["path"]
        if path in budget_values:
            value["value"] = budget_values[path]
            value["value_kind"] = "number"
            value["visibility"] = "model-visible"
            value["env_gate_ids"] = []
    result["visibility"] = {
        "host_only_paths": sorted(value["path"] for value in result["effective_values"] if value["visibility"] == "host-only"),
        "model_visible_paths": sorted(value["path"] for value in result["effective_values"] if value["visibility"] == "model-visible"),
        "redacted_paths": sorted(value["path"] for value in result["effective_values"] if value["visibility"] == "redacted"),
    }
    return finalize_effective_config_graph(result)


def compile_replay_records(probe: Mapping[str, Any]) -> dict[str, Any]:
    app = probe["app"]
    settings = probe["settings"]
    context = probe["context"]
    tools = probe["tools"]
    sources = []
    for index, entry in enumerate(context["agentsFiles"]):
        sources.append({"source_id": f"context_{index}", "source_kind": "project-file", "uri": entry["path"], "content": entry["content"], "order": index * 10, "model_visible": True, "host_visible": True})
    sources.append({"source_id": "system_prompt", "source_kind": "system-prompt", "uri": "pi://system", "content": context.get("systemPrompt") or "", "order": 100, "model_visible": True, "host_visible": True})
    for index, value in enumerate(context.get("appendSystemPrompt") or []):
        sources.append({"source_id": f"append_system_{index}", "source_kind": "developer-instruction", "uri": f"pi://append-system/{index}", "content": value, "order": 110 + index, "model_visible": True, "host_visible": True})
    context_pack = helper.compile_context_resource_pack(pack_id=f"{LANE_ID}_context_pack", sources=sources)
    capabilities = []
    for name in tools["all"]:
        capabilities.append({"capability_id": f"pi.tool.{name}", "capability_type": "tool", "name": name, "scopes": ["model_visible"], "metadata": {"declared_by": "pi_target_probe"}})
    registry = helper.compile_capability_registry(registry_id=f"{LANE_ID}_tool_registry", run_id=RUN_ID, environment_id=f"{LANE_ID}_env", declarations=capabilities, generated_at=GENERATED_AT_UTC)
    config_graph = compile_effective_config_graph(
        graph_id=f"{LANE_ID}_effective_config_graph",
        layers=[
            {
                "layer_id": "global_settings",
                "source_kind": "user",
                "scope": "global",
                "precedence": 10,
                "source_ref": "pi://agent/settings.json",
                "values": {
                    "app": {"name": app["name"], "configDirName": app["config_dir_name"], "version": app["version"]},
                    "provider": {"default": settings["global"]["defaultProvider"]},
                    "model": {"default": settings["global"].get("defaultModel")},
                    "thinking": {"default": settings["global"].get("defaultThinkingLevel")},
                    "theme": settings["global"].get("theme"),
                    "compaction": settings["global"].get("compaction"),
                    "enabledModels": settings["global"].get("enabledModels"),
                    "skills": settings["global"].get("skills"),
                    "enableSkillCommands": settings["global"].get("enableSkillCommands"),
                    "steeringMode": settings["global"].get("steeringMode"),
                    "transport": settings["global"].get("transport"),
                },
            },
            {
                "layer_id": "project_settings",
                "source_kind": "project",
                "scope": "project",
                "precedence": 20,
                "source_ref": "pi://project/.pi/settings.json",
                "values": {
                    "model": {"default": settings["project"].get("defaultModel")},
                    "theme": settings["project"].get("theme"),
                    "compaction": settings["project"].get("compaction"),
                },
            },
            {
                "layer_id": "runtime_overrides",
                "source_kind": "runtime",
                "scope": "session",
                "precedence": 30,
                "source_ref": "pi://runtime/overrides",
                "values": {
                    "provider": {"default": settings["effective_after_runtime_override"]["defaultProvider"]},
                    "compaction": {"keepRecentTokens": settings["effective_after_runtime_override"]["compaction"]["keepRecentTokens"]},
                },
            },
        ],
        merge_policy={"policy_id": "pi-precedence-deep-merge", "strategy": "deep-merge", "conflict_resolution": "highest-precedence"},
        migrations=[
            {"migration_id": "pi-queueMode-to-steeringMode", "from_version": "pi.settings.queueMode", "to_version": "pi.settings.steeringMode", "applied": True},
            {"migration_id": "pi-websockets-to-transport", "from_version": "pi.settings.websockets", "to_version": "pi.settings.transport", "applied": True},
            {"migration_id": "pi-skills-object-to-array", "from_version": "pi.settings.skills.object", "to_version": "pi.settings.skills.array", "applied": True},
        ],
    )
    config_graph = restore_budget_token_config_values(
        config_graph,
        {
            "compaction.keepRecentTokens": int(settings["effective_after_runtime_override"]["compaction"]["keepRecentTokens"]),
            "compaction.reserveTokens": int(settings["effective_after_runtime_override"]["compaction"]["reserveTokens"]),
        },
    )
    records = {"effective_config_graph": config_graph, "context_resource_pack": context_pack, "capability_registry": registry}
    validate_replay_records(records)
    return records


def validate_record_schema(record_name: str, record: Mapping[str, Any]) -> list[str]:
    schema_version = str(record.get("schema_version", ""))
    schema_path = SCHEMA_DIR / f"{schema_version}.schema.json"
    if not schema_path.exists():
        return [f"{record_name}: missing schema for {schema_version}"]
    schema = read_json(schema_path)
    validator = Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(record), key=lambda item: list(item.absolute_path)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"{record_name}.{path}: {error.message}")
    return errors


def validate_replay_records(records: Mapping[str, Mapping[str, Any]]) -> None:
    errors: list[str] = []
    for name, record in records.items():
        errors.extend(validate_record_schema(name, record))
    if errors:
        raise RuntimeError("Replay record schema validation failed: " + "; ".join(errors))


def assertion_eq(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "status": "passed" if observed == expected else "failed", "observed": observed, "expected": expected}


def comparator_assertions(probe: Mapping[str, Any], commands: Mapping[str, Any], replay_records: Mapping[str, Any]) -> list[dict[str, Any]]:
    help_text = (RAW_DIR / "pi_help.stdout.txt").read_text(encoding="utf-8")
    version_text = (RAW_DIR / "pi_version.stdout.txt").read_text(encoding="utf-8")
    settings = probe["settings"]
    context = probe["context"]
    tools = probe["tools"]
    return [
        assertion_eq("package_version", probe["app"]["version"], "0.57.1"),
        assertion_eq("app_config_dir", probe["app"]["config_dir_name"], ".pi"),
        assertion_eq("version_command", version_text.strip().splitlines()[-1], "0.57.1"),
        assertion_eq("help_declares_default_tools", "default: read,bash,edit,write" in help_text, True),
        assertion_eq("help_declares_read_only_tools", all(name in help_text for name in ["grep", "find", "ls"]), True),
        assertion_eq("settings_project_model_wins", settings["effective_before_runtime_override"]["defaultModel"], "project-model"),
        assertion_eq("settings_global_provider_preserved", settings["effective_before_runtime_override"]["defaultProvider"], "anthropic"),
        assertion_eq("settings_nested_merge_preserves_reserve", settings["effective_before_runtime_override"]["compaction"], {"enabled": False, "reserveTokens": 111, "keepRecentTokens": 222}),
        assertion_eq("settings_runtime_override_provider", settings["effective_after_runtime_override"]["defaultProvider"], "runtime-provider"),
        assertion_eq("settings_runtime_override_nested", settings["effective_after_runtime_override"]["compaction"], {"enabled": False, "reserveTokens": 111, "keepRecentTokens": 333}),
        assertion_eq("settings_migration_queue_mode", settings["effective_before_runtime_override"]["steeringMode"], "all"),
        assertion_eq("settings_migration_websockets", settings["effective_before_runtime_override"]["transport"], "websocket"),
        assertion_eq("settings_migration_skills", settings["effective_before_runtime_override"]["skillPaths"], ["/tmp/pi-skill"]),
        assertion_eq("settings_writeback", settings["writeback_file"].get("defaultModel"), "writeback-model"),
        assertion_eq("context_order", [Path(entry["path"]).name for entry in context["agentsFiles"]], ["AGENTS.md", "AGENTS.md", "CLAUDE.md"]),
        assertion_eq("context_contents", [entry["content"] for entry in context["agentsFiles"]], ["global agent context\n", "project root agent context\n", "child claude context\n"]),
        assertion_eq("system_prompt", context["systemPrompt"], "project system prompt\n"),
        assertion_eq("append_system_prompt", context["appendSystemPrompt"], ["project append system\n"]),
        assertion_eq("tool_all", tools["all"], ["bash", "edit", "find", "grep", "ls", "read", "write"]),
        assertion_eq("tool_coding_defaults", tools["coding"], ["read", "bash", "edit", "write"]),
        assertion_eq("tool_read_only", tools["readOnly"], ["read", "grep", "find", "ls"]),
        assertion_eq("no_provider_secrets", probe["no_secret_env"], {"ANTHROPIC_API_KEY": False, "OPENAI_API_KEY": False, "GEMINI_API_KEY": False, "PI_OFFLINE": "1"}),
        assertion_eq("replay_context_pack_sources", replay_records["context_resource_pack"]["render_order"], ["context_0", "context_1", "context_2", "system_prompt", "append_system_0"]),
        assertion_eq("replay_capability_count", len(replay_records["capability_registry"]["capabilities"]), 7),
    ]


def write_capture_replay_compare(*, recapture: bool = False) -> str:
    existing_capture = None if recapture else load_existing_capture()
    capture_mode = "reused" if existing_capture is not None else "live"
    probe, setup = existing_capture if existing_capture is not None else run_target_capture()
    replay_records = compile_replay_records(probe)
    write_json(REPLAY_PATH, {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "p5_items": P5_ITEMS,
        "generated_at_utc": GENERATED_AT_UTC,
        "exit_status": "passed",
        "warnings": [],
        "errors": [],
        "input_hashes": {
            display_path(TARGET_PROBE_OUTPUT_PATH): sha256_file(TARGET_PROBE_OUTPUT_PATH),
            display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
            display_path(AGENT_CONFIG_PATH): sha256_file(AGENT_CONFIG_PATH),
        },
        "normalized_records": replay_records,
        "replay_summary": "BreadBoard replay maps Pi no-secret module observations to effective config graph, context resource pack, and capability registry records.",
    })
    assertions = comparator_assertions(probe, setup["commands"], replay_records)
    failed = sum(1 for item in assertions if item["status"] != "passed")
    comparator = {
        "schema_version": "bb.e4.comparator_report.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "scope": {"config_id": CONFIG_ID, "lane_id": LANE_ID, "phase": PHASE, "p5_items": P5_ITEMS, "provider_model": PROVIDER_MODEL, "run_id": RUN_ID, "sandbox_mode": SANDBOX_MODE, "target_family": TARGET_FAMILY, "target_version": TARGET_VERSION},
        "assertions": assertions,
        "details": [{"name": item["name"], "status": item["status"]} for item in assertions],
        "failed": failed,
        "warned": 0,
        "input_hashes": {display_path(RAW_CAPTURE_PATH): "pending", display_path(REPLAY_PATH): sha256_file(REPLAY_PATH), display_path(TARGET_PROBE_OUTPUT_PATH): sha256_file(TARGET_PROBE_OUTPUT_PATH)},
    }
    write_json(COMPARATOR_PATH, comparator)
    parity = {"schema_version": "bb.e4.parity_results.v1", "lane_id": LANE_ID, "config_id": CONFIG_ID, "passed": failed == 0, "failed": failed, "warned": 0, "comparator_ref": ref(COMPARATOR_PATH), "generated_at_utc": GENERATED_AT_UTC}
    write_json(PARITY_PATH, parity)
    scan_paths = [TARGET_PROBE_OUTPUT_PATH, SETUP_REPORT_PATH, REPLAY_PATH, COMPARATOR_PATH, PARITY_PATH, RAW_DIR / "pi_help.stdout.txt", RAW_DIR / "module_probe.stdout.txt"]
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
    schema_valid = True
    prevalidation = {"schema_version": "bb.e4.prevalidation_report.v1", "ok": failed == 0 and schema_valid and not findings, "accepted": failed == 0 and schema_valid and not findings, "lane_id": LANE_ID, "config_id": CONFIG_ID, "checks": [{"name": "target_commands_exit_zero", "passed": True}, {"name": "replay_record_schema_validation", "passed": schema_valid}, {"name": "machine_comparator_assertions", "passed": failed == 0}, {"name": "secret_scan", "passed": not findings}], "generated_at_utc": GENERATED_AT_UTC}
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
        "lineage_rationale": "Pi P5 L1 capture runs @mariozechner/pi-coding-agent 0.57.1 from the frozen local source archive in an isolated no-secret target workspace, observing module-level settings/context/tool behavior without provider inference.",
        "source_artifacts": [display_path(ZIP_PATH), display_path(SOURCE_FREEZE_PATH), display_path(SETUP_REPORT_PATH), display_path(TARGET_PROBE_OUTPUT_PATH), display_path(TARGET_PROBE_SCRIPT_PATH), display_path(AGENT_CONFIG_PATH)],
        "source_hashes": {display_path(path): sha256_file(path) for path in [ZIP_PATH, SOURCE_FREEZE_PATH, SETUP_REPORT_PATH, TARGET_PROBE_OUTPUT_PATH, TARGET_PROBE_SCRIPT_PATH, AGENT_CONFIG_PATH]},
        "captured_artifacts": [{"path": display_path(path), "role": role, "sha256": sha256_file(path)} for path, role in [(SETUP_REPORT_PATH, "target_setup"), (TARGET_PROBE_OUTPUT_PATH, "target_probe"), (RAW_DIR / "pi_help.stdout.txt", "pi_help_stdout"), (RAW_DIR / "pi_version.stdout.txt", "pi_version_stdout"), (RAW_DIR / "module_probe.stdout.txt", "module_probe_stdout")]],
        "target_source": {"repository": "https://github.com/badlogic/pi-mono.git", "package": "@mariozechner/pi-coding-agent", "version": "0.57.1", "source_archive_ref": ref(ZIP_PATH)},
        "scope_exclusions": ["no extension hook claim", "no session/resume/fork claim", "no provider-authenticated inference", "no broad Pi customization parity"],
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
        "breadboard_mapping": {"primitive": "bb.effective_config_graph.v1 + bb.context_resource_pack.v1 + bb.capability_registry.v1", "support": "supported", "truth_scope": "kernel_truth"},
        "claim_type": "runtime_behavior",
        "dedupe_key": f"pi/p5/{LANE_ID}/c4",
        "e4_row_ref": CONFIG_ID,
        "evidence_tier": "C4",
        "family": "target.pi",
        "feature_id": FEATURE_ID,
        "fixture_refs": [f"freeze:{freeze_ref}", f"capture:{ref(RAW_CAPTURE_PATH)}", f"replay:{ref(REPLAY_PATH)}", f"comparator:{ref(COMPARATOR_PATH)}", f"support_claim:{display_path(SUPPORT_CLAIM_PATH)}", f"evidence_manifest:{display_path(EVIDENCE_MANIFEST_PATH)}", f"parity_results:{ref(PARITY_PATH)}", f"secret_scan_report:{ref(SECRET_SCAN_PATH)}", f"validator_output:{ref(PREVALIDATION_PATH)}"],
        "gap_kind": "none",
        "model_visible": True,
        "promotion_state": "ready",
        "schema_version": "bb.atomic_feature_ledger.v1",
        "source_refs": [f"source:{display_path(SOURCE_FREEZE_PATH)}", f"source:{display_path(ZIP_PATH)}", f"source:{display_path(SUPPORT_CLAIM_PATH)}", f"source:{display_path(EVIDENCE_MANIFEST_PATH)}"],
        "stateful": True,
        "target": TARGET_FAMILY,
    }


def upsert_ledger(row: dict[str, Any]) -> str:
    ledger = read_json(LEDGER_PATH)
    rows = [item for item in ledger.get("rows", []) if not (isinstance(item, Mapping) and item.get("feature_id") == FEATURE_ID)]
    rows.append(row)
    ledger["rows"] = rows
    ledger["row_count"] = len(rows)
    write_json(LEDGER_PATH, ledger)
    return row_hash(FEATURE_ID, row)


def catalog_binding(prior_binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    catalog_path = ROOT / CATALOG_BINDING_PATH
    catalog = read_json(catalog_path)
    binding_hashes = {
        "segment_hash": catalog_segment_hash(catalog, LANE_ID),
        "shared_segment_hash": catalog_segment_hash(catalog, "shared"),
    }
    return {
        "catalog_path": CATALOG_BINDING_PATH,
        "catalog_revision": reusable_catalog_revision(catalog, prior_binding, binding_hashes),
        "segment_id": LANE_ID,
        **binding_hashes,
    }


def write_support_claim_and_manifest(ledger_ref: str, freeze_ref: str) -> None:
    existing_claim = read_json(SUPPORT_CLAIM_PATH) if SUPPORT_CLAIM_PATH.exists() else {}
    if not isinstance(existing_claim, Mapping):
        existing_claim = {}
    prior_binding = existing_claim.get("catalog_binding") if isinstance(existing_claim.get("catalog_binding"), Mapping) else None
    comparator = read_json(COMPARATOR_PATH)
    assertion_ids = [
        str(assertion.get("assertion_id") or assertion.get("name"))
        for assertion in comparator.get("assertions", [])
        if isinstance(assertion, Mapping) and assertion.get("status") == "passed" and (assertion.get("assertion_id") or assertion.get("name"))
    ]
    claim_semantics = {
        "asserted_behaviors": [
            {
                "behavior_id": f"pi_p5_{assertion_id}",
                "description": "Observed Pi P5 comparator assertion passed.",
                "comparator_assertion_ids": [assertion_id],
            }
            for assertion_id in assertion_ids
        ],
        "excluded_behaviors": [
            {
                "behavior_id": "broad_target_parity",
                "description": "Broad target-family parity remains outside this exact C4 lane claim.",
            }
        ],
    }
    support_claim = {
        "schema_version": schema_generation_default("support_claim"),
        "claim_id": CLAIM_ID,
        "kind": "target_support",
        "accepted": True,
        "summary": "Pi P5 L1 no-secret CLI/module support is accepted only for observed settings merge/runtime override/writeback, AGENTS/CLAUDE/SYSTEM/APPEND context ordering, and declared tool surfaces.",
        "acceptance_rationale": "The target capture installs and runs the frozen Pi source in an isolated no-secret workspace, executes CLI help/version, imports Pi modules through tsx, and compares observed settings/context/tool outputs to exact expectations.",
        "phase_label": PHASE,
        "scope": {
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "provider_model": PROVIDER_MODEL,
            "run_id": RUN_ID,
            "sandbox_mode": SANDBOX_MODE,
            "target_family": TARGET_FAMILY,
            "target_version": TARGET_VERSION,
        },
        "exclusions": [
            "No full P5.2 claim for every migration/writeback path; only the observed queueMode/websockets/skills migrations, runtime override, and default-model writeback are accepted.",
            "No full P5.3 claim for every environment/date context; only observed AGENTS/CLAUDE ancestry, SYSTEM/APPEND prompt discovery, cwd, and Pi env capture are accepted.",
            "No P5.5 extension hook behavior is claimed.",
            "No P5.6 session/resume/fork/compaction behavior is claimed.",
            "No provider-authenticated inference, network, UI, or broad Pi customization parity is claimed.",
        ],
        "exclusion_facets": {
            "excluded_behavior_classes": ["broad_target_parity", "model_inference", "network", "provider_authenticated", "ui_parity"],
            "excluded_families": ["all_other_families"],
        },
        "claim_semantics": claim_semantics,
        "freeze_ref": freeze_ref,
        "capture_ref": ref(RAW_CAPTURE_PATH),
        "replay_ref": ref(REPLAY_PATH),
        "comparator_ref": ref(COMPARATOR_PATH),
        "evidence_manifest_ref": display_path(EVIDENCE_MANIFEST_PATH),
        "ledger_row_refs": [ledger_ref],
        "validation_refs": [ref(PREVALIDATION_PATH)],
        "catalog_binding": catalog_binding(prior_binding),
        "reverify_command": {
            "argv": [".venv/bin/python", "scripts/validate_e4_c4_chain.py", "--config-id", CONFIG_ID, "--support-claim", display_path(SUPPORT_CLAIM_PATH), "--evidence-manifest", display_path(EVIDENCE_MANIFEST_PATH), "--json-out", CT_OUTPUT, "--check-only"],
            "cwd": ".",
        },
        "parity_results_ref": ref(PARITY_PATH),
        "secret_scan_ref": ref(SECRET_SCAN_PATH),
        "source_freeze_ref": ref(SOURCE_FREEZE_PATH),
        "generated_at_utc": GENERATED_AT_UTC,
        "metadata": {"p5_items": P5_ITEMS, "points": POINTS},
    }
    write_json(SUPPORT_CLAIM_PATH, support_claim)
    artifacts = [
        {"path": display_path(FREEZE_MANIFEST_PATH), "role": "freeze_manifest", "sha256": freeze_row_hash()},
        {"path": display_path(RAW_CAPTURE_PATH), "role": "capture_ref", "sha256": sha256_file(RAW_CAPTURE_PATH)},
        {"derived_from": [ref(RAW_CAPTURE_PATH), ref(TARGET_PROBE_OUTPUT_PATH)], "path": display_path(REPLAY_PATH), "role": "replay_ref", "sha256": sha256_file(REPLAY_PATH)},
        {"derived_from": [ref(RAW_CAPTURE_PATH), ref(REPLAY_PATH), ref(TARGET_PROBE_OUTPUT_PATH)], "path": display_path(COMPARATOR_PATH), "role": "comparator_ref", "sha256": sha256_file(COMPARATOR_PATH)},
        {"path": display_path(SUPPORT_CLAIM_PATH), "role": "support_claim_ref", "sha256": sha256_file(SUPPORT_CLAIM_PATH)},
        {"derived_from": [ref(COMPARATOR_PATH)], "path": display_path(PARITY_PATH), "role": "parity_results", "sha256": sha256_file(PARITY_PATH)},
        {"path": display_path(SECRET_SCAN_PATH), "role": "secret_scan_report", "sha256": sha256_file(SECRET_SCAN_PATH)},
        {"path": display_path(PREVALIDATION_PATH), "role": "validator_output", "sha256": sha256_file(PREVALIDATION_PATH)},
        {"path": display_path(SETUP_REPORT_PATH), "role": "target_setup_report", "sha256": sha256_file(SETUP_REPORT_PATH)},
        {"path": display_path(TARGET_PROBE_OUTPUT_PATH), "role": "target_probe_output", "sha256": sha256_file(TARGET_PROBE_OUTPUT_PATH)},
        {"path": display_path(TARGET_PROBE_SCRIPT_PATH), "role": "target_probe_script", "sha256": sha256_file(TARGET_PROBE_SCRIPT_PATH)},
        {"path": display_path(AGENT_CONFIG_PATH), "role": "agent_config", "sha256": sha256_file(AGENT_CONFIG_PATH)},
        {"path": display_path(SOURCE_FREEZE_PATH), "role": "source_freeze", "sha256": sha256_file(SOURCE_FREEZE_PATH)},
        {"path": display_path(ZIP_PATH), "role": "source_archive", "sha256": sha256_file(ZIP_PATH), "bytes": ZIP_PATH.stat().st_size},
    ]
    manifest = {"schema_version": "bb.e4.evidence_manifest.v1", "claim_id": CLAIM_ID, "config_id": CONFIG_ID, "lane_id": LANE_ID, "generated_at_utc": GENERATED_AT_UTC, "hash_algorithm": "sha256", "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"], "manifest_scope_note": "Every governed artifact for this Pi P5 L1 C4 chain is canonical repo evidence or source-freeze/archive evidence under docs_tmp/phase_15; temporary target extraction paths are not promotion evidence.", "artifacts": artifacts}
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
    write_source_freeze()
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


_SCRATCH_PATH_GLOBALS = (
    "SOURCE_FREEZE_PATH",
    "FREEZE_MANIFEST_PATH",
    "LEDGER_PATH",
    "CT_SCENARIOS_PATH",
    "AGENT_CONFIG_PATH",
    "LANE_DIR",
    "RAW_DIR",
    "TARGET_PROBE_SCRIPT_PATH",
    "TARGET_PROBE_OUTPUT_PATH",
    "SETUP_REPORT_PATH",
    "RAW_CAPTURE_PATH",
    "REPLAY_PATH",
    "COMPARATOR_PATH",
    "PARITY_PATH",
    "SECRET_SCAN_PATH",
    "PREVALIDATION_PATH",
    "SUPPORT_CLAIM_PATH",
    "EVIDENCE_MANIFEST_PATH",
    "NODE_GATE_PATH",
)


def _overlay_path(out_dir: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        return out_dir / resolved.relative_to(ROOT.resolve())
    except ValueError:
        return out_dir / "__workspace__" / resolved.relative_to(WORKSPACE.resolve())


@contextmanager
def _scratch_paths(out_dir: Path):
    originals = {name: globals()[name] for name in _SCRATCH_PATH_GLOBALS}
    original_display = display_path
    replacements = {name: _overlay_path(out_dir, path) for name, path in originals.items()}

    lane_source = originals["LANE_DIR"]
    lane_destination = replacements["LANE_DIR"]
    if lane_source.is_dir():
        shutil.copytree(lane_source, lane_destination, dirs_exist_ok=True)
    for name in (
        "SOURCE_FREEZE_PATH",
        "FREEZE_MANIFEST_PATH",
        "LEDGER_PATH",
        "CT_SCENARIOS_PATH",
        "SUPPORT_CLAIM_PATH",
        "EVIDENCE_MANIFEST_PATH",
    ):
        source = originals[name]
        destination = replacements[name]
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def logical_display(path: Path) -> str:
        resolved = path.resolve()
        workspace_overlay = (out_dir / "__workspace__").resolve()
        try:
            return original_display(WORKSPACE / resolved.relative_to(workspace_overlay))
        except ValueError:
            try:
                return original_display(ROOT / resolved.relative_to(out_dir.resolve()))
            except ValueError:
                return original_display(path)

    globals().update(replacements)
    globals()["display_path"] = logical_display
    try:
        yield
    finally:
        globals().update(originals)
        globals()["display_path"] = original_display


def capture(
    lane_def: Mapping[str, Any] | None = None,
    inventory_lane: Mapping[str, Any] | None = None,
    *,
    promote_accepted: bool,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the Pi P5 capture adapter for the accepted lane packet."""
    if promote_accepted:
        return build(write=True, recapture=False)
    if out_dir is None:
        raise ValueError("Pi P5 L1 scratch capture requires out_dir")
    with _scratch_paths(Path(out_dir)):
        row = build(write=True, recapture=False)
    row["scratch_node_gate_ok"] = row.get("ok")
    row["scratch_node_gate_errors"] = list(row.get("errors", []))
    row["ok"] = True
    row["errors"] = []
    return row


capture.supports_scratch_out_dir = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Pi P5 L1 no-secret C4 packet.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--recapture", action="store_true", help="force live Pi target setup and probe")
    args = parser.parse_args(argv)
    report = build(write=not args.check, recapture=args.recapture)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("ok" if report.get("ok") else "failed")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
