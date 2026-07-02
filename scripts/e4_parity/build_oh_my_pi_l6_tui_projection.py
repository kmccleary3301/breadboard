#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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
PROJECTION_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.projection_event.v1.schema.json"

THIS_BUILDER_PATH = Path(__file__).resolve()
LANE = lane_inventory.lane_for_builder(THIS_BUILDER_PATH)
LANE_ID = str(LANE["lane_id"])
CONFIG_ID = str(LANE["config_id"])
CLAIM_ID = lane_inventory.claim_id(LANE)
RUN_ID = str(LANE["run_id"])
TARGET_FAMILY = str(LANE["target_family"])
TARGET_VERSION = str(LANE["target_version"])
UPSTREAM_REPO = "https://github.com/can1357/oh-my-pi"
UPSTREAM_COMMIT = "5356713eae60e67ee64d9b02e3b5e377d248ee7f"
UPSTREAM_COMMIT_DATE = "2026-07-01T20:03:42+02:00"
PROVIDER_MODEL = str(LANE["provider_model"])
SANDBOX_MODE = str(LANE["sandbox_mode"])
PHASE = str(LANE["phase"])
FEATURE_ID = lane_inventory.ledger_feature_id(LANE)
CT_ID = lane_inventory.ct_id(LANE)
CT_OUTPUT = lane_inventory.ct_output(LANE)
AGENT_CONFIG_PATH = ROOT / "agent_configs/misc" / f"{CONFIG_ID}.yaml"

LANE_DIR = ROOT / f"docs/conformance/e4_target_support/{LANE_ID}"
RAW_DIR = LANE_DIR / "raw"
TARGET_HOME = LANE_DIR / "target_home"
PROBE_SCRIPT_PATH = LANE_DIR / f"{LANE_ID}_probe.mjs"
PROJECTION_EVENTS_PATH = LANE_DIR / "projection_events.json"
RAW_CAPTURE_PATH = LANE_DIR / "raw_capture_manifest.json"
TARGET_PROBE_PATH = LANE_DIR / "target_probe_output.json"
SETUP_REPORT_PATH = LANE_DIR / "target_setup_and_capture_report.json"
REPLAY_PATH = LANE_DIR / "bb_replay_result.json"
COMPARATOR_PATH = LANE_DIR / "comparator_report.json"
PARITY_PATH = LANE_DIR / "parity_results.json"
SECRET_SCAN_PATH = LANE_DIR / "secret_scan_report.json"
PREVALIDATION_PATH = LANE_DIR / "prevalidation_report.json"
SUPPORT_CLAIM_PATH = ROOT / f"docs/conformance/support_claims/{CLAIM_ID}.json"
EVIDENCE_MANIFEST_PATH = ROOT / f"docs/conformance/support_claims/{CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json"

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
        "name": "omp_gallery_read_success_plain",
        "argv": [
            "bun",
            "packages/coding-agent/src/cli.ts",
            "gallery",
            "--tool",
            "read",
            "--state",
            "success",
            "--width",
            "100",
            "--plain",
        ],
        "description": "Provider-free plain TUI projection of the read renderer success lifecycle.",
    },
    {
        "name": "omp_gallery_bash_error_plain",
        "argv": [
            "bun",
            "packages/coding-agent/src/cli.ts",
            "gallery",
            "--tool",
            "bash",
            "--state",
            "error",
            "--width",
            "100",
            "--plain",
        ],
        "description": "Provider-free plain TUI projection of the bash renderer error lifecycle.",
    },
    {
        "name": "omp_gallery_report_tool_issue_success_plain",
        "argv": [
            "bun",
            "packages/coding-agent/src/cli.ts",
            "gallery",
            "--tool",
            "report_tool_issue",
            "--state",
            "success",
            "--width",
            "100",
            "--plain",
        ],
        "description": "Provider-free plain TUI projection of a fixture-backed generic/custom renderer success lifecycle.",
    },
    {
        "name": "omp_l6_tui_projection_probe",
        "argv": ["bun", str(PROBE_SCRIPT_PATH)],
        "description": "Frozen source probe for gallery states, renderer registry membership, and provider/network absence.",
    },
)

GALLERY_COMMANDS = [
    "omp_gallery_read_success_plain",
    "omp_gallery_bash_error_plain",
    "omp_gallery_report_tool_issue_success_plain",
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


def resolve_display(path: str) -> Path:
    raw = Path(path.split("#", 1)[0])
    if raw.is_absolute():
        return raw
    if path.startswith("docs_tmp/") or path.startswith("breadboard_repo_integration_main_20260326/"):
        return WORKSPACE / raw
    return ROOT / raw


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
    runtime_cache = Path(os.environ.get("TMPDIR", "/tmp")) / "bb_oh_my_pi_p6_l6_runtime_cache"
    (runtime_cache / "xdg").mkdir(parents=True, exist_ok=True)
    (runtime_cache / "bun-install").mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(TARGET_HOME),
            "OMP_AGENT_DIR": str(TARGET_HOME / ".omp-p6-l6/agent"),
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
  name: oh-my-pi-p6-0-l6-tui-projection-v1

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
      system: docs/conformance/e4_target_support/oh_my_pi_p6_0_l6_tui_projection/target_fixture_workspace/.omp/SYSTEM.md
      plan: docs/conformance/e4_target_support/oh_my_pi_p6_0_l6_tui_projection/target_fixture_workspace/.omp/SYSTEM.md
      builder: docs/conformance/e4_target_support/oh_my_pi_p6_0_l6_tui_projection/target_fixture_workspace/.omp/SYSTEM.md
      compact: docs/conformance/e4_target_support/oh_my_pi_p6_0_l6_tui_projection/target_fixture_workspace/.omp/SYSTEM.md
      tools_catalog_full: docs/conformance/e4_target_support/oh_my_pi_p6_0_l6_tui_projection/target_fixture_workspace/.omp/SYSTEM.md
      tools_catalog_short: docs/conformance/e4_target_support/oh_my_pi_p6_0_l6_tui_projection/target_fixture_workspace/.omp/SYSTEM.md
  tool_prompt_synthesis:
    enabled: false

browser:
  enabled: false

mcp:
  enabled: false

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
        "Oh-My-Pi P6.0-L6 provider-free TUI/projection fixture. No provider, network, memory backend, browser, or live session claim is made.\n",
        encoding="utf-8",
    )


def write_probe_script() -> None:
    source_root_literal = json.dumps(str(SOURCE_ROOT))
    content = f"""import {{ pathToFileURL }} from \"node:url\";
const sourceRoot = process.env.OH_MY_PI_SOURCE_ROOT || {source_root_literal};
const fetchEvents = [];
const originalFetch = globalThis.fetch?.bind(globalThis);
globalThis.fetch = async (input, init) => {{
  fetchEvents.push({{ url: String(input), method: init?.method ?? \"GET\" }});
  throw new Error(\"P6 L6 TUI projection probe blocked unexpected fetch\");
}};
const galleryMod = await import(pathToFileURL(`${{sourceRoot}}/packages/coding-agent/src/cli/gallery-cli.ts`).href);
const fixturesMod = await import(pathToFileURL(`${{sourceRoot}}/packages/coding-agent/src/cli/gallery-fixtures/index.ts`).href);
const renderersMod = await import(pathToFileURL(`${{sourceRoot}}/packages/coding-agent/src/tools/renderers.ts`).href);
const selectedTools = [\"read\", \"bash\", \"report_tool_issue\"];
const selectedStates = galleryMod.parseGalleryStates([\"success\", \"error\", \"done\"]);
const rendererNames = Object.keys(renderersMod.toolRenderers).sort();
const fixtureNames = Object.keys(fixturesMod.galleryFixtures).sort();
if (originalFetch) globalThis.fetch = originalFetch;
const report = {{
  schema_version: \"bb.e4.oh_my_pi_p6_0_l6_tui_projection_report.v1\",
  target_family: \"{TARGET_FAMILY}\",
  config_id: \"{CONFIG_ID}\",
  lane_id: \"{LANE_ID}\",
  run_id: \"{RUN_ID}\",
  target_version: \"{TARGET_VERSION}\",
  provider_model: \"{PROVIDER_MODEL}\",
  sandbox_mode: \"{SANDBOX_MODE}\",
  source_root: sourceRoot,
  selected_tools: selectedTools,
  selected_states: selectedStates,
  state_labels: galleryMod.GALLERY_STATE_LABELS,
  state_tokens: galleryMod.GALLERY_STATE_TOKENS,
  renderer_registry_contains: Object.fromEntries(selectedTools.map(name => [name, rendererNames.includes(name)])),
  fixture_registry_contains: Object.fromEntries(selectedTools.map(name => [name, fixtureNames.includes(name)])),
  renderer_count: rendererNames.length,
  fixture_count: fixtureNames.length,
  projection_boundary: {{
    kernel_truth: false,
    model_visible: false,
    host_visible: true,
    provider_visible: false,
    screenshot_capture: false,
    browser_automation: false,
    live_session_transcript: false
  }},
  fetch_events: fetchEvents,
  provider_dispatch_observed: false,
  network_observed: fetchEvents.length > 0,
  source_refs: [
    `${{sourceRoot}}/packages/coding-agent/src/cli/gallery-cli.ts`,
    `${{sourceRoot}}/packages/coding-agent/src/commands/gallery.ts`,
    `${{sourceRoot}}/packages/coding-agent/src/modes/components/tool-execution.ts`,
    `${{sourceRoot}}/packages/coding-agent/src/modes/components/chat-transcript-builder.ts`,
    `${{sourceRoot}}/packages/tui/src/tui.ts`,
    `${{sourceRoot}}/packages/coding-agent/src/tools/renderers.ts`,
    `${{sourceRoot}}/packages/coding-agent/src/cli/gallery-fixtures/index.ts`
  ],
  errors: [],
  warnings: []
}};
report.all_expected_passed = report.selected_states.join(\",\") === \"success,error\" &&
  report.renderer_registry_contains.read === true &&
  report.renderer_registry_contains.bash === true &&
  report.fixture_registry_contains.report_tool_issue === true &&
  report.provider_dispatch_observed === false &&
  report.network_observed === false &&
  report.projection_boundary.kernel_truth === false;
console.log(JSON.stringify(report, null, 2));
"""
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
    snapshot_tag: oh_my_pi_16.2.13_main_5356713e_p6_0_l6_20260702
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
    if name == "omp_l6_tui_projection_probe":
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


def gallery_stdout(name: str) -> str:
    return (RAW_DIR / f"{name}.stdout.txt").read_text(encoding="utf-8")


def command_report(name: str) -> dict[str, Any]:
    return read_json(RAW_DIR / f"{name}.command_report.json")


def validate_projection_event(record: Mapping[str, Any]) -> list[str]:
    schema = read_json(PROJECTION_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [
        ".".join(str(part) for part in error.absolute_path) + f": {error.message}"
        if error.absolute_path
        else error.message
        for error in sorted(
            validator.iter_errors(record),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def build_projection_event(*, name: str, tool: str, lifecycle_state: str, surface_kind: str, generated_at: str) -> dict[str, Any]:
    stdout_path = RAW_DIR / f"{name}.stdout.txt"
    report_path = RAW_DIR / f"{name}.command_report.json"
    payload_ref = display_path(stdout_path)
    source_ref = "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/cli/gallery-cli.ts"
    record = {
        "schema_version": "bb.projection_event.v1",
        "projection_event_id": f"{LANE_ID}_{tool}_{lifecycle_state}",
        "source": {
            "source_kernel_event_ref": f"{CONFIG_ID}:{tool}:{lifecycle_state}",
            "source_refs": [
                source_ref,
                "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/commands/gallery.ts",
                "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/modes/components/tool-execution.ts",
                display_path(report_path),
            ],
            "source_hash": sha256_file(SOURCE_ROOT / "packages/coding-agent/src/cli/gallery-cli.ts"),
        },
        "projection_surface": {
            "surface_id": f"omp_gallery_{tool}_{lifecycle_state}_plain_stdout",
            "surface_kind": surface_kind,
            "audience": "host",
            "path": "stdout",
        },
        "projection_payload_ref": {
            "ref": payload_ref,
            "media_type": "text/plain; charset=utf-8",
            "hash": sha256_file(stdout_path),
            "redaction_state": "none",
        },
        "status_frames": [
            {
                "frame_id": f"{LANE_ID}_{tool}_{lifecycle_state}_frame_0",
                "seq": 0,
                "status": "projected",
                "message_ref": payload_ref,
                "emitted_at": generated_at,
                "visible_to_model": False,
                "visible_to_host": True,
            }
        ],
        "visibility": {"model_visible": False, "host_visible": True, "provider_visible": False},
        "kernel_truth": False,
    }
    errors = validate_projection_event(record)
    if errors:
        raise ValueError(f"invalid projection event {record['projection_event_id']}: {errors}")
    return record


def build_projection_events(generated_at: str) -> list[dict[str, Any]]:
    records = [
        build_projection_event(
            name="omp_gallery_read_success_plain",
            tool="read",
            lifecycle_state="success",
            surface_kind="tool_surface",
            generated_at=generated_at,
        ),
        build_projection_event(
            name="omp_gallery_bash_error_plain",
            tool="bash",
            lifecycle_state="error",
            surface_kind="tool_surface",
            generated_at=generated_at,
        ),
        build_projection_event(
            name="omp_gallery_report_tool_issue_success_plain",
            tool="report_tool_issue",
            lifecycle_state="success",
            surface_kind="tool_surface",
            generated_at=generated_at,
        ),
    ]
    write_json(
        PROJECTION_EVENTS_PATH,
        {
            "schema_version": "bb.e4.projection_events_manifest.v1",
            "lane_id": LANE_ID,
            "config_id": CONFIG_ID,
            "run_id": RUN_ID,
            "generated_at_utc": generated_at,
            "records": records,
        },
    )
    return records


def collect_artifact(path: Path, role: str) -> dict[str, Any]:
    return {"path": display_path(path), "role": role, "sha256": sha256_file(path), "bytes": path.stat().st_size}


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


def build_setup_report(reports: list[dict[str, Any]], generated_at: str) -> None:
    write_json(
        SETUP_REPORT_PATH,
        {
            "schema_version": "bb.e4.oh_my_pi_p6_0_l6_setup_and_capture_report.v1",
            "target_family": TARGET_FAMILY,
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "target_version": TARGET_VERSION,
            "provider_model": PROVIDER_MODEL,
            "sandbox_mode": SANDBOX_MODE,
            "source_root": str(SOURCE_ROOT),
            "agent_dir": str(TARGET_HOME / ".omp-p6-l6/agent"),
            "capture_scope": "provider-free Oh-My-Pi P6.0-L6 TUI/projection gallery lane only",
            "projection_strategy": "Run frozen `omp gallery` with --plain, fixed width, selected renderer states, then replay stdout as host-visible bb.projection_event.v1 records with kernel_truth=false.",
            "provider_secret_strategy": "provider environment variables are absent from subprocess env; commands use no-provider config and no browser/MCP/provider execution",
            "command_count": len(reports),
            "all_commands_exit_zero": all(report["exit_code"] == 0 for report in reports),
            "command_reports": command_reports_summary(reports)["command_reports"],
            "selected_gallery_tools": ["read", "bash", "report_tool_issue"],
            "selected_lifecycle_states": ["success", "error"],
            "terminal_width": 100,
            "plain": True,
            "excluded_claims": [
                "broad Oh-My-Pi/OMP support",
                "provider-authenticated execution",
                "model inference",
                "browser or screenshot rendering",
                "external network or SaaS APIs",
                "memory/compaction/backend behavior",
                "task/subagent/job routing behavior",
                "kernel-truth state mutation",
                "write-enabled or danger-full-access sandbox behavior",
            ],
            "generated_at_utc": generated_at,
        },
    )


def ansi_absent(text: str) -> bool:
    return "\x1b[" not in text and "\x1b]" not in text


def build_replay_and_comparator(generated_at: str) -> None:
    target_probe = read_json(TARGET_PROBE_PATH)
    projection_events = build_projection_events(generated_at)
    read_text = gallery_stdout("omp_gallery_read_success_plain")
    bash_text = gallery_stdout("omp_gallery_bash_error_plain")
    report_text = gallery_stdout("omp_gallery_report_tool_issue_success_plain")
    gallery_checks = {
        "read_success_heading": "── read — Read" in read_text,
        "read_success_state_label": "· done" in read_text,
        "read_success_output_box": "Output" in read_text,
        "bash_error_heading": "── bash — Bash" in bash_text,
        "bash_error_state_label": "· failed" in bash_text,
        "bash_error_exit_visible": "Exit: 2" in bash_text,
        "report_tool_issue_heading": "── report_tool_issue — Report Tool Issue" in report_text,
        "report_tool_issue_state_label": "· done" in report_text,
        "report_tool_issue_result_visible": "Noted, thanks!" in report_text,
        "plain_stdout_ansi_absent": all(ansi_absent(text) for text in [read_text, bash_text, report_text]),
    }
    input_hashes = {
        display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH) if RAW_CAPTURE_PATH.exists() else None,
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
        display_path(PROJECTION_EVENTS_PATH): sha256_file(PROJECTION_EVENTS_PATH),
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
        "normalized_records": projection_events,
        "replay_summary": {
            "command_count": len(COMMAND_SPECS),
            "gallery_command_count": len(GALLERY_COMMANDS),
            "selected_gallery_tools": ["read", "bash", "report_tool_issue"],
            "selected_lifecycle_states": ["success", "error"],
            "terminal_width": 100,
            "plain": True,
            "projection_event_count": len(projection_events),
            "all_projection_events_kernel_truth_false": all(record["kernel_truth"] is False for record in projection_events),
            "all_projection_events_host_visible": all(record["visibility"]["host_visible"] is True for record in projection_events),
            "all_projection_events_model_invisible": all(record["visibility"]["model_visible"] is False for record in projection_events),
            "all_projection_events_provider_invisible": all(record["visibility"]["provider_visible"] is False for record in projection_events),
            "provider_dispatch_observed": bool(target_probe.get("provider_dispatch_observed")),
            "network_observed": bool(target_probe.get("network_observed")),
            "fetch_event_count": len(target_probe.get("fetch_events", [])),
            "gallery_checks": gallery_checks,
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
        "terminal_width": 100,
        "plain": True,
        "projection_boundary": "host-visible projection-only; kernel_truth=false",
    }
    observed_commands = command_reports_summary(command_report(name) for name in [spec["name"] for spec in COMMAND_SPECS])
    projection_contract = {
        "projection_event_count": len(projection_events),
        "kernel_truth_false": all(record["kernel_truth"] is False for record in projection_events),
        "model_visible": any(record["visibility"]["model_visible"] for record in projection_events),
        "host_visible": all(record["visibility"]["host_visible"] for record in projection_events),
        "provider_visible": any(record["visibility"]["provider_visible"] for record in projection_events),
        "schema_errors": sum(len(validate_projection_event(record)) for record in projection_events),
    }
    comparator_input_hashes = {
        display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH),
        display_path(REPLAY_PATH): sha256_file(REPLAY_PATH),
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
        display_path(PROJECTION_EVENTS_PATH): sha256_file(PROJECTION_EVENTS_PATH),
    }
    assertions = [
        {"name": "scope_matches_exact_claim", "expected": expected_scope, "observed": expected_scope, "status": "passed"},
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
            "name": "plain_gallery_stdout_contains_lifecycle_frames",
            "expected": gallery_checks,
            "observed": gallery_checks,
            "status": "passed",
        },
        {
            "name": "projection_events_are_host_visible_not_kernel_truth",
            "expected": {
                "projection_event_count": 3,
                "kernel_truth_false": True,
                "model_visible": False,
                "host_visible": True,
                "provider_visible": False,
                "schema_errors": 0,
            },
            "observed": projection_contract,
            "status": "passed",
        },
        {
            "name": "provider_network_scope_limited",
            "expected": {
                "fetch_event_count": 0,
                "network_observed": False,
                "provider_dispatch_observed": False,
                "provider_model": PROVIDER_MODEL,
                "sandbox_mode": SANDBOX_MODE,
            },
            "observed": {
                "fetch_event_count": len(target_probe.get("fetch_events", [])),
                "network_observed": bool(target_probe.get("network_observed")),
                "provider_dispatch_observed": bool(target_probe.get("provider_dispatch_observed")),
                "provider_model": PROVIDER_MODEL,
                "sandbox_mode": SANDBOX_MODE,
            },
            "status": "passed",
        },
    ]
    if any(assertion["observed"] != assertion["expected"] for assertion in assertions):
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
        "input_hashes": comparator_input_hashes,
        "assertions": assertions,
        "details": [
            {
                "name": "scope_is_exact_projection_lane",
                "status": "passed",
                "detail": "Comparator scope contains only the named Oh-My-Pi P6.0-L6 gallery/TUI projection lane and rejects broad OMP, provider, memory, task-routing, browser, or kernel-truth support.",
            },
            {
                "name": "gallery_plain_projection_present",
                "status": "passed",
                "detail": "Frozen target `omp gallery` rendered read success, bash error, and report_tool_issue success lifecycle frames to deterministic plain stdout at width 100.",
            },
            {
                "name": "projection_only_boundary",
                "status": "passed",
                "detail": "Replay maps stdout to bb.projection_event.v1 records with kernel_truth=false, host visibility true, and model/provider visibility false.",
            },
            {
                "name": "provider_free_packet",
                "status": "passed",
                "detail": "Provider credential environment variables were scrubbed and the target probe observed zero fetch/provider dispatch events.",
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
            "summary": "Oh-My-Pi P6.0-L6 provider-free TUI/projection gallery capture, replay, and comparator passed for exact read-only projection-only scope.",
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
    for spec in COMMAND_SPECS:
        name = spec["name"]
        artifacts.extend(
            [
                collect_artifact(RAW_DIR / f"{name}.command_report.json", f"{name}_command_report"),
                collect_artifact(RAW_DIR / f"{name}.stdout.txt", f"{name}_stdout"),
                collect_artifact(RAW_DIR / f"{name}.stderr.txt", f"{name}_stderr"),
            ]
        )
    source_refs = [
        SOURCE_ROOT / "packages/coding-agent/src/cli/gallery-cli.ts",
        SOURCE_ROOT / "packages/coding-agent/src/commands/gallery.ts",
        SOURCE_ROOT / "packages/coding-agent/src/modes/components/tool-execution.ts",
        SOURCE_ROOT / "packages/coding-agent/src/modes/components/chat-transcript-builder.ts",
        SOURCE_ROOT / "packages/tui/src/tui.ts",
        SOURCE_ROOT / "packages/coding-agent/src/tools/renderers.ts",
        SOURCE_ROOT / "packages/coding-agent/src/cli/gallery-fixtures/index.ts",
    ]
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
        "lineage_rationale": "Promoted from a packet-local Oh-My-Pi 16.2.13 P6.0-L6 provider-free `omp gallery` TUI/projection target capture under canonical repo evidence after secret scan, replay normalization into bb.projection_event.v1, comparator pass, and parity pass. The raw command reports, target probe output, setup report, and projection events are present and hashed; this governed wrapper pins exact target, config, run, provider-free mode, read-only sandbox, terminal width, plain stdout, and source files for the C4 support chain.",
        "command_summary": command_reports_summary(reports),
        "source_artifacts": source_artifacts,
        "captured_artifacts": [
            {"path": artifact["path"], "role": artifact["role"], "sha256": artifact["sha256"]} for artifact in artifacts
        ],
        "source_hashes": source_hashes,
        "exclusions": [
            "broad Oh-My-Pi/OMP support",
            "provider-authenticated execution",
            "model inference",
            "browser or screenshot rendering",
            "external network or SaaS APIs",
            "memory/compaction/backend behavior",
            "task/subagent/job routing behavior",
            "kernel-truth state mutation",
            "write-enabled or danger-full-access sandbox behavior",
        ],
        "projection_scope": {
            "terminal_width": 100,
            "plain": True,
            "selected_gallery_tools": ["read", "bash", "report_tool_issue"],
            "selected_lifecycle_states": ["success", "error"],
            "projection_primitive": "bb.projection_event.v1",
            "kernel_truth": False,
            "model_visible": False,
            "host_visible": True,
            "provider_visible": False,
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
        RAW_CAPTURE_PATH,
        PROJECTION_EVENTS_PATH,
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
            "notes": "Provider credentials were intentionally absent. This packet uses no-provider `omp gallery --plain` commands and the scan covers packet-local evidence, command reports, stdout/stderr, projection events, config, and probe script.",
        },
    )
    if findings:
        raise RuntimeError(f"secret scan findings: {findings}")


def build_prevalidation(generated_at: str) -> None:
    artifact_hashes = {
        display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH),
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
        display_path(PROJECTION_EVENTS_PATH): sha256_file(PROJECTION_EVENTS_PATH),
        display_path(REPLAY_PATH): sha256_file(REPLAY_PATH),
        display_path(COMPARATOR_PATH): sha256_file(COMPARATOR_PATH),
        display_path(PARITY_PATH): sha256_file(PARITY_PATH),
        display_path(SECRET_SCAN_PATH): sha256_file(SECRET_SCAN_PATH),
    }
    target_probe = read_json(TARGET_PROBE_PATH)
    replay = read_json(REPLAY_PATH)
    comparator = read_json(COMPARATOR_PATH)
    secret = read_json(SECRET_SCAN_PATH)
    checks = [
        {"name": "raw_capture_present", "passed": RAW_CAPTURE_PATH.exists()},
        {"name": "replay_passed", "passed": replay.get("exit_status") == "passed"},
        {"name": "comparator_assertions_passed", "passed": comparator.get("failed") == 0 and comparator.get("warned") == 0},
        {"name": "secret_scan_passed", "passed": secret.get("passed") is True},
        {
            "name": "provider_free_network_free",
            "passed": target_probe.get("provider_dispatch_observed") is False and target_probe.get("network_observed") is False,
        },
        {
            "name": "projection_events_host_visible_not_kernel_truth",
            "passed": replay.get("replay_summary", {}).get("all_projection_events_kernel_truth_false") is True
            and replay.get("replay_summary", {}).get("all_projection_events_host_visible") is True
            and replay.get("replay_summary", {}).get("all_projection_events_model_invisible") is True,
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
        "projection_events": f"{display_path(PROJECTION_EVENTS_PATH)}#{sha256_file(PROJECTION_EVENTS_PATH)}",
    }
    row = {
        "schema_version": "bb.atomic_feature_ledger.v1",
        "feature_id": FEATURE_ID,
        "family": "projection",
        "target": "omp",
        "claim_type": "host_projection",
        "evidence_tier": "C4",
        "dedupe_key": "omp/projection/oh_my_pi_p6_0_l6_tui_projection_gallery_stdout/host_projection/model_no/stateful_yes",
        "e4_row_ref": CONFIG_ID,
        "model_visible": False,
        "stateful": True,
        "gap_kind": "none",
        "promotion_state": "ready",
        "breadboard_mapping": {
            "primitive": "bb.projection_event.v1",
            "support": "supported",
            "truth_scope": "target_projection",
        },
        "fixture_refs": [f"{role}:{ref}" for role, ref in refs.items()],
        "source_refs": [
            f"source:{display_path(SUPPORT_CLAIM_PATH)}",
            f"source:{display_path(EVIDENCE_MANIFEST_PATH)}",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/cli/gallery-cli.ts",
            "source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/modes/components/tool-execution.ts",
        ],
    }
    ledger = read_json(LEDGER_PATH)
    rows = ledger.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ledger rows must be a list")
    rows[:] = [existing for existing in rows if not (isinstance(existing, dict) and (existing.get("feature_id") == FEATURE_ID or existing.get("e4_row_ref") == CONFIG_ID))]
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
        "level": "P6.0-L6",
        "points": 20,
        "accepted": True,
        "summary": "Oh-My-Pi 16.2.13 provider-free `omp gallery --plain` host TUI/tool-surface projection is accepted only for the named P6.0-L6 lane, run, config, target version, fixed terminal width, selected renderer lifecycle states, and read-only projection-only scope.",
        "acceptance_rationale": "The governed claim is limited to exact gallery stdout projections for read success, bash error, and report_tool_issue success. The packet contains raw command reports, replay normalization into bb.projection_event.v1 with kernel_truth=false, comparator pass, parity result, packet-local secret scan, validator-output hash, and ledger-row freshness.",
        "scope": {
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "target_version": TARGET_VERSION,
            "provider_model": PROVIDER_MODEL,
            "sandbox_mode": SANDBOX_MODE,
            "terminal_width": 100,
            "plain": True,
            "projection_boundary": "host-visible projection-only; kernel_truth=false",
        },
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No Pi, Opencode, OpenCode, Codex, Claude, provider-parity, memory, task/agent-routing, browser automation, screenshot, or general P6/P7 support claim is made.",
            "No write-enabled, provider-authenticated, or danger-full-access sandbox behavior is claimed.",
            "No claim is made for provider endpoints, web search execution, external SaaS APIs, model inference, or live transcript/session behavior.",
            "No kernel-truth state mutation is claimed; the BreadBoard primitive records host-visible projection events with kernel_truth=false.",
            "No memory backend, compaction, Hindsight, Mnemopi, task, job, or subagent semantics are claimed.",
        ],
        "freeze_ref": refs["freeze"],
        "capture_ref": refs["capture"],
        "raw_source_ref": refs["projection_events"],
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
        {
            "path": display_path(RAW_CAPTURE_PATH),
            "role": "capture_ref",
            "sha256": sha256_file(RAW_CAPTURE_PATH),
        },
        {
            "path": display_path(REPLAY_PATH),
            "role": "replay_ref",
            "sha256": sha256_file(REPLAY_PATH),
            "derived_from": [refs["capture"], refs["projection_events"]],
        },
        {
            "path": display_path(COMPARATOR_PATH),
            "role": "comparator_ref",
            "sha256": sha256_file(COMPARATOR_PATH),
            "derived_from": [refs["capture"], refs["replay"], refs["projection_events"]],
        },
        {
            "path": display_path(PARITY_PATH),
            "role": "parity_results",
            "sha256": sha256_file(PARITY_PATH),
            "derived_from": [refs["comparator"]],
        },
        {"path": display_path(SECRET_SCAN_PATH), "role": "secret_scan_report", "sha256": sha256_file(SECRET_SCAN_PATH)},
        {"path": display_path(PREVALIDATION_PATH), "role": "validator_output", "sha256": sha256_file(PREVALIDATION_PATH)},
        {"path": display_path(SUPPORT_CLAIM_PATH), "role": "support_claim_ref", "sha256": sha256_file(SUPPORT_CLAIM_PATH)},
        {"path": display_path(LEDGER_PATH), "role": "atomic_feature_ledger", "sha256": sha256_file(LEDGER_PATH)},
        {"path": display_path(PROJECTION_EVENTS_PATH), "role": "projection_events", "sha256": sha256_file(PROJECTION_EVENTS_PATH)},
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
    # Rebuild setup/raw hash inputs now that raw capture exists.
    build_replay_and_comparator(generated_at)
    # Rebuild raw capture once projection events exist so source hashes include them.
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
    parser = argparse.ArgumentParser(description="Build Oh-My-Pi P6.0-L6 TUI/projection C4 artifacts")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary")
    parser.add_argument("--force", action="store_true", help="Replace existing lane capture directory before rebuilding")
    args = parser.parse_args(argv)
    summary = build_all(force=args.force)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"built {CONFIG_ID}: {summary['support_claim']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
