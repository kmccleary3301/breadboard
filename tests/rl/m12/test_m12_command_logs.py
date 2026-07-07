from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from breadboard.rl.m12 import record_command_log_result, validate_command_log_manifest
from breadboard.rl.m12.final_report import REQUIRED_COMMAND_LOG_IDS


REPO_ROOT = Path(__file__).resolve().parents[3]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_wrapper_style_log(
    log_path: Path,
    *,
    command_id: str,
    command: str = "python -m pytest -q",
    exit_code: int = 0,
    started_at: str = "2026-06-18T00:00:00Z",
    completed_at: str = "2026-06-18T00:00:01Z",
    target_run_id: str = "m12-target-run-test",
    body: str | None = None,
) -> None:
    log_path.write_text(
        "\n".join(
            [
                f"# command_id: {command_id}",
                f"# target_run_id: {target_run_id}",
                f"# command: {command}",
                f"# argv_json: {json.dumps(shlex.split(command), ensure_ascii=True)}",
                f"# started_at: {started_at}",
                body if body is not None else f"{command_id} ok",
                f"# completed_at: {completed_at}",
                f"# exit_code: {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_valid_logged_manifest(tmp_path: Path, command_id: str = "target_preflight") -> Path:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / f"{command_id}.log"
    _write_wrapper_style_log(log_path, command_id=command_id)
    record_command_log_result(
        manifest_path=manifest_path,
        command_id=command_id,
        command="python -m pytest -q",
        log_path=log_path,
        exit_code=0,
        started_at="2026-06-18T00:00:00Z",
        completed_at="2026-06-18T00:00:01Z",
        target_run_id="m12-target-run-test",
    )
    return manifest_path


def _write_full_valid_logged_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for command_id in REQUIRED_COMMAND_LOG_IDS:
        log_path = log_dir / f"{command_id}.log"
        _write_wrapper_style_log(log_path, command_id=command_id)
        record_command_log_result(
            manifest_path=manifest_path,
            command_id=command_id,
            command="python -m pytest -q",
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-18T00:00:00Z",
            completed_at="2026-06-18T00:00:01Z",
            target_run_id="m12-target-run-test",
        )
    return manifest_path


def test_logged_command_cli_records_hash_verified_manifest(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "target_preflight",
            "--target-run-id",
            "m12-target-run-test",
            "--",
            sys.executable,
            "-c",
            "print('m12 log ok')",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "m12 log ok" in result.stdout
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    assert manifest["claim_boundary"] == "target_command_logs_not_scorecard_update"
    assert manifest["scorecard_update_allowed"] is False
    assert manifest["m12_points_awarded"] is False
    assert entry["status"] == "passed"
    assert entry["exit_code"] == 0
    assert entry["target_run_id"] == "m12-target-run-test"
    assert entry["argv"] == [sys.executable, "-c", "print('m12 log ok')"]
    assert entry["attempts"][0]["argv"] == [sys.executable, "-c", "print('m12 log ok')"]
    assert entry["log_path"] == "logs/target_preflight.log"
    assert entry["sha256"].startswith("sha256:")
    assert manifest["target_run_ids"] == ["m12-target-run-test"]
    assert manifest["latest_target_run_id"] == "m12-target-run-test"
    assert (log_dir / "target_preflight.log").exists()
    assert "# argv_json: " in (log_dir / "target_preflight.log").read_text(encoding="utf-8")
    assert (
        validate_command_log_manifest(
            manifest_path,
            required_command_ids=["target_preflight"],
            require_passed=True,
            verify_hashes=True,
        )
        == []
    )


def test_logged_command_cli_records_failure_and_exits_nonzero(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "target_preflight",
            "--",
            sys.executable,
            "-c",
            "import sys; print('m12 fail'); sys.exit(7)",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 7
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    assert entry["status"] == "failed"
    assert entry["exit_code"] == 7
    assert "command did not pass: target_preflight" in validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )


def test_logged_command_cli_separates_trailer_after_output_without_newline(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "target_preflight",
            "--target-run-id",
            "m12-target-run-test",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('no trailing newline')",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    log_text = (log_dir / "target_preflight.log").read_text(encoding="utf-8")
    assert "no trailing newline\n# completed_at:" in log_text
    assert (
        validate_command_log_manifest(
            manifest_path,
            required_command_ids=["target_preflight"],
            require_passed=True,
            verify_hashes=True,
        )
        == []
    )


def test_logged_command_cli_records_spawn_failure_and_exits_nonzero(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "target_preflight",
            "--target-run-id",
            "m12-target-run-test",
            "--",
            "definitely-not-a-real-m12-command",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 127
    assert "spawn_error: FileNotFoundError" in result.stderr
    log_path = log_dir / "target_preflight.log"
    assert log_path.exists()
    assert "spawn_error: FileNotFoundError" in log_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    assert entry["status"] == "failed"
    assert entry["exit_code"] == 127
    assert entry["target_run_id"] == "m12-target-run-test"
    assert entry["argv"] == ["definitely-not-a-real-m12-command"]
    assert entry["log_path"] == "logs/target_preflight.log"
    assert entry["sha256"].startswith("sha256:")
    assert "spawn_error: FileNotFoundError" in entry["notes"]
    assert "command did not pass: target_preflight" in validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )


def test_logged_command_cli_rejects_unsafe_target_run_id_before_running(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "target_preflight",
            "--target-run-id",
            "../bad-target-run",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid target run id" in result.stderr
    assert "should not run" not in result.stdout
    assert not manifest_path.exists()
    assert not (log_dir / "target_preflight.log").exists()


def test_logged_command_cli_exports_target_run_id_to_child_environment(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "target_preflight",
            "--target-run-id",
            "m12-target-run-test",
            "--",
            sys.executable,
            "-c",
            "import os; print(os.environ.get('M12_TARGET_RUN_ID'))",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "m12-target-run-test" in result.stdout
    log_path = log_dir / "target_preflight.log"
    assert "m12-target-run-test" in log_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    assert entry["target_run_id"] == "m12-target-run-test"


def test_logged_command_cli_rejects_unsafe_log_dir_before_running(tmp_path) -> None:
    manifest_path = tmp_path / "manifest" / "command_log_manifest.json"
    outside_log_dir = tmp_path.parent / f"{tmp_path.name}_outside_logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(outside_log_dir),
            "--command-id",
            "target_preflight",
            "--target-run-id",
            "m12-target-run-test",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid log path" in result.stderr
    assert "should not run" not in result.stdout
    assert not manifest_path.exists()
    assert not outside_log_dir.exists()


def test_logged_command_cli_preserves_rerun_attempt_logs(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    base_command = [
        sys.executable,
        "scripts/rl_phase1/run_m12_logged_command.py",
        "--manifest",
        str(manifest_path),
        "--log-dir",
        str(log_dir),
        "--command-id",
        "target_preflight",
        "--",
        sys.executable,
        "-c",
    ]

    first = subprocess.run(
        [*base_command, "print('first target attempt')"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    second = subprocess.run(
        [*base_command, "print('second target attempt')"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert (log_dir / "target_preflight.log").exists()
    assert (log_dir / "target_preflight.attempt-002.log").exists()
    assert "first target attempt" in (log_dir / "target_preflight.log").read_text(encoding="utf-8")
    assert "second target attempt" in (log_dir / "target_preflight.attempt-002.log").read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    assert entry["status"] == "passed"
    assert entry["log_path"] == "logs/target_preflight.attempt-002.log"
    assert len(entry["attempts"]) == 2
    assert entry["attempts"][0]["log_path"] == "logs/target_preflight.log"
    assert entry["attempts"][1]["log_path"] == "logs/target_preflight.attempt-002.log"
    assert entry["attempts"][0]["argv"] == [sys.executable, "-c", "print('first target attempt')"]
    assert entry["attempts"][1]["argv"] == [sys.executable, "-c", "print('second target attempt')"]
    assert entry["argv"] == [sys.executable, "-c", "print('second target attempt')"]
    assert (
        validate_command_log_manifest(
            manifest_path,
            required_command_ids=["target_preflight"],
            require_passed=True,
            verify_hashes=True,
        )
        == []
    )


def test_command_log_manifest_validator_rejects_completed_row_without_attempts(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    del entry["attempts"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "completed command entry must preserve attempts: target_preflight" in errors


def test_command_log_manifest_validator_rejects_unsafe_attempt_metadata(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["attempts"][0]["log_path"] = "../target_preflight.log"
    entry["attempts"][0]["target_run_id"] = "../bad-target-run"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "invalid attempt log_path for target_preflight attempt 1: log_path must not contain parent-directory traversal" in errors
    assert "invalid target_run_id for target_preflight attempt 1: target_run_id must contain only letters, numbers, dot, underscore, colon, and hyphen" in errors
    assert "latest attempt mismatch for target_preflight: log_path" in errors
    assert "latest attempt mismatch for target_preflight: target_run_id" in errors


def test_command_log_manifest_validator_rejects_latest_attempt_drift(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["status"] = "failed"
    entry["exit_code"] = 99
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "latest attempt mismatch for target_preflight: status" in errors
    assert "latest attempt mismatch for target_preflight: exit_code" in errors


def test_command_log_manifest_validator_rejects_status_exit_code_incongruence(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["status"] = "passed"
    entry["exit_code"] = 4
    entry["attempts"][0]["status"] = "passed"
    entry["attempts"][0]["exit_code"] = 4
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=False,
    )

    assert "invalid status/exit_code for target_preflight: passed commands must have exit_code 0" in errors
    assert (
        "invalid status/exit_code for target_preflight attempt 1: passed commands must have exit_code 0"
        in errors
    )


def test_command_log_manifest_validator_rejects_unknown_command_rows(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commands"].append(
        {
            "command_id": "operator_diagnostic",
            "required": False,
            "description": "unexpected target command row",
            "status": "pending",
            "exit_code": None,
            "log_path": None,
            "sha256": None,
            "started_at": None,
            "completed_at": None,
            "notes": "",
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "unknown command entry: operator_diagnostic" in errors


def test_command_log_manifest_validator_rejects_invalid_argv_and_latest_drift(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["argv"] = ["python", "-m", "pytest", "-q"]
    entry["attempts"][0]["argv"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "invalid argv for target_preflight attempt 1: argv must be a non-empty list" in errors
    assert "latest attempt mismatch for target_preflight: argv" in errors


def test_command_log_manifest_validator_rejects_command_argv_incongruence(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["argv"] = ["python", "-m", "pytest", "tests/rl", "-q"]
    entry["attempts"][0]["argv"] = ["python", "-m", "pytest", "tests/rl", "-q"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert (
        "invalid command/argv for target_preflight attempt 1: command must equal shlex.join(argv)"
        in errors
    )


def test_command_log_manifest_validator_rejects_raw_log_header_drift(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    log_path = tmp_path / "logs" / "target_preflight.log"
    log_text = log_path.read_text(encoding="utf-8")
    log_path.write_text(
        log_text.replace("# command: python -m pytest -q", "# command: python -m pytest tests/rl -q"),
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    new_hash = _sha256_file(log_path)
    entry["sha256"] = new_hash
    entry["attempts"][0]["sha256"] = new_hash
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "raw log header mismatch for target_preflight attempt 1: command" in errors


def test_command_log_manifest_validator_rejects_raw_log_header_layout_drift(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    log_path = tmp_path / "logs" / "target_preflight.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    command_line = lines.pop(2)
    lines.insert(5, command_line)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    new_hash = _sha256_file(log_path)
    entry["sha256"] = new_hash
    entry["attempts"][0]["sha256"] = new_hash
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "raw log header layout mismatch for target_preflight attempt 1: preamble" in errors


def test_command_log_manifest_validator_rejects_duplicate_raw_log_header_keys(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "target_preflight.log"
    _write_wrapper_style_log(
        log_path,
        command_id="target_preflight",
        body="normal output\n# command: python -m pytest -q\nmore output",
    )
    record_command_log_result(
        manifest_path=manifest_path,
        command_id="target_preflight",
        command="python -m pytest -q",
        log_path=log_path,
        exit_code=0,
        started_at="2026-06-18T00:00:00Z",
        completed_at="2026-06-18T00:00:01Z",
        target_run_id="m12-target-run-test",
    )

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "raw log header duplicate key for target_preflight attempt 1: command" in errors


def test_logged_command_cli_rejects_unsafe_command_id_before_running(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_dir = tmp_path / "logs"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_logged_command.py",
            "--manifest",
            str(manifest_path),
            "--log-dir",
            str(log_dir),
            "--command-id",
            "../target_preflight",
            "--",
            sys.executable,
            "-c",
            "print('should not run')",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid command id" in result.stderr
    assert "should not run" not in result.stdout
    assert not manifest_path.exists()
    assert not (tmp_path / "target_preflight.log").exists()


def test_record_command_log_result_rejects_unsafe_command_id_before_manifest_write(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    log_path = tmp_path / "logs" / "target_preflight.log"
    log_path.parent.mkdir()
    log_path.write_text("ok\n", encoding="utf-8")

    with pytest.raises(ValueError, match="command_id must contain only"):
        record_command_log_result(
            manifest_path=manifest_path,
            command_id="../target_preflight",
            command="python -m pytest -q",
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-18T00:00:00Z",
            completed_at="2026-06-18T00:00:01Z",
            target_run_id="m12-target-run-test",
        )

    assert not manifest_path.exists()


def test_record_command_log_result_rejects_log_path_outside_manifest_tree(tmp_path) -> None:
    manifest_path = tmp_path / "command_log_manifest.json"
    outside_dir = tmp_path.parent / f"{tmp_path.name}_outside_logs"
    outside_dir.mkdir()
    log_path = outside_dir / "target_preflight.log"
    log_path.write_text("ok\n", encoding="utf-8")

    with pytest.raises(ValueError, match="log_path must be relative"):
        record_command_log_result(
            manifest_path=manifest_path,
            command_id="target_preflight",
            command="python -m pytest -q",
            log_path=log_path,
            exit_code=0,
            started_at="2026-06-18T00:00:00Z",
            completed_at="2026-06-18T00:00:01Z",
            target_run_id="m12-target-run-test",
        )

    assert not manifest_path.exists()


def test_command_log_manifest_validator_rejects_duplicate_command_entries(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    manifest["commands"].append(dict(entry))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "duplicate command entry: target_preflight" in errors


def test_command_log_manifest_validator_rejects_boundary_drift(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["claim_boundary"] = "scorecard_update_allowed"
    manifest["scorecard_update_allowed"] = True
    manifest["m12_points_awarded"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "claim_boundary must remain target_command_logs_not_scorecard_update" in errors
    assert "scorecard_update_allowed must be false" in errors
    assert "m12_points_awarded must be false" in errors


def test_command_log_manifest_validator_rejects_required_flag_drift(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    optional_entry = next(item for item in manifest["commands"] if item["command_id"] == "final_report")
    required_entry["required"] = False
    optional_entry["required"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "required flag mismatch for target_preflight" in errors
    assert "required flag mismatch for final_report" in errors


def test_command_log_manifest_validator_rejects_unsafe_manifest_command_id(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commands"].append({"command_id": "../escape", "status": "pending"})
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "invalid command_id '../escape': command_id must contain only letters, numbers, dot, underscore, and hyphen" in errors


def test_command_log_manifest_validator_rejects_invalid_target_run_ids(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["target_run_id"] = "../bad"
    manifest["target_run_ids"] = ["m12-target-run-test", "../bad"]
    manifest["latest_target_run_id"] = "../bad"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "invalid target_run_id for target_preflight: target_run_id must contain only letters, numbers, dot, underscore, colon, and hyphen" in errors
    assert "invalid manifest target_run_id '../bad': target_run_id must contain only letters, numbers, dot, underscore, colon, and hyphen" in errors
    assert "invalid latest_target_run_id: target_run_id must contain only letters, numbers, dot, underscore, colon, and hyphen" in errors


def test_command_log_manifest_validator_rejects_unsafe_log_paths(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["commands"] if item["command_id"] == "target_preflight")
    entry["log_path"] = "../target_preflight.log"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "invalid log_path for target_preflight: log_path must not contain parent-directory traversal" in errors

    entry["log_path"] = str((tmp_path / "logs" / "target_preflight.log").resolve())
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "invalid log_path for target_preflight: log_path must be relative to the command-log manifest directory" in errors


def test_command_log_manifest_validator_rejects_stale_target_run_id_summary(tmp_path) -> None:
    manifest_path = _write_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["target_run_ids"] = []
    manifest["latest_target_run_id"] = "m12-target-run-missing"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "target_run_ids must equal sorted target_run_id values from command rows" in errors
    assert "latest_target_run_id must be present in target_run_ids" in errors


def test_command_log_manifest_validator_rejects_stale_required_summary_flags(tmp_path) -> None:
    manifest_path = _write_full_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["all_required_logs_archived"] is True
    assert manifest["all_required_commands_passed"] is True
    manifest["all_required_logs_archived"] = False
    manifest["all_required_commands_passed"] = False
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(manifest_path, require_passed=True, verify_hashes=True)

    assert "all_required_logs_archived must match required command log rows" in errors
    assert "all_required_commands_passed must match required command statuses" in errors


def test_command_log_manifest_validator_rejects_narrowed_required_command_ids(tmp_path) -> None:
    manifest_path = _write_full_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["required_command_ids"] = ["target_preflight"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "required_command_ids must equal canonical M12 required command IDs" in errors


def test_command_log_manifest_validator_rejects_missing_required_command_ids(tmp_path) -> None:
    manifest_path = _write_full_valid_logged_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["required_command_ids"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_command_log_manifest(
        manifest_path,
        required_command_ids=["target_preflight"],
        require_passed=True,
        verify_hashes=True,
    )

    assert "required_command_ids must equal canonical M12 required command IDs" in errors
