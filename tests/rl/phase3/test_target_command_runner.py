from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path

from scripts.rl_phase3.run_phase3_target_command import _build_remote_command, _build_ssh_command, _safe_artifact_name, _validated_slurm_option, main


def test_ssh_command_quotes_remote_script_as_single_bash_argument() -> None:
    remote = "set -euo pipefail; export PHASE3_TARGET_RUN_ID=run id; echo ok"
    command = _build_ssh_command(ssh_alias="target", remote_command=remote)

    assert command == ["ssh", "target", f"bash -lc {shlex.quote(remote)}"]
    assert "bash -lc 'set -euo pipefail;" in command[2]


def test_remote_command_runs_payload_under_slurm_bash_context() -> None:
    remote = _build_remote_command(
        target_run_id="20260624T040000Z-slurm-243958",
        command_id="phase3 probe current",
        remote_zip="/tmp/phase3 probe current.zip",
        partition="gpu",
        job_name="bb p3 probe",
    )

    assert "export PHASE3_TARGET_RUN_ID=20260624T040000Z-slurm-243958" in remote
    assert "mktemp -d /shared/bb-p3-${USER:-root}/'phase3 probe current'.XXXXXX" in remote
    assert "unzip -q '/tmp/phase3 probe current.zip' -d \"$WORK\"" in remote
    assert "test -x ./run.sh" in remote
    assert "srun --partition=gpu --job-name='bb p3 probe' --gres=gpu:8" in remote
    assert shlex.quote("echo PHASE3_NODE=$(hostname); echo PHASE3_SLURM_JOB_ID=${SLURM_JOB_ID:-}; ./run.sh") in remote


def test_remote_command_accepts_explicit_slurm_targeting_options() -> None:
    remote = _build_remote_command(
        target_run_id="20260706T193804Z-slurm-pending",
        command_id="phase4_nemo_agentloop_canonical_scratch",
        remote_zip="/tmp/payload.zip",
        partition="gpu",
        job_name="bb-p4-nemo-agentloop",
        nodelist="cnode-[19,148]",
        constraint="mi300x",
        reservation="bmoe",
        qos="normal",
        gres="gpu:1",
        mem="512M",
    )

    assert "--gres=gpu:1" in remote
    assert "--mem=512M" in remote
    assert "--nodelist='cnode-[19,148]'" in remote
    assert "--constraint=mi300x" in remote
    assert "--reservation=bmoe" in remote
    assert "--qos=normal" in remote
    assert "20260706T193804Z-slurm-pending" in remote



def test_slurm_targeting_option_validation_rejects_shell_characters() -> None:
    assert _validated_slurm_option("cnode-[19,148]", name="nodelist") == "cnode-[19,148]"
    try:
        _validated_slurm_option("cnode-19;rm", name="nodelist")
    except ValueError as exc:
        assert "--nodelist contains unsupported characters" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid Slurm option was accepted")

def test_safe_artifact_name_rejects_path_segments() -> None:
    bad_id = _safe_artifact_name("../bad id.log", fallback="fallback")
    assert bad_id == f"bad_id_log-{hashlib.sha256(b'../bad id.log').hexdigest()[:12]}"
    assert _safe_artifact_name("a b", fallback="fallback") != _safe_artifact_name("a/b", fallback="fallback")
    assert _safe_artifact_name("...", fallback="fallback").startswith("fallback-")


def test_main_uses_sanitized_artifact_paths(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    safe_command_id = _safe_artifact_name("../bad id", fallback="phase3_command")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command)
        if command[0] == "scp":
            assert command[1] == str(payload)
            assert command[2] == f"target:/tmp/{safe_command_id}.zip"
            assert kwargs["timeout"] == 20
            return subprocess.CompletedProcess(command, 0, "", "")
        raise subprocess.TimeoutExpired(command, timeout=3600)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--partition",
            "gpu",
            "--job-name",
            "bb p3 probe",
            "--command-id",
            "../bad id",
            "--target-run-id",
            "20260624T040000Z-slurm-243958",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert len(calls) == 2
    ssh_payload = calls[1][2]
    assert "../bad id" not in ssh_payload
    assert f"/tmp/{safe_command_id}.zip" in ssh_payload
    assert f"/shared/bb-p3-${{USER:-root}}/{safe_command_id}.XXXXXX" in ssh_payload

    out = tmp_path / "out"
    assert result == 124
    assert (out / "command_logs" / f"{safe_command_id}.log").exists()
    assert not (out / ".." / "bad id.log").exists()
    assert (out / f"{safe_command_id}_blocked.json").exists()
    assert not (out / "phase3_command_log_manifest.json").exists()
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    assert attempts["attempts"][0]["command_id"] == "../bad id"
    assert attempts["attempts"][0]["raw_log_path"] == f"command_logs/{safe_command_id}.log"
    raw_log = out / "command_logs" / f"{safe_command_id}.log"
    expected_log_sha = "sha256:" + hashlib.sha256(raw_log.read_bytes()).hexdigest()
    assert attempts["attempts"][0]["raw_log_sha256"] == expected_log_sha



def test_main_honors_explicit_scp_timeout(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    scp_timeouts: list[int] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            scp_timeouts.append(kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "", "")
        raise subprocess.TimeoutExpired(command, timeout=3600)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--partition",
            "gpu",
            "--job-name",
            "bb-p3-probe",
            "--command-id",
            "phase3_probe",
            "--target-run-id",
            "20260624T040000Z-slurm-243958",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(tmp_path / "out"),
            "--scp-timeout-seconds",
            "90",
        ]
    )

    assert result == 124
    assert scp_timeouts == [90]

def test_main_writes_only_passed_runs_to_canonical_manifest(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    out.mkdir()
    (out / "phase3_command_attempts_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "bb.rl.phase3.command_attempts_manifest.v1",
                "target_run_id": "20260624T040000Z-slurm-243958",
                "attempts": [
                    {"command_id": "phase3_probe", "status": "failed"},
                    {"command_id": "other_probe", "status": "failed"},
                ],
            }
        )
        + "\n"
    )

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "PHASE3_NODE=cnode-1\nPHASE3_SLURM_JOB_ID=12345\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--partition",
            "gpu",
            "--job-name",
            "bb p3 probe",
            "--command-id",
            "phase3_probe",
            "--target-run-id",
            "20260624T040000Z-slurm-243958",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(out),
        ]
    )

    assert result == 0
    manifest = json.loads((out / "phase3_command_log_manifest.json").read_text())
    assert manifest["commands"][0]["command_id"] == "phase3_probe"
    assert manifest["commands"][0]["status"] == "passed"
    assert manifest["commands"][0]["slurm_job_id"] == "12345"
    assert manifest["commands"][0]["node"] == "cnode-1"
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    assert attempts["attempts"] == [{"command_id": "other_probe", "status": "failed"}]


def test_failed_rerun_removes_stale_canonical_manifest_row(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    attempts = 0

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        nonlocal attempts
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        attempts += 1
        if attempts == 1:
            return subprocess.CompletedProcess(command, 0, "PHASE3_NODE=cnode-1\nPHASE3_SLURM_JOB_ID=12345\n", "")
        raise subprocess.TimeoutExpired(command, timeout=3600)

    monkeypatch.setattr(subprocess, "run", fake_run)
    argv = [
        "--ssh-alias",
        "target",
        "--partition",
        "gpu",
        "--job-name",
        "bb p3 probe",
        "--command-id",
        "phase3_probe",
        "--target-run-id",
        "20260624T040000Z-slurm-243958",
        "--payload-zip",
        str(payload),
        "--output-dir",
        str(out),
    ]

    assert main(argv) == 0
    assert (out / "phase3_command_log_manifest.json").exists()

    assert main(argv) == 124
    assert not (out / "phase3_command_log_manifest.json").exists()
    retry_attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    assert retry_attempts["attempts"][0]["command_id"] == "phase3_probe"
    assert retry_attempts["attempts"][0]["status"] == "failed"


def test_passed_rerun_resets_stale_target_manifest(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    out.mkdir()
    (out / "phase3_command_log_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "bb.rl.phase3.command_log_manifest.v1",
                "target_run_id": "20260624T040000Z-slurm-111111",
                "commands": [{"command_id": "stale", "target_run_id": "20260624T040000Z-slurm-111111"}],
            }
        )
        + "\n"
    )

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "PHASE3_NODE=cnode-2\nPHASE3_SLURM_JOB_ID=67890\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        main(
            [
                "--ssh-alias",
                "target",
                "--partition",
                "gpu",
                "--job-name",
                "bb p3 probe",
                "--command-id",
                "phase3_probe",
                "--target-run-id",
                "20260624T040000Z-slurm-243958",
                "--payload-zip",
                str(payload),
                "--output-dir",
                str(out),
            ]
        )
        == 0
    )

    manifest = json.loads((out / "phase3_command_log_manifest.json").read_text())
    assert manifest["target_run_id"] == "20260624T040000Z-slurm-243958"
    assert [row["command_id"] for row in manifest["commands"]] == ["phase3_probe"]


def test_inline_component_report_paths_are_sanitized(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "a/b",
        "component": "../evil",
        "passed": True,
    }

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = (
            "PHASE3_NODE=cnode-1\n"
            "PHASE3_SLURM_JOB_ID=12345\n"
            f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        main(
            [
                "--ssh-alias",
                "target",
                "--partition",
                "gpu",
                "--job-name",
                "bb p3 probe",
                "--command-id",
                "phase3_probe",
                "--target-run-id",
                "20260624T040000Z-slurm-243958",
                "--payload-zip",
                str(payload),
                "--output-dir",
                str(out),
            ]
        )
        == 0
    )

    safe_component = _safe_artifact_name("../evil", fallback=_safe_artifact_name("a/b", fallback="phase3_probe"))
    safe_report = _safe_artifact_name("a/b", fallback="phase3_probe")
    report_path = out / safe_component / f"{safe_report}.json"
    assert report_path.exists()
    emitted = json.loads(report_path.read_text())
    assert emitted["component"] == "../evil"
    assert emitted["report_id"] == "a/b"
    assert emitted["target_run_id"] == "20260624T040000Z-slurm-243958"
    assert not (out / ".." / "evil" / "b.json").exists()


def test_inline_component_report_empty_artifact_paths_are_injected(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "artifact-report",
        "component": "runtime_probe",
        "passed": True,
        "artifact_paths": {},
    }
    stdout = (
        "PHASE3_NODE=cnode-1\n"
        "PHASE3_SLURM_JOB_ID=12345\n"
        f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n"
    )
    stderr = "remote diagnostic line\n"
    raw_log = stdout + stderr

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, stdout, stderr)

    def resolve_artifact_path(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else out / path

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--partition",
            "gpu",
            "--job-name",
            "bb p3 probe",
            "--command-id",
            "phase3_probe",
            "--target-run-id",
            "20260624T040000Z-slurm-243958",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(out),
        ]
    )

    assert result == 0
    report_path = out / "runtime_probe" / "artifact-report.json"
    emitted = json.loads(report_path.read_text())
    artifact_paths = emitted["artifact_paths"]
    component_report_path = resolve_artifact_path(artifact_paths["component_report_json"])
    command_log_path = resolve_artifact_path(artifact_paths["command_log"])
    assert component_report_path == report_path.resolve()
    assert component_report_path.exists()
    assert command_log_path.exists()
    assert command_log_path.read_text() == raw_log
    manifest = json.loads((out / "phase3_command_log_manifest.json").read_text())
    assert manifest["commands"][0]["command_id"] == "phase3_probe"
    assert manifest["commands"][0]["status"] == "passed"

def test_inline_component_passed_false_blocks_canonical_manifest(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "blocked-report",
        "component": "nemo_gym_agentloop_smoke",
        "passed": False,
        "blocked_reason": "canonical_runtime_imports_missing",
    }

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = (
            "PHASE3_NODE=cnode-1\n"
            "PHASE3_SLURM_JOB_ID=12345\n"
            f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--partition",
            "gpu",
            "--job-name",
            "bb p3 probe",
            "--command-id",
            "phase3_probe",
            "--target-run-id",
            "20260624T040000Z-slurm-243958",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(out),
        ]
    )

    assert result == 1
    assert not (out / "phase3_command_log_manifest.json").exists()
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    row = attempts["attempts"][0]
    assert row["command_id"] == "phase3_probe"
    assert row["status"] == "failed"
    assert row["blocked_reason"] == "inline_component_failed"
    assert row["component_passed"] is False
    assert row["component_failed_count"] == 1
    assert row["component_blocked_reasons"] == ["canonical_runtime_imports_missing"]
    report_path = out / "nemo_gym_agentloop_smoke" / "blocked-report.json"
    assert report_path.exists()
    emitted = json.loads(report_path.read_text())
    assert emitted["passed"] is False
    assert emitted["target_run_id"] == "20260624T040000Z-slurm-243958"


def test_inline_component_blocked_rerun_removes_stale_canonical_row(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    out.mkdir()
    (out / "phase3_command_log_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "bb.rl.phase3.command_log_manifest.v1",
                "target_run_id": "20260624T040000Z-slurm-243958",
                "commands": [{"command_id": "phase3_probe", "status": "passed"}],
            }
        )
        + "\n"
    )
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "blocked-report",
        "component": "nemo_gym_agentloop_smoke",
        "passed": False,
        "blocked_reason": "canonical_runtime_imports_missing",
    }

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = (
            "PHASE3_NODE=cnode-1\n"
            "PHASE3_SLURM_JOB_ID=12345\n"
            f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        main(
            [
                "--ssh-alias",
                "target",
                "--partition",
                "gpu",
                "--job-name",
                "bb p3 probe",
                "--command-id",
                "phase3_probe",
                "--target-run-id",
                "20260624T040000Z-slurm-243958",
                "--payload-zip",
                str(payload),
                "--output-dir",
                str(out),
            ]
        )
        == 1
    )
    assert not (out / "phase3_command_log_manifest.json").exists()
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    assert attempts["attempts"][0]["blocked_reason"] == "inline_component_failed"


def test_multiple_inline_component_reports_preserve_blocked_reasons(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    reports = [
        {
            "schema_version": "bb.rl.phase3.component_report.v1",
            "report_id": "passed-report",
            "component": "runtime_probe",
            "passed": True,
        },
        {
            "schema_version": "bb.rl.phase3.component_report.v1",
            "report_id": "blocked-import",
            "component": "nemo_gym_agentloop_smoke",
            "passed": False,
            "blocked_reason": "canonical_runtime_imports_missing",
        },
        {
            "schema_version": "bb.rl.phase3.component_report.v1",
            "report_id": "blocked-harbor",
            "component": "harbor_lifecycle",
            "passed": False,
            "blocked_reason": "target_harbor_endpoint_missing",
        },
    ]

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = "PHASE3_NODE=cnode-1\nPHASE3_SLURM_JOB_ID=12345\n" + "".join(
            f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n" for report in reports
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        main(
            [
                "--ssh-alias",
                "target",
                "--partition",
                "gpu",
                "--job-name",
                "bb p3 probe",
                "--command-id",
                "phase3_probe",
                "--target-run-id",
                "20260624T040000Z-slurm-243958",
                "--payload-zip",
                str(payload),
                "--output-dir",
                str(out),
            ]
        )
        == 1
    )
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    row = attempts["attempts"][0]
    assert row["component_passed"] is False
    assert row["component_failed_count"] == 2
    assert row["component_blocked_reasons"] == [
        "canonical_runtime_imports_missing",
        "target_harbor_endpoint_missing",
    ]


def test_inline_component_missing_passed_blocks_canonical_manifest(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "ambiguous-report",
        "component": "runtime_probe",
    }

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = (
            "PHASE3_NODE=cnode-1\n"
            "PHASE3_SLURM_JOB_ID=12345\n"
            f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--partition",
            "gpu",
            "--job-name",
            "bb p3 probe",
            "--command-id",
            "phase3_probe",
            "--target-run-id",
            "20260624T040000Z-slurm-243958",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(out),
        ]
    )

    assert result == 1
    assert not (out / "phase3_command_log_manifest.json").exists()
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    row = attempts["attempts"][0]
    assert row["blocked_reason"] == "inline_component_failed"
    assert row["component_passed"] is False
    assert row["component_failed_count"] == 1
    assert row["component_blocked_reasons"] == ["inline_component_not_passed"]


def test_bad_inline_component_report_does_not_pass_canonical_manifest(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = (
            "PHASE3_NODE=cnode-1\n"
            "PHASE3_SLURM_JOB_ID=12345\n"
            "PHASE3_COMPONENT_REPORT_JSON={bad-json\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        main(
            [
                "--ssh-alias",
                "target",
                "--partition",
                "gpu",
                "--job-name",
                "bb p3 probe",
                "--command-id",
                "phase3_probe",
                "--target-run-id",
                "20260624T040000Z-slurm-243958",
                "--payload-zip",
                str(payload),
                "--output-dir",
                str(out),
            ]
        )
        == 1
    )

    assert not (out / "phase3_command_log_manifest.json").exists()
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    assert attempts["attempts"][0]["command_id"] == "phase3_probe"
    assert attempts["attempts"][0]["status"] == "failed"
    assert attempts["attempts"][0]["blocked_reason"] == "invalid_inline_report"


def test_mixed_good_and_bad_inline_reports_leave_no_partial_artifacts(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    out = tmp_path / "out"
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "good-report",
        "component": "component",
        "passed": True,
    }

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, "", "")
        stdout = (
            "PHASE3_NODE=cnode-1\n"
            "PHASE3_SLURM_JOB_ID=12345\n"
            f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(report)}\n"
            "PHASE3_COMPONENT_REPORT_JSON={bad-json\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        main(
            [
                "--ssh-alias",
                "target",
                "--partition",
                "gpu",
                "--job-name",
                "bb p3 probe",
                "--command-id",
                "phase3_probe",
                "--target-run-id",
                "20260624T040000Z-slurm-243958",
                "--payload-zip",
                str(payload),
                "--output-dir",
                str(out),
            ]
        )
        == 1
    )

    assert not (out / "component" / "good-report.json").exists()
    assert not (out / "phase3_command_log_manifest.json").exists()
    attempts = json.loads((out / "phase3_command_attempts_manifest.json").read_text())
    assert attempts["attempts"][0]["status"] == "failed"
    assert attempts["attempts"][0]["blocked_reason"] == "invalid_inline_report"
