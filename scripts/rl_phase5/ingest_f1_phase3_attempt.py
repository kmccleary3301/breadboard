from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f1_preflight import (
    PARTITION,
    TARGET_ALIAS,
    canonical_json_bytes,
    validate_scratch,
)
from scripts.rl_phase5.run_f1_target_command import (
    decode_result_archive,
    decode_runner_archive,
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _option(argv: list[str], name: str) -> str:
    try:
        index = argv.index(name)
        value = argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"outer Phase 3 command lacks {name}") from exc
    if not isinstance(value, str) or not value:
        raise ValueError(f"outer Phase 3 command has invalid {name}")
    return value


def ingest(
    *,
    phase3_output: Path,
    attempt_id: str,
    scratch_root: Path,
) -> Path:
    phase3_output = phase3_output.resolve(strict=True)
    manifest_path = phase3_output / "phase3_command_log_manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    commands = manifest.get("commands")
    if not isinstance(commands, list):
        raise ValueError("outer Phase 3 command manifest is invalid")
    matches = [row for row in commands if row.get("command_id") == attempt_id]
    if len(matches) != 1:
        raise ValueError("exactly one outer Phase 3 command row is required")
    row: dict[str, Any] = matches[0]
    if (
        row.get("status") != "passed"
        or row.get("exit_code") != 0
        or not str(row.get("slurm_job_id") or "").isdigit()
        or not str(row.get("node") or "")
        or row.get("blocked_reason") not in ("", None)
        or row.get("component_failed_count") != 0
    ):
        raise ValueError("outer Phase 3 command did not pass transport execution")
    argv = row.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ValueError("outer Phase 3 argv is invalid")
    if _option(argv, "--ssh-alias") != TARGET_ALIAS:
        raise ValueError("outer target alias mismatch")
    if _option(argv, "--partition") != PARTITION:
        raise ValueError("outer partition mismatch")
    if _option(argv, "--command-id") != attempt_id:
        raise ValueError("outer command identity mismatch")
    relative_log = PurePosixPath(str(row.get("raw_log_path") or ""))
    if relative_log.is_absolute() or ".." in relative_log.parts or not relative_log.parts:
        raise ValueError("outer raw log path is unsafe")
    raw_log_path = (phase3_output / Path(*relative_log.parts)).resolve(strict=True)
    if phase3_output not in raw_log_path.parents:
        raise ValueError("outer raw log escapes Phase 3 output")
    raw_log = raw_log_path.read_bytes()
    if _sha256(raw_log_path) != row.get("raw_log_sha256"):
        raise ValueError("outer raw log hash mismatch")

    scratch_root = scratch_root.resolve()
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = scratch_root / attempt_id
    if destination.exists():
        raise FileExistsError(destination)
    staging = Path(tempfile.mkdtemp(prefix=f".{attempt_id}-ingest-", dir=scratch_root))
    try:
        decode_runner_archive(raw_log, staging)
        decode_result_archive((staging / "target.stdout").read_bytes(), staging)
        (staging / "exit_code").write_text("0\n", encoding="ascii")
        (staging / "attempt.json").write_bytes(
            canonical_json_bytes(
                {
                    "schema_version": "bb.rl.f1.phase3-ingested-attempt.v1",
                    "attempt_id": attempt_id,
                    "outer_target_run_id": row.get("target_run_id"),
                    "outer_slurm_job_id": row["slurm_job_id"],
                    "outer_node": row["node"],
                }
            )
        )
        outer = staging / "outer"
        outer.mkdir(mode=0o700)
        (outer / "phase3-command-log-manifest.json").write_bytes(manifest_raw)
        (outer / "phase3-command.log").write_bytes(raw_log)
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    validate_scratch(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest an F1 result produced only through the Phase 3 Slurm runner"
    )
    parser.add_argument("--phase3-output", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    args = parser.parse_args()
    destination = ingest(
        phase3_output=args.phase3_output,
        attempt_id=args.attempt_id,
        scratch_root=args.scratch_root,
    )
    print(canonical_json_bytes({"attempt_path": str(destination)}).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
