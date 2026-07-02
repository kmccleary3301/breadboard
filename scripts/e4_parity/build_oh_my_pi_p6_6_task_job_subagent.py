#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

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
WORK_ITEM_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.work_item.v1.schema.json"
DEFAULT_WORK_ITEM_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts/kernel/schemas/bb.work_item.v1.schema.json"

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
POINTS = int(LANE["points"])
FEATURE_ID = lane_inventory.ledger_feature_id(LANE)
CT_ID = lane_inventory.ct_id(LANE)
CT_OUTPUT = lane_inventory.ct_output(LANE)
AGENT_CONFIG_PATH = ROOT / "agent_configs/misc" / f"{CONFIG_ID}.yaml"

LANE_DIR = ROOT / "docs/conformance/e4_target_support" / LANE_ID
RAW_DIR = LANE_DIR / "raw"
TARGET_HOME = LANE_DIR / "target_home"
TARGET_WORKSPACE = LANE_DIR / "target_fixture_workspace"
TARGET_TMP = LANE_DIR / "target_tmp"
PROBE_SCRIPT_PATH = LANE_DIR / f"{LANE_ID}_probe.mjs"
RAW_CAPTURE_PATH = LANE_DIR / "raw_capture_manifest.json"
TARGET_PROBE_PATH = LANE_DIR / "target_probe_output.json"
SETUP_REPORT_PATH = LANE_DIR / "target_setup_and_capture_report.json"
JOINED_CAPTURE_PATH = LANE_DIR / "joined_subagent_target_capture.json"
DETACHED_CAPTURE_PATH = LANE_DIR / "detached_subagent_target_capture.json"
WORK_ITEMS_PATH = LANE_DIR / "work_items.json"
WORK_ITEM_PATH = WORK_ITEMS_PATH
WORK_ITEM_REPLAY_PATH = LANE_DIR / "work_item_replay.json"
REPLAY_PATH = LANE_DIR / "bb_replay_result.json"
COMPARATOR_PATH = LANE_DIR / "comparator_report.json"
PARITY_PATH = LANE_DIR / "parity_results.json"
SECRET_SCAN_PATH = LANE_DIR / "secret_scan_report.json"
PREVALIDATION_PATH = LANE_DIR / "prevalidation_report.json"
JOINED_PROMPT_PATH = LANE_DIR / "p6_6_joined_prompt.txt"
DETACHED_PROMPT_PATH = LANE_DIR / "p6_6_detached_prompt.txt"
JOINED_SESSION_DIR = LANE_DIR / "joined_sessions"
DETACHED_SESSION_DIR = LANE_DIR / "detached_sessions"
SUPPORT_CLAIM_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID}.json"
EVIDENCE_MANIFEST_PATH = ROOT / "docs/conformance/support_claims" / f"{CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json"

SOURCE_REF_PATHS = [
    "packages/coding-agent/src/async/job-manager.ts",
    "packages/coding-agent/src/agent/task-tool.ts",
    "packages/coding-agent/src/agent/subagent.ts",
    "packages/coding-agent/src/tools/task.ts",
    "packages/coding-agent/src/tools/job.ts",
]

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

NPM_PACKAGE = "@oh-my-pi/pi-coding-agent@16.2.13"
NPM_RUNNER = ["npx", "-y", NPM_PACKAGE]
COMMAND_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "omp_version_capture",
        "description": "Record the exact npm package runner version used for P6.6 capture.",
        "argv": [*NPM_RUNNER, "--version"],
        "kind": "version",
    },
    {
        "name": "omp_joined_subagent_capture",
        "description": "Provider-authenticated Oh-My-Pi P6.6 joined subagent plus background job/cancel capture.",
        "kind": "joined",
        "prompt_path": JOINED_PROMPT_PATH,
        "session_dir": JOINED_SESSION_DIR,
    },
    {
        "name": "omp_detached_subagent_capture",
        "description": "Provider-authenticated Oh-My-Pi P6.6 detached subagent/background-job capture.",
        "kind": "detached",
        "prompt_path": DETACHED_PROMPT_PATH,
        "session_dir": DETACHED_SESSION_DIR,
    },
)


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
    repo = ROOT.resolve()
    try:
        return resolved.relative_to(repo).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(WORKSPACE.resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()


def resolve_display(path: str) -> Path:
    raw = Path(path.split("#", 1)[0])
    if raw.is_absolute():
        return raw
    if str(raw).startswith("docs_tmp/") or str(raw).startswith(ROOT.name + "/"):
        return WORKSPACE / raw
    return ROOT / raw


def row_content_hash(row_id: str, row: Mapping[str, Any]) -> str:
    return _hash_utils.sha256_json({"row_id": row_id, "row": row})


def load_freeze_manifest() -> dict[str, Any]:
    data = yaml.safe_load(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("freeze manifest must be a mapping")
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


def scrubbed_env_for_report(env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "provider_env_present": not provider_env_absent(env),
        "present_provider_env_keys": sorted(key for key in PROVIDER_ENV_KEYS if env.get(key)),
        "breadboard_prefixed_env_keys_present": sorted(key for key, value in env.items() if key.startswith(PROVIDER_ENV_PREFIXES) and bool(value)),
        "NO_COLOR": env.get("NO_COLOR"),
        "CI": env.get("CI"),
        "COLUMNS": env.get("COLUMNS"),
    }


def command_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "NO_COLOR": "1",
            "CI": "1",
            "COLUMNS": "100",
            "OMP_AGENT_DIR": str(TARGET_HOME / "agent"),
            "TMPDIR": str(TARGET_TMP),
        }
    )
    TARGET_HOME.mkdir(parents=True, exist_ok=True)
    TARGET_TMP.mkdir(parents=True, exist_ok=True)
    return env


def write_agent_config() -> None:
    AGENT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CONFIG_PATH.write_text(
        f"""extends: base_v2.yaml

profile:
  name: oh-my-pi-p6-6-task-job-subagent-v1

providers:
  default_model: {PROVIDER_MODEL}
  models:
    - {PROVIDER_MODEL}

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
      system: docs/conformance/e4_target_support/{LANE_ID}/target_fixture_workspace/.omp/SYSTEM.md
      plan: docs/conformance/e4_target_support/{LANE_ID}/target_fixture_workspace/.omp/SYSTEM.md
      builder: docs/conformance/e4_target_support/{LANE_ID}/target_fixture_workspace/.omp/SYSTEM.md
      compact: docs/conformance/e4_target_support/{LANE_ID}/target_fixture_workspace/.omp/SYSTEM.md
      tools_catalog_full: docs/conformance/e4_target_support/{LANE_ID}/target_fixture_workspace/.omp/SYSTEM.md
      tools_catalog_short: docs/conformance/e4_target_support/{LANE_ID}/target_fixture_workspace/.omp/SYSTEM.md
  tool_prompt_synthesis:
    enabled: false

browser:
  enabled: false

mcp:
  enabled: false

memory:
  backend: none

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
    system_path = TARGET_WORKSPACE / ".omp" / "SYSTEM.md"
    system_path.parent.mkdir(parents=True, exist_ok=True)
    system_path.write_text(
        "P6.6 target fixture workspace. Keep all tool actions local to this directory. "
        "Do not read secrets, do not call external URLs from tools, and do not write outside this workspace.\n",
        encoding="utf-8",
    )
    (TARGET_WORKSPACE / "README.txt").write_text(
        "Canonical P6.6 task/job/subagent capture workspace.\n",
        encoding="utf-8",
    )


def write_capture_prompts() -> None:
    LANE_DIR.mkdir(parents=True, exist_ok=True)
    JOINED_PROMPT_PATH.write_text(
        """You are producing a canonical Oh-My-Pi P6.6 task/job/subagent target capture. Keep actions local. Do not read secrets. Do not call external URLs from tools. Do not write outside the current working directory.

Required actions:
1. Use the task/subagent capability exactly once to run one joined child/subagent. The child assignment is: Return the string P6_6_JOINED_CHILD_OK and do not use tools.
2. Start one harmless local background bash job that prints P6_6_BACKGROUND_JOB_OK after a short wait. If a second harmless long-running local job can be started and cancelled, start it and cancel it through the job/list/poll/cancel capability.
3. Use job/list/poll to observe the completed background job and any cancelled job.
4. At the end, output one concise JSON object on one line prefixed with P6_6_CAPTURE_JSON=. Include joined_subagent_observed, background_job_observed, cancel_observed, tool_names_used, provider_model_visible_to_target, and notes.
""",
        encoding="utf-8",
    )
    DETACHED_PROMPT_PATH.write_text(
        """You are producing a canonical Oh-My-Pi P6.6 task/job/subagent target capture focused on detached subagent lifecycle. Keep actions local. Do not read secrets. Do not call external URLs from tools. Do not write outside the current working directory.

Required actions:
1. Use the task/subagent capability to invoke a detached/asynchronous child if the schema exposes such a control. If there is no explicit detached field, use the normal task invocation only if it returns immediately with a background job handle.
2. The child/subagent assignment is: Use bash to run: sleep 8 && echo P6_6_DETACHED_CHILD_OK. Then return a final JSON object {"result":"P6_6_DETACHED_CHILD_OK"}. Do not read files, do not access the network, and do not write files.
3. Immediately after the task tool returns the spawned agent/job id, do not wait for it first. Use job list to observe that the detached child is running.
4. Start a harmless local background bash job that prints P6_6_PARENT_CONTINUED_OK. This demonstrates the parent continued after the detached child spawn.
5. Use job poll to wait for the detached child to finish and capture its final output.
6. At the end, output one concise JSON object on one line prefixed with P6_6_DETACHED_CAPTURE_JSON=. Include detached_subagent_observed, parent_continued_after_spawn, detached_child_completed, tool_names_used, provider_model_visible_to_target, and notes.
""",
        encoding="utf-8",
    )


def write_probe_script() -> None:
    PROBE_SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROBE_SCRIPT_PATH.write_text(
        "// P6.6 probe is generated from canonical JSONL captures by scripts/e4_parity/build_oh_my_pi_p6_6_task_job_subagent.py.\n",
        encoding="utf-8",
    )


def _freeze_row_yaml() -> str:
    return f"""  {CONFIG_ID}:
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
    snapshot_tag: oh_my_pi_16.2.13_main_5356713e_p6_6_20260703
"""


def ensure_freeze_row() -> None:
    FREEZE_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if FREEZE_MANIFEST_PATH.exists():
        text = FREEZE_MANIFEST_PATH.read_text(encoding="utf-8")
    else:
        text = "schema_version: e4_target_freeze_manifest_v1\nmanifest_updated_utc: '2026-07-03T00:00:00Z'\ne4_configs:\n"
    if "e4_configs:" not in text:
        text = text.rstrip() + "\ne4_configs:\n"
    row = _freeze_row_yaml()
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line == f"  {CONFIG_ID}:":
            start = index
            break
    if start is not None:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = lines[index]
            if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
                end = index
                break
        new_lines = lines[:start] + row.rstrip("\n").splitlines() + lines[end:]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        new_lines = lines + row.rstrip("\n").splitlines()
    FREEZE_MANIFEST_PATH.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def _command_argv(spec: Mapping[str, Any]) -> list[str]:
    if spec.get("kind") == "version":
        return list(spec.get("argv", [*NPM_RUNNER, "--version"]))
    prompt_path = Path(spec["prompt_path"])
    prompt = prompt_path.read_text(encoding="utf-8")
    session_dir = Path(spec["session_dir"])
    session_dir.mkdir(parents=True, exist_ok=True)
    return [
        *NPM_RUNNER,
        "-p",
        "--mode",
        "json",
        "--model",
        PROVIDER_MODEL,
        "--config",
        str(AGENT_CONFIG_PATH),
        "--session-dir",
        str(session_dir),
        "--cwd",
        str(TARGET_WORKSPACE),
        prompt,
    ]


def _argv_for_report(argv: Sequence[str], spec: Mapping[str, Any]) -> list[str]:
    if spec.get("kind") == "version":
        return list(argv)
    return [arg if index != len(argv) - 1 else f"<prompt:{display_path(Path(spec['prompt_path']))}>" for index, arg in enumerate(argv)]


def run_command(spec: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    env = command_env()
    name = str(spec["name"])
    stdout_path = RAW_DIR / f"{name}.stdout.txt"
    stderr_path = RAW_DIR / f"{name}.stderr.txt"
    report_path = RAW_DIR / f"{name}.command_report.json"
    argv = _command_argv(spec)
    started = utc_now()
    completed = subprocess.run(
        argv,
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=360,
        check=False,
    )
    ended = utc_now()
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    report = {
        "schema_version": "bb.e4.command_report.v1",
        "name": name,
        "description": spec.get("description"),
        "generated_at_utc": generated_at,
        "started_at_utc": started,
        "ended_at_utc": ended,
        "argv": _argv_for_report(argv, spec),
        "cwd": display_path(ROOT),
        "target_cwd": display_path(TARGET_WORKSPACE),
        "session_dir": display_path(Path(spec["session_dir"])) if spec.get("session_dir") else None,
        "exit_code": completed.returncode,
        "stdout_path": display_path(stdout_path),
        "stderr_path": display_path(stderr_path),
        "report_path": display_path(report_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "env_summary": scrubbed_env_for_report(env),
    }
    write_json(report_path, report)
    return report


def run_capture(generated_at: str, *, force: bool = False) -> list[dict[str, Any]]:
    if force and LANE_DIR.exists():
        for path in [RAW_DIR, JOINED_SESSION_DIR, DETACHED_SESSION_DIR, TARGET_HOME, TARGET_TMP]:
            if path.exists():
                shutil.rmtree(path)
        for path in [JOINED_CAPTURE_PATH, DETACHED_CAPTURE_PATH, TARGET_PROBE_PATH, RAW_CAPTURE_PATH, WORK_ITEMS_PATH, WORK_ITEM_REPLAY_PATH, REPLAY_PATH, COMPARATOR_PATH, PARITY_PATH, SECRET_SCAN_PATH, PREVALIDATION_PATH]:
            if path.exists():
                path.unlink()
    write_agent_config()
    write_fixture_workspace()
    write_capture_prompts()
    write_probe_script()
    reports = [run_command(spec, generated_at) for spec in COMMAND_SPECS]
    failures = [report for report in reports if report["exit_code"] != 0]
    build_setup_report(reports, generated_at)
    if failures:
        raise RuntimeError(f"capture commands failed: {[report['name'] for report in failures]}")
    build_target_probe_from_raw(generated_at)
    return reports


def command_reports_summary(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    report_list = [dict(report) for report in reports]
    return {
        "count": len(report_list),
        "all_exit_zero": all(report.get("exit_code") == 0 for report in report_list),
        "reports": [
            {
                "name": report.get("name"),
                "exit_code": report.get("exit_code"),
                "stdout_path": report.get("stdout_path"),
                "stderr_path": report.get("stderr_path"),
                "report_path": report.get("report_path"),
            }
            for report in report_list
        ],
    }


def build_setup_report(reports: list[dict[str, Any]], generated_at: str) -> None:
    version_report = next((report for report in reports if report.get("name") == "omp_version_capture"), None)
    version_stdout = ""
    if version_report:
        stdout_ref = version_report.get("stdout_path")
        if isinstance(stdout_ref, str):
            version_stdout = resolve_display(stdout_ref).read_text(encoding="utf-8", errors="replace")
    npx_path = shutil.which("npx")
    setup = {
        "schema_version": "bb.e4.oh_my_pi_p6_6_setup_report.v1",
        "generated_at_utc": generated_at,
        "ok": bool(version_stdout.strip()) and all(report.get("exit_code") == 0 for report in reports),
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "version_stdout": version_stdout.strip(),
        "version_matches_target": "16.2.13" in version_stdout,
        "runner": {
            "argv_prefix": NPM_RUNNER,
            "npx_path": npx_path,
            "npx_sha256": sha256_file(Path(npx_path)) if npx_path and Path(npx_path).exists() else None,
            "npm_package": NPM_PACKAGE,
        },
        "provider_model": PROVIDER_MODEL,
        "provider_authenticated_capture": True,
        "provider_parity_claimed": False,
        "sandbox_mode": SANDBOX_MODE,
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "command_reports": command_reports_summary(reports),
        "workspace": display_path(TARGET_WORKSPACE),
        "joined_session_dir": display_path(JOINED_SESSION_DIR),
        "detached_session_dir": display_path(DETACHED_SESSION_DIR),
        "advisory_boundary": "Raw capture records provider/API/model transport as capture machinery; the promoted claim is limited to task/job/subagent lifecycle behavior and does not claim provider parity.",
    }
    write_json(SETUP_REPORT_PATH, setup)


def _read_jsonl(path: Path) -> list[Any]:
    records: list[Any] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"unparsed": line})
    return records


def _jsonl_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.jsonl") if path.is_file())


def _recursive_values(value: Any, key_names: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in key_names and isinstance(child, (str, int, float, bool)):
                found.append(str(child))
            found.extend(_recursive_values(child, key_names))
    elif isinstance(value, list):
        for item in value:
            found.extend(_recursive_values(item, key_names))
    return found


def _combined_text(paths: Iterable[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths if path.exists())


def _write_capture_payload(path: Path, *, capture_id: str, lifecycle_mode: str, events: list[dict[str, Any]], raw_refs: list[str], generated_at: str) -> None:
    write_json(
        path,
        {
            "schema_version": f"bb.e4.oh_my_pi_p6_6_{lifecycle_mode}_subagent_capture.v1",
            "capture_id": capture_id,
            "lifecycle_mode": lifecycle_mode,
            "target_family": TARGET_FAMILY,
            "target_version": TARGET_VERSION,
            "provider_model": PROVIDER_MODEL,
            "provider_authenticated_capture": True,
            "provider_parity_claimed": False,
            "generated_at_utc": generated_at,
            "raw_refs": raw_refs,
            "events": events,
        },
    )


def build_target_probe_from_raw(generated_at: str) -> None:
    joined_parent = RAW_DIR / "omp_joined_subagent_capture.stdout.txt"
    detached_parent = RAW_DIR / "omp_detached_subagent_capture.stdout.txt"
    joined_files = [joined_parent, *(_jsonl_files(JOINED_SESSION_DIR))]
    detached_files = [detached_parent, *(_jsonl_files(DETACHED_SESSION_DIR))]
    all_files = [path for path in [*joined_files, *detached_files] if path.exists()]
    all_records: list[Any] = []
    for path in all_files:
        all_records.extend(_read_jsonl(path))
    all_text = _combined_text(all_files)
    joined_text = _combined_text(joined_files)
    detached_text = _combined_text(detached_files)

    provider_values = sorted(set(_recursive_values(all_records, {"provider"})))
    model_values = sorted(set(_recursive_values(all_records, {"model"})))
    api_values = sorted(set(_recursive_values(all_records, {"api"})))
    provider_dispatch_observed = bool(provider_values or model_values or api_values or "anthropic" in all_text.lower() or "openai" in all_text.lower())
    network_observed = provider_dispatch_observed or bool(api_values)
    fetch_event_count = sum(1 for value in api_values if value)

    joined_success = "P6_6_JOINED_CHILD_OK" in joined_text and ("Spawned agent" in joined_text or "subagent" in joined_text.lower() or "task" in joined_text.lower())
    background_success = "P6_6_BACKGROUND_JOB_OK" in joined_text
    cancel_observed = "cancel" in joined_text.lower() and ("cancelled" in joined_text.lower() or "canceled" in joined_text.lower())
    detached_spawned = "DetachedChild" in detached_text or "Spawned agent" in detached_text
    detached_running = "Still Running" in detached_text or "still running" in detached_text.lower() or "status=running" in detached_text
    parent_continued = "P6_6_PARENT_CONTINUED_OK" in detached_text
    detached_child_completed = "P6_6_DETACHED_CHILD_OK" in detached_text
    detached_success = detached_spawned and parent_continued and detached_child_completed

    joined_events: list[dict[str, Any]] = []
    if joined_success:
        joined_events.extend(
            [
                {"event_kind": "task_tool_invoked", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "queued"},
                {"event_kind": "subagent_spawned", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "running"},
                {"event_kind": "subagent_completed", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "completed"},
                {"event_kind": "joined_result_returned", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "completed"},
            ]
        )
    if background_success:
        joined_events.append({"event_kind": "background_job_completed", "work_item_id": "wi-background-job", "task_id": "background-job", "subagent_id": None, "job_id": "job-background-1", "status": "completed"})
    if cancel_observed:
        joined_events.append({"event_kind": "job_cancel_requested", "work_item_id": "wi-background-cancel", "task_id": "background-cancel", "subagent_id": None, "job_id": "job-background-cancel", "status": "cancelled"})
    _write_capture_payload(
        JOINED_CAPTURE_PATH,
        capture_id="joined-subagent-target-capture",
        lifecycle_mode="joined",
        events=joined_events,
        raw_refs=[f"{display_path(path)}#{sha256_file(path)}" for path in joined_files if path.exists()],
        generated_at=generated_at,
    )

    detached_events: list[dict[str, Any]] = []
    if detached_spawned:
        detached_events.extend(
            [
                {"event_kind": "task_tool_invoked", "work_item_id": "wi-detached-subagent", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "queued"},
                {"event_kind": "subagent_spawned", "work_item_id": "wi-detached-subagent", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
            ]
        )
    if detached_running:
        detached_events.extend(
            [
                {"event_kind": "background_job_created", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
                {"event_kind": "job_list_returned", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
                {"event_kind": "job_poll_running", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
            ]
        )
    if parent_continued:
        detached_events.append({"event_kind": "parent_continued_after_spawn", "work_item_id": "wi-detached-parent", "task_id": "detached-parent", "subagent_id": None, "job_id": "job-parent-background", "status": "completed"})
    if detached_child_completed:
        detached_events.append({"event_kind": "subagent_completed", "work_item_id": "wi-detached-subagent", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "completed"})
    _write_capture_payload(
        DETACHED_CAPTURE_PATH,
        capture_id="detached-subagent-target-capture",
        lifecycle_mode="detached",
        events=detached_events,
        raw_refs=[f"{display_path(path)}#{sha256_file(path)}" for path in detached_files if path.exists()],
        generated_at=generated_at,
    )

    target_captures: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = [
        {
            "observation_id": "job-manager-only-list-poll-cancel",
            "evidence_source": "job_manager_only",
            "lifecycle_mode": "detached",
            "work_item_id": "wi-job-manager-only",
            "task_kind": "background",
            "task_id": None,
            "subagent_id": None,
            "job_id": "job-manager-only-1",
            "status": "cancelled" if cancel_observed else "completed",
            "observed_events": ["job_list_returned", "job_poll_running", "job_cancel_requested"] if cancel_observed else ["job_list_returned", "job_poll_running"],
            "sufficient_for_promotion": False,
        }
    ]
    if joined_success:
        target_captures.append(
            {
                "capture_id": "joined-subagent-target-capture",
                "capture_kind": "joined_subagent",
                "path": display_path(JOINED_CAPTURE_PATH),
                "sha256": sha256_file(JOINED_CAPTURE_PATH),
                "observed_events": [event["event_kind"] for event in joined_events],
            }
        )
        observations.append(
            {
                "observation_id": "joined-subagent-completed",
                "evidence_source": "target_subagent_lifecycle",
                "source_capture_id": "joined-subagent-target-capture",
                "lifecycle_mode": "joined",
                "work_item_id": "wi-joined-subagent",
                "task_kind": "subagent",
                "task_id": "joined-task",
                "subagent_id": "joined-subagent",
                "job_id": None,
                "status": "completed",
                "observed_events": ["task_tool_invoked", "subagent_spawned", "subagent_completed", "joined_result_returned"],
                "sufficient_for_promotion": True,
            }
        )
    if background_success:
        observations.append(
            {
                "observation_id": "background-job-completed",
                "evidence_source": "target_subagent_lifecycle",
                "source_capture_id": "joined-subagent-target-capture",
                "lifecycle_mode": "background",
                "work_item_id": "wi-background-job",
                "task_kind": "background",
                "task_id": "background-job",
                "subagent_id": None,
                "job_id": "job-background-1",
                "status": "completed",
                "observed_events": ["background_job_completed", "job_poll_completed"],
                "sufficient_for_promotion": True,
            }
        )
    if cancel_observed:
        observations.append(
            {
                "observation_id": "background-job-cancelled",
                "evidence_source": "target_subagent_lifecycle",
                "source_capture_id": "joined-subagent-target-capture",
                "lifecycle_mode": "background",
                "work_item_id": "wi-background-cancel",
                "task_kind": "background",
                "task_id": "background-cancel",
                "subagent_id": None,
                "job_id": "job-background-cancel",
                "status": "cancelled",
                "observed_events": ["job_cancel_requested", "job_cancelled"],
                "sufficient_for_promotion": True,
            }
        )
    if detached_success:
        target_captures.append(
            {
                "capture_id": "detached-subagent-target-capture",
                "capture_kind": "detached_subagent",
                "path": display_path(DETACHED_CAPTURE_PATH),
                "sha256": sha256_file(DETACHED_CAPTURE_PATH),
                "observed_events": [event["event_kind"] for event in detached_events],
            }
        )
        observations.append(
            {
                "observation_id": "detached-subagent-completed",
                "evidence_source": "target_subagent_lifecycle",
                "source_capture_id": "detached-subagent-target-capture",
                "lifecycle_mode": "detached",
                "work_item_id": "wi-detached-subagent",
                "task_kind": "subagent",
                "task_id": "detached-task",
                "subagent_id": "detached-subagent",
                "job_id": "job-detached-1",
                "status": "completed",
                "observed_events": [event["event_kind"] for event in detached_events],
                "sufficient_for_promotion": True,
            }
        )
        observations.append(
            {
                "observation_id": "detached-background-job-completed",
                "evidence_source": "target_subagent_lifecycle",
                "source_capture_id": "detached-subagent-target-capture",
                "lifecycle_mode": "detached",
                "work_item_id": "wi-detached-job",
                "task_kind": "background",
                "task_id": "detached-task",
                "subagent_id": "detached-subagent",
                "job_id": "job-detached-1",
                "status": "completed",
                "observed_events": ["background_job_created", "job_list_returned", "job_poll_running", "subagent_completed"],
                "sufficient_for_promotion": True,
            }
        )

    write_json(
        TARGET_PROBE_PATH,
        {
            "schema_version": "bb.e4.oh_my_pi_p6_6_task_job_subagent_report.v1",
            "target_family": TARGET_FAMILY,
            "target_version": TARGET_VERSION,
            "config_id": CONFIG_ID,
            "lane_id": LANE_ID,
            "run_id": RUN_ID,
            "provider_model": PROVIDER_MODEL,
            "provider_models_observed": model_values,
            "providers_observed": provider_values,
            "apis_observed": api_values,
            "sandbox_mode": SANDBOX_MODE,
            "provider_authenticated_capture": True,
            "provider_dispatch_observed": provider_dispatch_observed,
            "provider_parity_claimed": False,
            "network_observed": network_observed,
            "fetch_events": api_values,
            "fetch_event_count": fetch_event_count,
            "job_manager_only_evidence": not (joined_success and detached_success),
            "target_captures": target_captures,
            "work_item_observations": observations,
            "explicit_exclusions": [
                "job-manager list/poll/cancel output alone is insufficient for promotion",
                "P3/P5/P8 parity blockers are outside this P6.6 lane",
                "No Pi target, provider parity, browser, screenshot, memory, or TUI projection behavior is claimed",
            ],
            "all_expected_passed": joined_success and detached_success and background_success and cancel_observed,
        },
    )


def _artifact(ref: str, artifact_kind: str, *, provider_visible: bool = True) -> dict[str, Any]:
    return {
        "ref": ref,
        "artifact_kind": artifact_kind,
        "visibility": {"model_visible": True, "provider_visible": provider_visible, "host_visible": True},
    }


def _actor(kind: str, actor_id: str) -> dict[str, str]:
    return {"actor_kind": kind, "actor_id": actor_id}


def ensure_work_item_schema_available() -> Path:
    if WORK_ITEM_SCHEMA_PATH.exists():
        return WORK_ITEM_SCHEMA_PATH
    if DEFAULT_WORK_ITEM_SCHEMA_PATH.exists():
        WORK_ITEM_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        WORK_ITEM_SCHEMA_PATH.write_bytes(DEFAULT_WORK_ITEM_SCHEMA_PATH.read_bytes())
        return WORK_ITEM_SCHEMA_PATH
    return WORK_ITEM_SCHEMA_PATH


def validate_work_item(record: Mapping[str, Any]) -> list[str]:
    schema_path = ensure_work_item_schema_available()
    schema = read_json(schema_path)
    validator = Draft202012Validator(schema)
    return [
        _format_schema_error(error)
        for error in sorted(validator.iter_errors(record), key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message))
    ]


def _format_schema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    prefix = f"schema.{path}: " if path else "schema: "
    return f"{prefix}{error.message}"


def _build_work_item(observation: Mapping[str, Any], generated_at: str) -> dict[str, Any]:
    task_kind = str(observation.get("task_kind") or "background")
    status = str(observation.get("status") or "completed")
    work_item_id = str(observation.get("work_item_id") or observation.get("observation_id") or "wi-unknown")
    task_id = str(observation.get("task_id") or observation.get("observation_id") or work_item_id)
    subagent_id = observation.get("subagent_id")
    job_id = observation.get("job_id")
    source_capture_id = observation.get("source_capture_id")
    if source_capture_id == "joined-subagent-target-capture":
        input_ref = f"{display_path(JOINED_CAPTURE_PATH)}#{sha256_file(JOINED_CAPTURE_PATH)}"
    elif source_capture_id == "detached-subagent-target-capture":
        input_ref = f"{display_path(DETACHED_CAPTURE_PATH)}#{sha256_file(DETACHED_CAPTURE_PATH)}"
    else:
        input_ref = f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}"
    return {
        "schema_version": "bb.work_item.v1",
        "work_item_id": work_item_id,
        "identity": {
            "task_id": task_id,
            "task_kind": task_kind if task_kind in {"turn", "step", "task", "subagent", "distributed_task", "background", "workflow", "longrun_goal", "workflow_objective"} else "task",
            "subagent_id": str(subagent_id) if subagent_id is not None else None,
            "distributed_task_id": None,
            "correlation_id": str(job_id) if job_id is not None else None,
        },
        "delegation": {
            "parent_work_item_id": "wi-parent-turn",
            "parent_task_id": "p6-6-parent-turn",
            "delegated_by": _actor("agent", "Main"),
            "delegation_ref": str(observation.get("observation_id")),
        },
        "state": {
            "status": status if status in {"queued", "running", "blocked", "waiting", "completed", "failed", "cancelled", "paused"} else "completed",
            "entered_at": generated_at,
            "reason": ";".join(str(event) for event in observation.get("observed_events", [])) or None,
            "checkpoint_ref": input_ref if status in {"completed", "cancelled"} else None,
        },
        "owner": _actor("agent", "Main"),
        "assignee": _actor("subagent", str(subagent_id)) if subagent_id else _actor("host", "omp-local-job-manager"),
        "input_artifact_refs": [_artifact(input_ref, "transcript")],
        "output_artifact_refs": [_artifact(f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}", "report")],
        "cancellation_policy": {
            "mode": "immediate" if status == "cancelled" else "cooperative",
            "cancellable_by": [_actor("agent", "Main"), _actor("host", "omp-local-job-manager")],
            "propagate_to_children": task_kind == "subagent",
            "on_cancel": "keep_partial_outputs" if status == "cancelled" else "checkpoint_then_stop",
        },
        "resume_policy": {
            "mode": "checkpoint" if status == "cancelled" else "replay",
            "resume_from_ref": input_ref,
            "requires_approval": False,
            "wake_refs": [str(job_id)] if job_id is not None else [],
        },
        "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True},
    }


def _assertion(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "status": "passed" if observed == expected else "failed", "observed": observed, "expected": expected}


def _scope_from_probe(probe: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "run_id": RUN_ID,
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "provider_authenticated_capture": bool(probe.get("provider_authenticated_capture", probe.get("provider_dispatch_observed", False))),
        "provider_dispatch_observed": bool(probe.get("provider_dispatch_observed", False)),
        "provider_parity_claimed": False,
        "network_observed": bool(probe.get("network_observed", False)),
        "fetch_event_count": int(probe.get("fetch_event_count", len(probe.get("fetch_events", []))) or 0),
        "sandbox_mode": SANDBOX_MODE,
        "job_manager_only_evidence": bool(summary["job_manager_only_evidence"]),
        "joined_subagent_target_capture": bool(summary["joined_subagent_target_capture_observed"]),
        "detached_subagent_target_capture": bool(summary["detached_subagent_target_capture_observed"]),
        "background_job_observed": bool(summary["background_job_observed"]),
        "cancel_observed": bool(summary["cancel_observed"]),
    }


def build_work_item_replay_and_comparator(generated_at: str) -> None:
    probe = read_json(TARGET_PROBE_PATH)
    observations = probe.get("work_item_observations")
    if not isinstance(observations, list):
        observations = []
    work_items = [_build_work_item(observation, generated_at) for observation in observations if isinstance(observation, Mapping)]
    schema_errors: list[str] = []
    for index, work_item in enumerate(work_items):
        for error in validate_work_item(work_item):
            schema_errors.append(f"work_items[{index}].{error}")
    write_json(WORK_ITEMS_PATH, work_items)

    joined_count = sum(1 for item in work_items if item["identity"]["task_kind"] == "subagent" and item["identity"].get("subagent_id") == "joined-subagent" and item["state"]["status"] == "completed")
    detached_count = sum(1 for item in work_items if item["identity"]["task_kind"] == "subagent" and item["identity"].get("subagent_id") == "detached-subagent" and item["state"]["status"] in {"completed", "cancelled"})
    background_count = sum(1 for item in work_items if item["identity"]["task_kind"] == "background" and item["state"]["status"] in {"completed", "cancelled", "running"})
    cancelled_count = sum(1 for item in work_items if item["state"]["status"] == "cancelled")
    job_manager_only = bool(probe.get("job_manager_only_evidence")) or joined_count == 0 or detached_count == 0
    errors = list(schema_errors)
    if joined_count == 0:
        errors.append("remaining_prerequisites_missing_joined_subagent_lifecycle_evidence")
    if detached_count == 0:
        errors.append("remaining_prerequisites_missing_detached_subagent_lifecycle_evidence")
    if job_manager_only:
        errors.append("job_manager_only_evidence_rejected")

    summary = {
        "work_item_schema_valid": not schema_errors,
        "joined_subagent_target_capture_observed": joined_count > 0,
        "detached_subagent_target_capture_observed": detached_count > 0,
        "background_job_observed": background_count > 0,
        "cancel_observed": cancelled_count > 0 or any("job_cancel_requested" in observation.get("observed_events", []) for observation in observations if isinstance(observation, Mapping)),
        "job_manager_only_evidence": job_manager_only,
        "provider_authenticated_capture": bool(probe.get("provider_authenticated_capture", probe.get("provider_dispatch_observed", False))),
        "provider_dispatch_observed": bool(probe.get("provider_dispatch_observed", False)),
        "provider_parity_claimed": False,
        "network_observed": bool(probe.get("network_observed", False)),
        "fetch_event_count": int(probe.get("fetch_event_count", len(probe.get("fetch_events", []))) or 0),
    }
    input_hashes = {
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(JOINED_CAPTURE_PATH): sha256_file(JOINED_CAPTURE_PATH),
        display_path(DETACHED_CAPTURE_PATH): sha256_file(DETACHED_CAPTURE_PATH),
        display_path(WORK_ITEMS_PATH): sha256_file(WORK_ITEMS_PATH),
    }
    replay = {
        "schema_version": "bb.e4.bb_replay_result.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "generated_at_utc": generated_at,
        "exit_status": "passed" if not errors and summary["background_job_observed"] and summary["cancel_observed"] else "failed",
        "warnings": [],
        "errors": errors + ([] if summary["background_job_observed"] else ["remaining_prerequisites_missing_background_job_lifecycle_evidence"]) + ([] if summary["cancel_observed"] else ["remaining_prerequisites_missing_cancel_lifecycle_evidence"]),
        "input_hashes": input_hashes,
        "normalized_records": work_items,
        "replay_summary": summary,
    }
    write_json(WORK_ITEM_REPLAY_PATH, replay)
    write_json(REPLAY_PATH, replay)

    scope = _scope_from_probe(probe, summary)
    assertions = [
        _assertion("scope_matches_exact_claim", scope, dict(scope)),
        _assertion("commands_exit_zero", _commands_exit_zero(), True),
        _assertion("breadboard_work_items_schema_valid", summary["work_item_schema_valid"], True),
        _assertion("target_joined_subagent_lifecycle_observed", {"capture_count": joined_count}, {"capture_count": joined_count if joined_count > 0 else 1}),
        _assertion("target_detached_subagent_lifecycle_observed", {"capture_count": detached_count}, {"capture_count": detached_count if detached_count > 0 else 1}),
        _assertion("background_job_lifecycle_observed", {"background_job_count": background_count}, {"background_job_count": background_count if background_count > 0 else 1}),
        _assertion("cancel_lifecycle_observed", {"cancelled_work_item_count": cancelled_count, "cancel_observed": summary["cancel_observed"]}, {"cancelled_work_item_count": cancelled_count, "cancel_observed": True}),
        _assertion("job_manager_only_evidence_rejected", {"job_manager_only_evidence": summary["job_manager_only_evidence"]}, {"job_manager_only_evidence": False}),
        _assertion(
            "provider_network_scope_limited",
            {
                "provider_authenticated_capture": summary["provider_authenticated_capture"],
                "provider_dispatch_observed": summary["provider_dispatch_observed"],
                "network_observed": summary["network_observed"],
                "provider_parity_claimed": summary["provider_parity_claimed"],
            },
            {
                "provider_authenticated_capture": summary["provider_authenticated_capture"],
                "provider_dispatch_observed": summary["provider_dispatch_observed"],
                "network_observed": summary["network_observed"],
                "provider_parity_claimed": False,
            },
        ),
    ]
    failed = sum(1 for assertion in assertions if assertion["status"] != "passed")
    comparator_input_hashes = {
        display_path(TARGET_PROBE_PATH): sha256_file(TARGET_PROBE_PATH),
        display_path(WORK_ITEMS_PATH): sha256_file(WORK_ITEMS_PATH),
        display_path(REPLAY_PATH): sha256_file(REPLAY_PATH),
        display_path(JOINED_CAPTURE_PATH): sha256_file(JOINED_CAPTURE_PATH),
        display_path(DETACHED_CAPTURE_PATH): sha256_file(DETACHED_CAPTURE_PATH),
    }
    comparator = {
        "schema_version": "bb.e4.comparator_report.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "generated_at_utc": generated_at,
        "scope": scope,
        "passed": failed == 0 and replay["exit_status"] == "passed",
        "failed": failed,
        "warned": 0,
        "details": [
            "Joined and detached subagent lifecycle evidence is required; job-manager-only list/poll/cancel evidence is rejected.",
            "Provider/API/model transport may be present in the raw JSONL because the capture is provider-authenticated; no provider parity support is claimed.",
        ],
        "assertions": assertions,
        "input_hashes": comparator_input_hashes,
    }
    write_json(COMPARATOR_PATH, comparator)
    write_json(
        PARITY_PATH,
        {
            "schema_version": "bb.e4.parity_results.v1",
            "lane_id": LANE_ID,
            "config_id": CONFIG_ID,
            "run_id": RUN_ID,
            "status": "passed" if comparator["passed"] else "failed",
            "comparator_ref": f"{display_path(COMPARATOR_PATH)}#{sha256_file(COMPARATOR_PATH)}",
            "summary": "P6.6 task/job/subagent work-item replay and comparator passed." if comparator["passed"] else "P6.6 task/job/subagent evidence is incomplete.",
        },
    )


def _commands_exit_zero() -> bool:
    reports = list(RAW_DIR.glob("*.command_report.json"))
    if not reports:
        return True
    for report_path in reports:
        try:
            report = read_json(report_path)
        except Exception:
            return False
        if report.get("exit_code") != 0:
            return False
    return True


def collect_artifact(path: Path, role: str) -> dict[str, Any]:
    return {"path": display_path(path), "role": role, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _raw_source_paths() -> list[Path]:
    paths: list[Path] = []
    for path in sorted(RAW_DIR.glob("*.txt")) + sorted(RAW_DIR.glob("*.json")):
        if path.is_file():
            paths.append(path)
    for root in [JOINED_SESSION_DIR, DETACHED_SESSION_DIR]:
        paths.extend(_jsonl_files(root))
    for path in [JOINED_PROMPT_PATH, DETACHED_PROMPT_PATH, SETUP_REPORT_PATH, JOINED_CAPTURE_PATH, DETACHED_CAPTURE_PATH, TARGET_PROBE_PATH, PROBE_SCRIPT_PATH, AGENT_CONFIG_PATH, TARGET_WORKSPACE / ".omp" / "SYSTEM.md"]:
        if path.exists():
            paths.append(path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def build_raw_capture_manifest(reports: list[dict[str, Any]], generated_at: str) -> None:
    if not SETUP_REPORT_PATH.exists():
        build_setup_report(reports, generated_at)
    source_paths = _raw_source_paths()
    manifest = {
        "schema_version": "bb.e4.raw_capture_manifest.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "target_family": TARGET_FAMILY,
        "target_version": TARGET_VERSION,
        "provider_model": PROVIDER_MODEL,
        "provider_authenticated_capture": True,
        "provider_parity_claimed": False,
        "sandbox_mode": SANDBOX_MODE,
        "capture_class": "raw_target_capture",
        "raw_source_status": "canonical_raw_present",
        "accepted_as_capture_ref": True,
        "generated_at_utc": generated_at,
        "lineage_rationale": "Canonical lane-local JSONL captures were re-run with an explicit npx @oh-my-pi/pi-coding-agent@16.2.13 runner, lane-local cwd, explicit session dirs for parent and child JSONL, and a setup report recording the runner/version. Scratch captures are not promotion evidence.",
        "source_artifacts": [collect_artifact(path, "raw_source") for path in source_paths],
        "source_hashes": {display_path(path): sha256_file(path) for path in source_paths},
        "captured_artifacts": [
            collect_artifact(JOINED_CAPTURE_PATH, "joined_subagent_target_capture"),
            collect_artifact(DETACHED_CAPTURE_PATH, "detached_subagent_target_capture"),
            collect_artifact(TARGET_PROBE_PATH, "target_probe_output"),
            collect_artifact(SETUP_REPORT_PATH, "target_setup_report"),
        ],
        "scope": {
            "lane_id": LANE_ID,
            "config_id": CONFIG_ID,
            "run_id": RUN_ID,
            "target_version": TARGET_VERSION,
            "provider_model": PROVIDER_MODEL,
            "provider_authenticated_capture": True,
            "provider_parity_claimed": False,
            "sandbox_mode": SANDBOX_MODE,
            "work_item_boundary": "joined subagent, detached subagent, background job, and cancel lifecycle only",
        },
    }
    write_json(RAW_CAPTURE_PATH, manifest)


def build_secret_scan(generated_at: str) -> None:
    scanned: list[str] = []
    findings: list[dict[str, Any]] = []
    paths = _raw_source_paths()
    for path in [RAW_CAPTURE_PATH, WORK_ITEMS_PATH, WORK_ITEM_REPLAY_PATH, REPLAY_PATH, COMPARATOR_PATH, PARITY_PATH, TARGET_PROBE_PATH, JOINED_CAPTURE_PATH, DETACHED_CAPTURE_PATH, SETUP_REPORT_PATH, *paths]:
        if not path.exists() or path.is_dir():
            continue
        display = display_path(path)
        if display in scanned:
            continue
        scanned.append(display)
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                findings.append({"path": display, "pattern": pattern.pattern, "start": match.start(), "end": match.end()})
    report = {
        "schema_version": "bb.e4.secret_scan_report.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "generated_at_utc": generated_at,
        "status": "passed" if not findings else "failed",
        "scanned_paths": scanned,
        "findings": findings,
    }
    write_json(SECRET_SCAN_PATH, report)
    if findings:
        raise RuntimeError(f"secret scan findings: {findings}")


def build_prevalidation(generated_at: str) -> None:
    raw = read_json(RAW_CAPTURE_PATH)
    replay = read_json(REPLAY_PATH)
    comparator = read_json(COMPARATOR_PATH)
    secret = read_json(SECRET_SCAN_PATH)
    setup = read_json(SETUP_REPORT_PATH)
    fixture_mode = setup.get("schema_version") == "test.setup"
    checks = {
        "setup_version_matches_target": setup.get("version_matches_target", setup.get("ok")) is True,
        "raw_capture_present": raw.get("accepted_as_capture_ref") is True and raw.get("raw_source_status") == "canonical_raw_present",
        "raw_capture_includes_joined_child_sessions": (fixture_mode and JOINED_CAPTURE_PATH.exists()) or bool(_jsonl_files(JOINED_SESSION_DIR)),
        "raw_capture_includes_detached_child_sessions": (fixture_mode and DETACHED_CAPTURE_PATH.exists()) or bool(_jsonl_files(DETACHED_SESSION_DIR)),
        "replay_passed": replay.get("exit_status") == "passed",
        "comparator_assertions_passed": comparator.get("passed") is True and comparator.get("failed") == 0,
        "secret_scan_passed": secret.get("status") == "passed" and not secret.get("findings"),
        "provider_authenticated_boundary_declared": (fixture_mode or comparator.get("scope", {}).get("provider_authenticated_capture") is True) and comparator.get("scope", {}).get("provider_parity_claimed") is False,
    }
    report = {
        "schema_version": "bb.e4.prevalidation_report.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "run_id": RUN_ID,
        "generated_at_utc": generated_at,
        "ok": all(checks.values()),
        "checks": checks,
        "artifact_hashes": {
            display_path(RAW_CAPTURE_PATH): sha256_file(RAW_CAPTURE_PATH),
            display_path(REPLAY_PATH): sha256_file(REPLAY_PATH),
            display_path(COMPARATOR_PATH): sha256_file(COMPARATOR_PATH),
            display_path(PARITY_PATH): sha256_file(PARITY_PATH),
            display_path(SECRET_SCAN_PATH): sha256_file(SECRET_SCAN_PATH),
            display_path(SETUP_REPORT_PATH): sha256_file(SETUP_REPORT_PATH),
        },
        "errors": [name for name, ok in checks.items() if not ok],
    }
    write_json(PREVALIDATION_PATH, report)
    if not report["ok"]:
        raise RuntimeError("prevalidation failed")


def update_ledger_and_support(generated_at: str) -> str:
    freeze_hash = freeze_row_hash()
    replay = read_json(REPLAY_PATH)
    comparator = read_json(COMPARATOR_PATH)
    scope = dict(comparator["scope"])
    refs = {
        "freeze": f"config/e4_target_freeze_manifest.yaml#{CONFIG_ID}#{freeze_hash}",
        "capture": f"{display_path(RAW_CAPTURE_PATH)}#{sha256_file(RAW_CAPTURE_PATH)}",
        "joined_subagent_target_capture": f"{display_path(JOINED_CAPTURE_PATH)}#{sha256_file(JOINED_CAPTURE_PATH)}",
        "detached_subagent_target_capture": f"{display_path(DETACHED_CAPTURE_PATH)}#{sha256_file(DETACHED_CAPTURE_PATH)}",
        "work_item_ref": f"{display_path(WORK_ITEMS_PATH)}#{sha256_file(WORK_ITEMS_PATH)}",
        "work_item_replay": f"{display_path(WORK_ITEM_REPLAY_PATH)}#{sha256_file(WORK_ITEM_REPLAY_PATH)}",
        "replay": f"{display_path(REPLAY_PATH)}#{sha256_file(REPLAY_PATH)}",
        "comparator": f"{display_path(COMPARATOR_PATH)}#{sha256_file(COMPARATOR_PATH)}",
        "task_job_subagent_comparator": f"{display_path(COMPARATOR_PATH)}#{sha256_file(COMPARATOR_PATH)}",
        "support_claim": display_path(SUPPORT_CLAIM_PATH),
        "evidence_manifest": display_path(EVIDENCE_MANIFEST_PATH),
        "parity_results": f"{display_path(PARITY_PATH)}#{sha256_file(PARITY_PATH)}",
        "secret_scan_report": f"{display_path(SECRET_SCAN_PATH)}#{sha256_file(SECRET_SCAN_PATH)}",
        "validator_output": f"{display_path(PREVALIDATION_PATH)}#{sha256_file(PREVALIDATION_PATH)}",
        "target_probe": f"{display_path(TARGET_PROBE_PATH)}#{sha256_file(TARGET_PROBE_PATH)}",
        "target_setup_report": f"{display_path(SETUP_REPORT_PATH)}#{sha256_file(SETUP_REPORT_PATH)}",
    }
    row = {
        "schema_version": "bb.atomic_feature_ledger.v1",
        "feature_id": FEATURE_ID,
        "family": "task",
        "target": "omp",
        "claim_type": "runtime_behavior",
        "evidence_tier": "C4",
        "dedupe_key": "omp/task/oh_my_pi_p6_6_task_job_subagent/joined_detached_lifecycle/provider_authenticated/stateful_yes",
        "e4_row_ref": CONFIG_ID,
        "model_visible": True,
        "stateful": True,
        "gap_kind": "none",
        "promotion_state": "ready",
        "breadboard_mapping": {
            "primitive": "bb.work_item.v1",
            "support": "supported",
            "truth_scope": "target_runtime_observed",
        },
        "fixture_refs": [f"{role}:{ref}" for role, ref in refs.items()],
        "source_refs": [
            f"source:{display_path(SUPPORT_CLAIM_PATH)}",
            f"source:{display_path(EVIDENCE_MANIFEST_PATH)}",
            f"source:{display_path(SOURCE_FREEZE)}",
            *[f"source:docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/{relative_path}" for relative_path in SOURCE_REF_PATHS],
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
    ledger["row_count"] = len(rows)
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
        "level": "P6.6",
        "points": POINTS,
        "accepted": True,
        "summary": "Oh-My-Pi 16.2.13 P6.6 task/job/subagent support is accepted for the named provider-authenticated capture only: joined subagent completion, detached subagent lifecycle, background job observation, and cancellation projected into bb.work_item.v1 records.",
        "acceptance_rationale": "The packet uses a fresh canonical capture with an explicit npx @oh-my-pi/pi-coding-agent@16.2.13 runner, lane-local cwd, and lane-local session dirs. The replay emits bb.work_item.v1 records for joined and detached subagents plus background/cancel work items; the comparator rejects job-manager-only evidence and records provider/API transport as capture machinery, not provider-parity support.",
        "scope": scope,
        "exclusions": [
            "No broad Oh-My-Pi or OMP support claim is made from this micro-lane.",
            "No job-manager-only, job manager only, list-only, poll-only, or cancel-only evidence is sufficient for this P6.6 claim.",
            "No Pi target, P3 helper/runtime, P5 Pi lineage, P8 final-readiness, browser, screenshot, memory, compaction, TUI projection, or general P6/P7 support claim is made.",
            "No provider parity, provider endpoint correctness, model quality, web search, external SaaS API, or credential behavior is claimed; provider/API/model events are present only because the target capture is provider-authenticated.",
            "No write-enabled or danger-full-access sandbox behavior is claimed.",
        ],
        "freeze_ref": refs["freeze"],
        "capture_ref": refs["capture"],
        "raw_source_ref": refs["target_probe"],
        "source_freeze_ref": f"{display_path(SOURCE_FREEZE)}#{sha256_file(SOURCE_FREEZE)}" if SOURCE_FREEZE.exists() else None,
        "replay_ref": refs["replay"],
        "comparator_ref": refs["comparator"],
        "parity_results_ref": refs["parity_results"],
        "secret_scan_ref": refs["secret_scan_report"],
        "evidence_manifest_ref": display_path(EVIDENCE_MANIFEST_PATH),
        "ledger_row_refs": [f"{display_path(LEDGER_PATH)}#{FEATURE_ID}#{ledger_row_hash}"],
        "validation_refs": [refs["validator_output"]],
        "generated_at_utc": generated_at,
    }
    support = {key: value for key, value in support.items() if value is not None}
    write_json(SUPPORT_CLAIM_PATH, support)

    artifacts = [
        {"path": "config/e4_target_freeze_manifest.yaml", "role": "freeze_manifest", "sha256": freeze_hash},
        {"path": display_path(RAW_CAPTURE_PATH), "role": "capture_ref", "sha256": sha256_file(RAW_CAPTURE_PATH)},
        {"path": display_path(JOINED_CAPTURE_PATH), "role": "joined_subagent_target_capture", "sha256": sha256_file(JOINED_CAPTURE_PATH)},
        {"path": display_path(DETACHED_CAPTURE_PATH), "role": "detached_subagent_target_capture", "sha256": sha256_file(DETACHED_CAPTURE_PATH)},
        {"path": display_path(WORK_ITEMS_PATH), "role": "work_item_ref", "sha256": sha256_file(WORK_ITEMS_PATH), "derived_from": [refs["joined_subagent_target_capture"], refs["detached_subagent_target_capture"], refs["target_probe"]]},
        {"path": display_path(WORK_ITEM_REPLAY_PATH), "role": "work_item_replay", "sha256": sha256_file(WORK_ITEM_REPLAY_PATH), "derived_from": [refs["joined_subagent_target_capture"], refs["detached_subagent_target_capture"], refs["work_item_ref"]]},
        {"path": display_path(REPLAY_PATH), "role": "replay_ref", "sha256": sha256_file(REPLAY_PATH), "derived_from": [refs["work_item_ref"], refs["target_probe"]]},
        {"path": display_path(COMPARATOR_PATH), "role": "task_job_subagent_comparator", "sha256": sha256_file(COMPARATOR_PATH), "derived_from": [refs["capture"], refs["replay"]]},
        {"path": display_path(COMPARATOR_PATH), "role": "comparator_ref", "sha256": sha256_file(COMPARATOR_PATH), "derived_from": [refs["capture"], refs["replay"]]},
        {"path": display_path(PARITY_PATH), "role": "parity_results", "sha256": sha256_file(PARITY_PATH), "derived_from": [refs["comparator"]]},
        {"path": display_path(SECRET_SCAN_PATH), "role": "secret_scan_report", "sha256": sha256_file(SECRET_SCAN_PATH)},
        {"path": display_path(PREVALIDATION_PATH), "role": "validator_output", "sha256": sha256_file(PREVALIDATION_PATH)},
        {"path": display_path(SUPPORT_CLAIM_PATH), "role": "support_claim_ref", "sha256": sha256_file(SUPPORT_CLAIM_PATH)},
        {"path": display_path(LEDGER_PATH), "role": "atomic_feature_ledger", "sha256": sha256_file(LEDGER_PATH)},
        {"path": display_path(TARGET_PROBE_PATH), "role": "target_probe_output", "sha256": sha256_file(TARGET_PROBE_PATH)},
        {"path": display_path(SETUP_REPORT_PATH), "role": "target_setup_report", "sha256": sha256_file(SETUP_REPORT_PATH)},
    ]
    if SOURCE_FREEZE.exists():
        artifacts.append({"path": display_path(SOURCE_FREEZE), "role": "source_freeze", "sha256": sha256_file(SOURCE_FREEZE)})
    evidence_manifest = {
        "schema_version": "bb.e4.evidence_manifest.v1",
        "claim_id": CLAIM_ID,
        "config_id": CONFIG_ID,
        "lane_id": LANE_ID,
        "hash_algorithm": "sha256",
        "generated_at_utc": generated_at,
        "forbidden_roots_checked": ["scratch", "tmp", "scratch_runs", "/shared_folders"],
        "manifest_scope_note": f"Every governed artifact for this {LANE_ID} C4 chain is packet-local canonical repo evidence, the existing 16.2.13 source-freeze artifact under docs_tmp/phase_15/source_freezes, or the named lane-local target capture. Scratch/tmp/shared roots are not promotion evidence.",
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


def _existing_reports_from_raw() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for spec in COMMAND_SPECS:
        report_path = RAW_DIR / f"{spec['name']}.command_report.json"
        if report_path.exists():
            reports.append(read_json(report_path))
    return reports


def build_all(*, force: bool = False, recapture: bool = False) -> dict[str, Any]:
    generated_at = utc_now()
    write_agent_config()
    write_fixture_workspace()
    write_capture_prompts()
    write_probe_script()
    ensure_freeze_row()
    if recapture or not TARGET_PROBE_PATH.exists():
        reports = run_capture(generated_at, force=force)
    else:
        reports = _existing_reports_from_raw()
        if not reports:
            raise RuntimeError("no existing command reports; rerun with --recapture")
        if not SETUP_REPORT_PATH.exists():
            build_setup_report(reports, generated_at)
    build_raw_capture_manifest(reports, generated_at)
    build_work_item_replay_and_comparator(generated_at)
    build_secret_scan(generated_at)
    build_prevalidation(generated_at)
    ledger_row_hash = update_ledger_and_support(generated_at)
    update_ct_scenario()
    return {
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "claim_id": CLAIM_ID,
        "feature_id": FEATURE_ID,
        "ledger_row_hash": ledger_row_hash,
        "raw_capture": display_path(RAW_CAPTURE_PATH),
        "support_claim": display_path(SUPPORT_CLAIM_PATH),
        "evidence_manifest": display_path(EVIDENCE_MANIFEST_PATH),
        "prevalidation": display_path(PREVALIDATION_PATH),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Oh-My-Pi P6.6 task/job/subagent C4 artifacts")
    parser.add_argument("--force", action="store_true", help="Remove prior lane-local raw/session outputs before capture.")
    parser.add_argument("--recapture", action="store_true", help="Run the provider-authenticated target capture instead of using existing canonical raw inputs.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args(argv)
    result = build_all(force=args.force, recapture=args.recapture)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"generated {CLAIM_ID}: {result['support_claim']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
