from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from breadboard.rl.m12.final_report import (
    COMMAND_LOG_MANIFEST_ID,
    OPTIONAL_COMMAND_LOG_COMMANDS,
    REQUIRED_COMMAND_LOG_COMMANDS,
    REQUIRED_COMMAND_LOG_IDS,
)


COMMAND_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
TARGET_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")
LOG_HEADER_KEYS = {
    "command_id",
    "target_run_id",
    "command",
    "argv_json",
    "started_at",
    "completed_at",
    "exit_code",
}
COMMAND_LOG_CLAIM_BOUNDARY = "target_command_logs_not_scorecard_update"

COMMAND_LOG_ENTRY_TEMPLATES = [
    {
        "command_id": "target_transfer_archive_verify",
        "required": True,
        "description": "Target-side transfer archive verification before preflight.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_transfer_archive_verify"],
    },
    {
        "command_id": "phase1_validation_suite",
        "required": True,
        "description": "Full RL Phase 1 validation suite on the target checkout before target artifacts mutate local blocked-state fixtures.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["phase1_validation_suite"],
    },
    {
        "command_id": "target_preflight",
        "required": True,
        "description": "Target preflight with --require-pass.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_preflight"],
    },
    {
        "command_id": "target_swe_probe",
        "required": True,
        "description": "Target SWE probe run.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_swe_probe"],
    },
    {
        "command_id": "target_verl_export",
        "required": True,
        "description": "Target VeRL JSONL/Parquet projection smoke.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_verl_export"],
    },
    {
        "command_id": "target_ray_warm_pool",
        "required": True,
        "description": "Target Ray/warm-pool probe.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_ray_warm_pool"],
    },
    {
        "command_id": "target_load_ladder",
        "required": True,
        "description": "Target load ladder producing m12_node_load_ladder/load_ladder_report.json.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_load_ladder"],
    },
    {
        "command_id": "target_soak",
        "required": True,
        "description": "Target soak producing m12_node_soak/soak_report.json.",
        "command": REQUIRED_COMMAND_LOG_COMMANDS["target_soak"],
    },
    {
        "command_id": "final_report",
        "required": False,
        "description": "Final report builder with --require-eligible; logged after required command-log gates are satisfied.",
        "command": OPTIONAL_COMMAND_LOG_COMMANDS["final_report"],
    },
    {
        "command_id": "promotion_audit",
        "required": False,
        "description": "Non-scoring promotion audit; logged after the eligible final report command.",
        "command": OPTIONAL_COMMAND_LOG_COMMANDS["promotion_audit"],
    },
]

COMMAND_LOG_MANIFEST_TEMPLATE = {
    "manifest_id": COMMAND_LOG_MANIFEST_ID,
    "claim_boundary": COMMAND_LOG_CLAIM_BOUNDARY,
    "scorecard_update_allowed": False,
    "m12_points_awarded": False,
    "all_required_logs_archived": False,
    "all_required_commands_passed": False,
    "target_run_ids": [],
    "latest_target_run_id": None,
    "commands": [
        {
            **entry,
            "status": "pending",
            "exit_code": None,
            "log_path": None,
            "sha256": None,
            "started_at": None,
            "completed_at": None,
            "notes": "",
        }
        for entry in COMMAND_LOG_ENTRY_TEMPLATES
    ],
    "required_command_ids": list(REQUIRED_COMMAND_LOG_IDS),
    "operator_notes": "",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def validate_command_id(command_id: str) -> list[str]:
    errors: list[str] = []
    if not command_id:
        errors.append("command_id must be non-empty")
    if not COMMAND_ID_PATTERN.fullmatch(command_id):
        errors.append("command_id must contain only letters, numbers, dot, underscore, and hyphen")
    return errors


def validate_target_run_id(target_run_id: str) -> list[str]:
    errors: list[str] = []
    if not target_run_id:
        errors.append("target_run_id must be non-empty")
    if not TARGET_RUN_ID_PATTERN.fullmatch(target_run_id):
        errors.append("target_run_id must contain only letters, numbers, dot, underscore, colon, and hyphen")
    return errors


def next_command_log_path(log_dir: Path, command_id: str) -> Path:
    errors = validate_command_id(command_id)
    if errors:
        raise ValueError("; ".join(errors))
    base_path = log_dir / f"{command_id}.log"
    if not base_path.exists():
        return base_path
    attempt = 2
    while True:
        candidate = log_dir / f"{command_id}.attempt-{attempt:03d}.log"
        if not candidate.exists():
            return candidate
        attempt += 1


def init_command_log_manifest() -> dict[str, Any]:
    return copy.deepcopy(COMMAND_LOG_MANIFEST_TEMPLATE)


def read_command_log_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return init_command_log_manifest()
    return json.loads(path.read_text(encoding="utf-8"))


def write_command_log_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def manifest_relative_log_path(manifest_path: Path, log_path: Path) -> str:
    try:
        return str(log_path.resolve().relative_to(manifest_path.parent.resolve()))
    except ValueError:
        return str(log_path)


def resolve_manifest_log_path(manifest_path: Path, raw_log_path: str) -> Path:
    path = Path(raw_log_path)
    if path.is_absolute():
        return path
    manifest_relative = manifest_path.parent / path
    if manifest_relative.exists():
        return manifest_relative
    return path


def validate_manifest_log_path(raw_log_path: str) -> list[str]:
    errors: list[str] = []
    if not raw_log_path:
        errors.append("log_path must be non-empty")
        return errors
    path = Path(raw_log_path)
    if path.is_absolute():
        errors.append("log_path must be relative to the command-log manifest directory")
    if any(part == ".." for part in path.parts):
        errors.append("log_path must not contain parent-directory traversal")
    return errors


def normalize_command_argv(command: str, argv: list[str] | None = None) -> list[str]:
    if argv is not None:
        return [str(part) for part in argv]
    try:
        parsed = shlex.split(command)
    except ValueError:
        parsed = []
    return parsed or [command]


def validate_command_argv(raw_argv: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw_argv, list) or not raw_argv:
        return ["argv must be a non-empty list"]
    if any(not isinstance(item, str) or item == "" for item in raw_argv):
        errors.append("argv entries must be non-empty strings")
    return errors


def validate_command_argv_congruence(raw_command: Any, raw_argv: Any) -> list[str]:
    command = str(raw_command or "")
    if not command:
        return ["command must be non-empty"]
    argv_errors = validate_command_argv(raw_argv)
    if argv_errors:
        return []
    if shlex.join([str(part) for part in raw_argv]) != command:
        return ["command must equal shlex.join(argv)"]
    return []


def validate_status_exit_code_congruence(raw_status: Any, raw_exit_code: Any) -> list[str]:
    status = str(raw_status or "")
    exit_code_is_int = isinstance(raw_exit_code, int) and not isinstance(raw_exit_code, bool)
    errors: list[str] = []
    if status not in {"pending", "passed", "failed"}:
        return ["status must be pending, passed, or failed"]
    if status == "pending":
        if raw_exit_code is not None:
            errors.append("pending commands must not have exit_code")
        return errors
    if not exit_code_is_int:
        return ["completed commands must have integer exit_code"]
    if status == "passed" and raw_exit_code != 0:
        errors.append("passed commands must have exit_code 0")
    if status == "failed" and raw_exit_code == 0:
        errors.append("failed commands must have nonzero exit_code")
    return errors


def collect_command_log_header_metadata(log_path: Path) -> tuple[dict[str, str], dict[str, int]]:
    metadata: dict[str, str] = {}
    counts: dict[str, int] = {}
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("# ") or ": " not in line:
                continue
            raw_key, raw_value = line[2:].rstrip("\n").split(": ", 1)
            if raw_key in LOG_HEADER_KEYS:
                counts[raw_key] = counts.get(raw_key, 0) + 1
                metadata.setdefault(raw_key, raw_value)
    return metadata, counts


def read_command_log_header_metadata(log_path: Path) -> dict[str, str]:
    metadata, _counts = collect_command_log_header_metadata(log_path)
    return metadata


def _nonempty_log_lines(log_path: Path) -> list[str]:
    return [
        line.rstrip("\n")
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.rstrip("\n") != ""
    ]


def validate_command_log_header_layout(
    *,
    log_path: Path,
    command_id: str,
    attempt: dict[str, Any],
    attempt_index: int,
) -> list[str]:
    errors: list[str] = []
    lines = _nonempty_log_lines(log_path)
    argv_json = json.dumps(attempt.get("argv"), ensure_ascii=True)
    expected_prefix = [f"# command_id: {command_id}"]
    if attempt.get("target_run_id") is not None:
        expected_prefix.append(f"# target_run_id: {attempt.get('target_run_id')}")
    expected_prefix.extend(
        [
            f"# command: {attempt.get('command')}",
            f"# argv_json: {argv_json}",
            f"# started_at: {attempt.get('started_at')}",
        ]
    )
    expected_suffix = [
        f"# completed_at: {attempt.get('completed_at')}",
        f"# exit_code: {attempt.get('exit_code')}",
    ]
    if lines[: len(expected_prefix)] != expected_prefix:
        errors.append(f"raw log header layout mismatch for {command_id} attempt {attempt_index}: preamble")
    if lines[-len(expected_suffix) :] != expected_suffix:
        errors.append(f"raw log header layout mismatch for {command_id} attempt {attempt_index}: trailer")
    return errors


def validate_command_log_header_metadata(
    *,
    log_path: Path,
    command_id: str,
    attempt: dict[str, Any],
    attempt_index: int,
) -> list[str]:
    errors: list[str] = []
    metadata, counts = collect_command_log_header_metadata(log_path)
    errors.extend(
        validate_command_log_header_layout(
            log_path=log_path,
            command_id=command_id,
            attempt=attempt,
            attempt_index=attempt_index,
        )
    )
    for key, count in sorted(counts.items()):
        if count > 1:
            errors.append(
                f"raw log header duplicate key for {command_id} attempt {attempt_index}: {key}"
            )
    expected = {
        "command_id": command_id,
        "command": str(attempt.get("command") or ""),
        "started_at": str(attempt.get("started_at") or ""),
        "completed_at": str(attempt.get("completed_at") or ""),
        "exit_code": str(attempt.get("exit_code")),
    }
    if attempt.get("target_run_id") is not None:
        expected["target_run_id"] = str(attempt.get("target_run_id") or "")
    for key, expected_value in expected.items():
        observed = metadata.get(key)
        if observed != expected_value:
            errors.append(
                f"raw log header mismatch for {command_id} attempt {attempt_index}: {key}"
            )
    raw_argv_json = metadata.get("argv_json")
    if raw_argv_json is None:
        errors.append(f"raw log header missing argv_json for {command_id} attempt {attempt_index}")
    else:
        try:
            observed_argv = json.loads(raw_argv_json)
        except json.JSONDecodeError:
            errors.append(f"raw log header invalid argv_json for {command_id} attempt {attempt_index}")
        else:
            if observed_argv != attempt.get("argv"):
                errors.append(
                    f"raw log header mismatch for {command_id} attempt {attempt_index}: argv_json"
                )
    return errors


def record_command_log_result(
    *,
    manifest_path: Path,
    command_id: str,
    command: str,
    argv: list[str] | None = None,
    log_path: Path,
    exit_code: int,
    started_at: str,
    completed_at: str,
    description: str | None = None,
    notes: str = "",
    target_run_id: str | None = None,
) -> dict[str, Any]:
    command_id_errors = validate_command_id(command_id)
    if command_id_errors:
        raise ValueError("; ".join(command_id_errors))
    if target_run_id is not None:
        target_run_errors = validate_target_run_id(target_run_id)
        if target_run_errors:
            raise ValueError("; ".join(target_run_errors))
    manifest = read_command_log_manifest(manifest_path)
    commands = [item for item in manifest.get("commands", []) if isinstance(item, dict)]
    entry = next((item for item in commands if item.get("command_id") == command_id), None)
    if entry is None:
        entry = {
            "command_id": command_id,
            "required": command_id in REQUIRED_COMMAND_LOG_IDS,
            "description": description or "",
        }
        commands.append(entry)
    if description is not None:
        entry["description"] = description
    relative_log_path = manifest_relative_log_path(manifest_path, log_path)
    log_path_errors = validate_manifest_log_path(relative_log_path)
    if log_path_errors:
        raise ValueError("; ".join(log_path_errors))
    argv_list = normalize_command_argv(command, argv)
    attempt_record = {
        "command": command,
        "argv": argv_list,
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "log_path": relative_log_path,
        "sha256": sha256_file(log_path),
        "started_at": started_at,
        "completed_at": completed_at,
        "notes": notes,
    }
    if target_run_id is not None:
        attempt_record["target_run_id"] = target_run_id
    attempts = entry.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts.append(dict(attempt_record))
    entry.update(
        {
            **attempt_record,
            "attempts": attempts,
        }
    )
    manifest["commands"] = commands
    manifest["required_command_ids"] = list(REQUIRED_COMMAND_LOG_IDS)
    target_run_ids = sorted(
        {
            str(item.get("target_run_id"))
            for item in commands
            if item.get("target_run_id")
        }
    )
    manifest["target_run_ids"] = target_run_ids
    manifest["latest_target_run_id"] = target_run_id or (target_run_ids[-1] if target_run_ids else None)

    required_ids = set(REQUIRED_COMMAND_LOG_IDS)
    archived_ids = {
        str(item.get("command_id"))
        for item in commands
        if item.get("command_id") in required_ids and item.get("log_path") and str(item.get("sha256") or "").startswith("sha256:")
    }
    passed_ids = {
        str(item.get("command_id"))
        for item in commands
        if item.get("command_id") in required_ids and item.get("status") == "passed"
    }
    manifest["all_required_logs_archived"] = required_ids <= archived_ids
    manifest["all_required_commands_passed"] = required_ids <= passed_ids
    write_command_log_manifest(manifest_path, manifest)
    return manifest


def validate_command_log_manifest(
    manifest_path: Path,
    *,
    required_command_ids: list[str] | None = None,
    require_passed: bool = True,
    verify_hashes: bool = True,
) -> list[str]:
    errors: list[str] = []
    manifest = read_command_log_manifest(manifest_path)
    canonical_required_ids = set(REQUIRED_COMMAND_LOG_IDS)
    raw_manifest_required_ids = manifest.get("required_command_ids")
    if not isinstance(raw_manifest_required_ids, list):
        errors.append("required_command_ids must equal canonical M12 required command IDs")
        manifest_required_ids: set[str] = set()
    else:
        manifest_required_id_list = [str(item) for item in raw_manifest_required_ids]
        manifest_required_ids = set(manifest_required_id_list)
        if manifest_required_id_list != list(REQUIRED_COMMAND_LOG_IDS):
            errors.append("required_command_ids must equal canonical M12 required command IDs")
    required_ids = set(required_command_ids or canonical_required_ids)
    commands = [item for item in manifest.get("commands", []) if isinstance(item, dict)]
    seen_command_ids: set[str] = set()
    duplicate_command_ids: set[str] = set()
    entries_by_id = {str(item.get("command_id") or ""): item for item in commands}
    allowed_command_ids = canonical_required_ids | set(OPTIONAL_COMMAND_LOG_COMMANDS)
    if manifest.get("manifest_id") != COMMAND_LOG_MANIFEST_ID:
        errors.append("manifest_id must be bb_zyphra_rl_phase1_m12_command_log_manifest_v1")
    if manifest.get("claim_boundary") != COMMAND_LOG_CLAIM_BOUNDARY:
        errors.append("claim_boundary must remain target_command_logs_not_scorecard_update")
    if manifest.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if manifest.get("m12_points_awarded") is not False:
        errors.append("m12_points_awarded must be false")
    for command_id in sorted(required_ids):
        for command_id_error in validate_command_id(command_id):
            errors.append(f"invalid required_command_id {command_id!r}: {command_id_error}")
    for entry in commands:
        command_id = str(entry.get("command_id") or "")
        if command_id in seen_command_ids:
            duplicate_command_ids.add(command_id)
        seen_command_ids.add(command_id)
        for command_id_error in validate_command_id(command_id):
            errors.append(f"invalid command_id {command_id!r}: {command_id_error}")
        if command_id and command_id not in allowed_command_ids:
            errors.append(f"unknown command entry: {command_id}")
        if command_id in allowed_command_ids:
            expected_required = command_id in canonical_required_ids
            if entry.get("required") is not expected_required:
                errors.append(f"required flag mismatch for {command_id}")
        target_run_id = entry.get("target_run_id")
        if target_run_id is not None:
            for target_run_error in validate_target_run_id(str(target_run_id)):
                errors.append(f"invalid target_run_id for {command_id}: {target_run_error}")
        raw_log_path = entry.get("log_path")
        if raw_log_path:
            for log_path_error in validate_manifest_log_path(str(raw_log_path)):
                errors.append(f"invalid log_path for {command_id}: {log_path_error}")
        attempts = entry.get("attempts")
        row_is_completed = entry.get("status") in {"passed", "failed"} or bool(entry.get("log_path")) or bool(entry.get("sha256"))
        for status_error in validate_status_exit_code_congruence(entry.get("status"), entry.get("exit_code")):
            errors.append(f"invalid status/exit_code for {command_id}: {status_error}")
        if row_is_completed and not isinstance(attempts, list):
            errors.append(f"completed command entry must preserve attempts: {command_id}")
        if attempts is not None:
            if not isinstance(attempts, list) or not attempts:
                errors.append(f"attempts must be a non-empty list when present: {command_id}")
            else:
                for index, attempt in enumerate(attempts, start=1):
                    if not isinstance(attempt, dict):
                        errors.append(f"attempt {index} for {command_id} must be an object")
                        continue
                    attempt_log_path = attempt.get("log_path")
                    if not attempt_log_path:
                        errors.append(f"attempt {index} for {command_id} missing log_path")
                    else:
                        for log_path_error in validate_manifest_log_path(str(attempt_log_path)):
                            errors.append(f"invalid attempt log_path for {command_id} attempt {index}: {log_path_error}")
                    attempt_sha = str(attempt.get("sha256") or "")
                    if not attempt_sha.startswith("sha256:"):
                        errors.append(f"attempt {index} for {command_id} missing sha256")
                    attempt_status = attempt.get("status")
                    if attempt_status not in {"passed", "failed"}:
                        errors.append(f"attempt {index} for {command_id} status must be passed or failed")
                    for status_error in validate_status_exit_code_congruence(
                        attempt_status,
                        attempt.get("exit_code"),
                    ):
                        errors.append(f"invalid status/exit_code for {command_id} attempt {index}: {status_error}")
                    for argv_error in validate_command_argv(attempt.get("argv")):
                        errors.append(f"invalid argv for {command_id} attempt {index}: {argv_error}")
                    for congruence_error in validate_command_argv_congruence(
                        attempt.get("command"), attempt.get("argv")
                    ):
                        errors.append(f"invalid command/argv for {command_id} attempt {index}: {congruence_error}")
                    attempt_target_run_id = attempt.get("target_run_id")
                    if attempt_target_run_id is not None:
                        for target_run_error in validate_target_run_id(str(attempt_target_run_id)):
                            errors.append(f"invalid target_run_id for {command_id} attempt {index}: {target_run_error}")
                    if verify_hashes and attempt_log_path and attempt_sha.startswith("sha256:"):
                        attempt_resolved_log_path = resolve_manifest_log_path(manifest_path, str(attempt_log_path))
                        if not attempt_resolved_log_path.is_file():
                            errors.append(f"attempt log file missing: {command_id} attempt {index}")
                        elif sha256_file(attempt_resolved_log_path) != attempt_sha:
                            errors.append(f"attempt log sha256 mismatch: {command_id} attempt {index}")
                        else:
                            errors.extend(
                                validate_command_log_header_metadata(
                                    log_path=attempt_resolved_log_path,
                                    command_id=command_id,
                                    attempt=attempt,
                                    attempt_index=index,
                                )
                            )
                if attempts and isinstance(attempts[-1], dict):
                    latest_attempt = attempts[-1]
                    for field in [
                        "command",
                        "argv",
                        "status",
                        "exit_code",
                        "log_path",
                        "sha256",
                        "started_at",
                        "completed_at",
                        "target_run_id",
                    ]:
                        if latest_attempt.get(field) != entry.get(field):
                            errors.append(f"latest attempt mismatch for {command_id}: {field}")
    for command_id in sorted(duplicate_command_ids):
        errors.append(f"duplicate command entry: {command_id}")
    for target_run_id in manifest.get("target_run_ids") or []:
        for target_run_error in validate_target_run_id(str(target_run_id)):
            errors.append(f"invalid manifest target_run_id {target_run_id!r}: {target_run_error}")
    latest_target_run_id = manifest.get("latest_target_run_id")
    if latest_target_run_id is not None:
        for target_run_error in validate_target_run_id(str(latest_target_run_id)):
            errors.append(f"invalid latest_target_run_id: {target_run_error}")
    row_target_run_ids = sorted(
        {
            str(item.get("target_run_id"))
            for item in commands
            if item.get("target_run_id")
        }
    )
    if manifest.get("target_run_ids") != row_target_run_ids:
        errors.append("target_run_ids must equal sorted target_run_id values from command rows")
    if latest_target_run_id is not None and str(latest_target_run_id) not in row_target_run_ids:
        errors.append("latest_target_run_id must be present in target_run_ids")
    archived_ids = {
        str(item.get("command_id"))
        for item in commands
        if item.get("command_id") in canonical_required_ids
        and item.get("log_path")
        and str(item.get("sha256") or "").startswith("sha256:")
    }
    passed_ids = {
        str(item.get("command_id"))
        for item in commands
        if item.get("command_id") in canonical_required_ids and item.get("status") == "passed"
    }
    expected_logs_archived = canonical_required_ids <= archived_ids
    expected_commands_passed = canonical_required_ids <= passed_ids
    if manifest.get("all_required_logs_archived") is not expected_logs_archived:
        errors.append("all_required_logs_archived must match required command log rows")
    if manifest.get("all_required_commands_passed") is not expected_commands_passed:
        errors.append("all_required_commands_passed must match required command statuses")
    for command_id in sorted(required_ids):
        entry = entries_by_id.get(command_id)
        if entry is None:
            errors.append(f"missing command entry: {command_id}")
            continue
        if require_passed and entry.get("status") != "passed":
            errors.append(f"command did not pass: {command_id}")
        raw_log_path = entry.get("log_path")
        if not raw_log_path:
            errors.append(f"missing log_path: {command_id}")
            continue
        log_path_errors = validate_manifest_log_path(str(raw_log_path))
        if log_path_errors:
            continue
        expected_sha = str(entry.get("sha256") or "")
        if not expected_sha.startswith("sha256:"):
            errors.append(f"missing sha256: {command_id}")
            continue
        if verify_hashes:
            resolved_log_path = resolve_manifest_log_path(manifest_path, str(raw_log_path))
            if not resolved_log_path.is_file():
                errors.append(f"log file missing: {command_id}")
                continue
            actual_sha = sha256_file(resolved_log_path)
            if actual_sha != expected_sha:
                errors.append(f"log sha256 mismatch: {command_id}")
    return errors
