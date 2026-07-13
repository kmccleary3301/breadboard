from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12.command_logs import (  # noqa: E402
    manifest_relative_log_path,
    next_command_log_path,
    record_command_log_result,
    utc_now_iso,
    validate_manifest_log_path,
    validate_target_run_id,
)


def _strip_separator(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        return command[1:]
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one M12 target command and archive its raw log.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs"),
    )
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--target-run-id", default=os.environ.get("M12_TARGET_RUN_ID"))
    parser.add_argument("--description", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--allow-failure",
        action="store_true",
        help="Record the command result but exit zero even if the wrapped command fails.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = _strip_separator(args.command)
    if not command:
        raise SystemExit("missing wrapped command after --")

    try:
        log_path = next_command_log_path(args.log_dir, args.command_id)
    except ValueError as exc:
        raise SystemExit(f"invalid command id: {exc}") from exc
    if args.target_run_id:
        target_run_errors = validate_target_run_id(args.target_run_id)
        if target_run_errors:
            raise SystemExit(f"invalid target run id: {'; '.join(target_run_errors)}")
    relative_log_path = manifest_relative_log_path(args.manifest, log_path)
    log_path_errors = validate_manifest_log_path(relative_log_path)
    if log_path_errors:
        raise SystemExit(f"invalid log path: {'; '.join(log_path_errors)}")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    command_text = shlex.join(command)
    started_at = utc_now_iso()
    exit_code = 1
    completed_at = started_at
    notes = args.notes
    output_ended_with_newline = True
    child_env = os.environ.copy()
    if args.target_run_id:
        child_env["M12_TARGET_RUN_ID"] = args.target_run_id

    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# command_id: {args.command_id}\n")
        if args.target_run_id:
            handle.write(f"# target_run_id: {args.target_run_id}\n")
        handle.write(f"# command: {command_text}\n")
        handle.write(f"# argv_json: {json.dumps(command, ensure_ascii=True)}\n")
        handle.write(f"# started_at: {started_at}\n")
        handle.flush()
        try:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            exit_code = 127
            spawn_error = f"spawn_error: {type(exc).__name__}: {exc}"
            print(spawn_error, file=sys.stderr)
            handle.write(f"# {spawn_error}\n")
            notes = f"{notes}\n{spawn_error}".strip()
        else:
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                handle.write(line)
                output_ended_with_newline = line.endswith("\n")
            exit_code = process.wait()
        completed_at = utc_now_iso()
        if not output_ended_with_newline:
            handle.write("\n")
        handle.write(f"# completed_at: {completed_at}\n")
        handle.write(f"# exit_code: {exit_code}\n")
    record_command_log_result(
        manifest_path=args.manifest,
        command_id=args.command_id,
        command=command_text,
        argv=command,
        log_path=log_path,
        exit_code=exit_code,
        started_at=started_at,
        completed_at=completed_at,
        description=args.description,
        notes=notes,
        target_run_id=args.target_run_id,
    )
    if exit_code != 0 and not args.allow_failure:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
