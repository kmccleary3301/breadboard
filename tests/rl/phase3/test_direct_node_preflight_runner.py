from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

import scripts.rl_phase3.run_phase3_direct_node_preflight as direct_node_preflight
from scripts.rl_phase3.run_phase3_direct_node_preflight import ENDPOINT_ENV_VARS, _presence_shell, _remote_precheck_command, _remote_run_command, main


def _payload_zip(tmp_path):  # noqa: ANN001, ANN202
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"zip")
    return payload


def _load_assessment(output_dir, command_id: str):  # noqa: ANN001, ANN202
    return json.loads((output_dir / f"{command_id}_direct_node_preflight.json").read_text())


def _sha256_file(path):  # noqa: ANN001, ANN202
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_precheck_builder_records_presence_only_endpoint_checks_and_no_canonical_manifest() -> None:
    remote = _remote_precheck_command(
        direct_run_id="direct-20260706",
        command_id="precheck_probe",
        remote_root="/shared/bb-p3-root",
        image="rocm-image:dev",
        hip_visible_devices="0,1",
        create_remote_root=False,
    )

    assert "PHASE4_DIRECT_MODE=precheck" in remote
    assert "PHASE4_DIRECT_SCHEDULER=none_direct_ssh" in remote
    assert "REMOTE_ROOT=present" in remote
    assert "RUNTIME_VENV=present" in remote
    assert "RUNTIME_IMAGE=present" in remote
    assert "phase3_command_log_manifest.json" not in remote
    for name in ENDPOINT_ENV_VARS:
        assert f"ENV_{name}=present" in remote
        assert f"ENV_{name}=absent" in remote
        assert f"ENV_{name}=${{{name}" not in remote


def test_presence_shell_separates_endpoint_if_blocks_for_remote_precheck() -> None:
    presence = _presence_shell(("BREADBOARD_ORS_TOKEN", "HF_HOME"))
    remote = _remote_precheck_command(
        direct_run_id="direct-20260706",
        command_id="precheck_probe",
        remote_root="/shared/bb-p3-root",
        image="rocm-image:dev",
        hip_visible_devices="0,1",
        create_remote_root=False,
    )

    assert "fi if [ -n" not in presence
    assert "fi if [ -n" not in remote
    assert 'ENV_BREADBOARD_ORS_TOKEN=absent; fi; if [ -n "${HF_HOME:-}" ]' in presence
    assert all(f"; fi; if [ -n \"${{{name}:-}}\" ]" in remote for name in ENDPOINT_ENV_VARS[1:])

def test_precheck_main_writes_non_promotional_assessment_hash_and_endpoint_booleans(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / "out"
    monkeypatch.setenv("BREADBOARD_ORS_TOKEN", "super-secret-token")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command)
        assert command[0] == "ssh"
        assert kwargs["env"]["BREADBOARD_ORS_TOKEN"] == "super-secret-token"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=precheck\n"
                "PHASE4_DIRECT_SCHEDULER=none_direct_ssh\n"
                "REMOTE_ROOT=present\n"
                "RUNTIME_VENV=present\n"
                "RUNTIME_IMAGE=present\n"
                "CMD_docker=present:/usr/bin/docker\n"
                "DEVICE_KFD=present\n"
                "ENV_BREADBOARD_ORS_TOKEN=present\n"
                "ENV_BREADBOARD_OPENREWARD_TOKEN=absent\n"
                "ENV_HF_HOME=present\n"
                "IMAGE_ID=sha256:image-id\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            "direct_precheck",
            "--direct-run-id",
            "direct-20260706T193804Z",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "precheck",
            "--image",
            "rocm-image:dev",
            "--hip-visible-devices",
            "0,1,2,3",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    assert calls[0][0] == "ssh"
    assert not (output_dir / "phase3_command_log_manifest.json").exists()

    assessment = _load_assessment(output_dir, "direct_precheck")
    assert assessment["claim_boundary"] == "phase4_direct_node_dev_preflight_only_not_phase3_promotion"
    assert assessment["promotional"] is False
    assert assessment["scorecard_update_allowed"] is False
    assert assessment["canonical_phase3_command_log_manifest_eligible"] is False
    assert assessment["canonical_phase3_command_log_manifest_reason"] == "not_slurm_direct_ssh_preflight"
    assert assessment["scheduler"] == "none_direct_ssh"
    assert assessment["slurm_job_id_present"] is False
    assert assessment["target_run_id"] is None
    assert assessment["status"] == "passed"
    assert assessment["passed"] is True
    assert assessment["runtime"] == {"remote_root": "present", "venv": "present", "image": "present"}
    assert assessment["endpoint_env_presence"]["BREADBOARD_ORS_TOKEN"] is True
    assert assessment["endpoint_env_presence"]["BREADBOARD_OPENREWARD_TOKEN"] is False
    assert assessment["endpoint_env_presence"]["HF_HOME"] is True
    assert all(isinstance(value, bool) for value in assessment["endpoint_env_presence"].values())
    assert "super-secret-token" not in json.dumps(assessment, sort_keys=True)

    raw_log = output_dir / "command_logs" / "direct_precheck.log"
    expected_raw_hash = _sha256_file(raw_log)
    assert assessment["raw_log_path"] == "command_logs/direct_precheck.log"
    assert assessment["raw_log_sha256"] == expected_raw_hash
    assert assessment["input_hashes"]["raw_log"] == expected_raw_hash


@pytest.mark.parametrize(
    ("missing_key", "runtime_field"),
    [
        ("REMOTE_ROOT", "remote_root"),
        ("RUNTIME_VENV", "venv"),
        ("RUNTIME_IMAGE", "image"),
    ],
)
def test_precheck_readiness_requires_runtime_root_venv_and_image(tmp_path, monkeypatch, missing_key: str, runtime_field: str) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / f"out_{missing_key.lower()}"
    values = {"REMOTE_ROOT": "present", "RUNTIME_VENV": "present", "RUNTIME_IMAGE": "present"}
    values[missing_key] = "absent"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=precheck\n"
                f"REMOTE_ROOT={values['REMOTE_ROOT']}\n"
                f"RUNTIME_VENV={values['RUNTIME_VENV']}\n"
                f"RUNTIME_IMAGE={values['RUNTIME_IMAGE']}\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            f"precheck_{missing_key.lower()}",
            "--direct-run-id",
            f"direct-{missing_key.lower()}",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "precheck",
        ]
    )

    assert result == 1
    assert not (output_dir / "phase3_command_log_manifest.json").exists()
    assessment = _load_assessment(output_dir, f"precheck_{missing_key.lower()}")
    assert assessment["passed"] is False
    assert assessment["status"] == "not_ready"
    assert assessment["runtime"][runtime_field] == "absent"


@pytest.mark.parametrize(
    ("blocked_reason", "exit_code", "missing_line"),
    [
        ("direct_runtime_image_missing", 93, "RUNTIME_IMAGE=absent"),
        ("direct_runtime_venv_missing", 94, "RUNTIME_VENV=absent"),
    ],
)
def test_run_mode_runtime_gate_blocks_before_payload_execution_and_never_writes_canonical_manifest(
    tmp_path, monkeypatch, blocked_reason: str, exit_code: int, missing_line: str
) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / f"out_{blocked_reason}"
    events: list[str] = []

    def fake_enforce(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append("enforce")
        assert command[0] == "ssh"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append(command[0])
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        remote_payload = command[2]
        assert remote_payload.index(blocked_reason) < remote_payload.index("WORK=$(mktemp")
        assert remote_payload.index(blocked_reason) < remote_payload.index("python3 -m zipfile -e")
        assert remote_payload.index(blocked_reason) < remote_payload.index("./run.sh")
        return subprocess.CompletedProcess(
            command,
            exit_code,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=run\n"
                f"PHASE4_BLOCKED_REASON={blocked_reason}\n"
                f"{missing_line}\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(direct_node_preflight, "enforce_command_request", fake_enforce)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            blocked_reason,
            "--direct-run-id",
            f"direct-{blocked_reason}",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "run",
        ]
    )

    assert result == exit_code
    assert events == ["enforce", "scp", "ssh"]
    assert not (output_dir / "phase3_command_log_manifest.json").exists()
    assessment = _load_assessment(output_dir, blocked_reason)
    assert assessment["mode"] == "run"
    assert assessment["passed"] is False
    assert assessment["status"] == "blocked"
    assert assessment["blocked_reason"] == blocked_reason
    assert assessment["canonical_phase3_command_log_manifest_eligible"] is False
    assert assessment["component_reports"] == []


def test_run_mode_nonzero_remote_without_blocked_reason_reports_remote_command_failed(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / "out_remote_command_failed"
    events: list[str] = []

    def fake_enforce(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append("enforce")
        assert command[0] == "ssh"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append(command[0])
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            17,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=run\n"
            ),
            stderr="payload exited 17\n",
        )

    monkeypatch.setattr(direct_node_preflight, "enforce_command_request", fake_enforce)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            "run_remote_command_failed",
            "--direct-run-id",
            "direct-remote-command-failed",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "run",
        ]
    )

    assert result == 17
    assert events == ["enforce", "scp", "ssh"]
    assert not (output_dir / "phase3_command_log_manifest.json").exists()
    assessment = _load_assessment(output_dir, "run_remote_command_failed")
    assert assessment["mode"] == "run"
    assert assessment["exit_code"] == 17
    assert assessment["passed"] is False
    assert assessment["status"] == "blocked"
    assert assessment["blocked_reason"] == "remote_command_failed"
    assert assessment["component_reports"] == []


def test_run_mode_nonzero_with_component_report_uses_component_blocker(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / "out_component_blocked"

    def fake_enforce(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        assert command[0] == "ssh"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        component = {"component": "payload", "passed": False, "blocked_reason": "real_rollout_server_not_used"}
        return subprocess.CompletedProcess(
            command,
            2,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=run\n"
                f"PHASE3_COMPONENT_REPORT_JSON={json.dumps(component)}\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(direct_node_preflight, "enforce_command_request", fake_enforce)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            "run_component_blocked",
            "--direct-run-id",
            "direct-component-blocked",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "run",
        ]
    )

    assert result == 2
    assessment = _load_assessment(output_dir, "run_component_blocked")
    assert assessment["passed"] is False
    assert assessment["status"] == "blocked"
    assert assessment["blocked_reason"] == "real_rollout_server_not_used"
    assert assessment["component_blocked_reasons"] == ["real_rollout_server_not_used"]
    assert assessment["component_failed_count"] == 1
    assert assessment["component_reports"][0]["blocked_reason"] == "real_rollout_server_not_used"


def test_run_mode_success_enforces_remote_command_before_payload_copy_and_ssh(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / "out_success"
    events: list[str] = []

    def fake_enforce(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append("enforce")
        assert command[0] == "ssh"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append(command[0])
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=run\n"
                f"PHASE3_COMPONENT_REPORT_JSON={json.dumps({'component': 'payload', 'passed': True})}\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(direct_node_preflight, "enforce_command_request", fake_enforce)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            "run_success",
            "--direct-run-id",
            "direct-run-success",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "run",
        ]
    )

    assert result == 0
    assert events == ["enforce", "scp", "ssh"]
    assert not (output_dir / "phase3_command_log_manifest.json").exists()
    assessment = _load_assessment(output_dir, "run_success")
    assert assessment["mode"] == "run"
    assert assessment["passed"] is True
    assert assessment["status"] == "passed"
    assert assessment["blocked_reason"] == ""
    assert assessment["canonical_phase3_command_log_manifest_eligible"] is False



def test_run_mode_exit_zero_without_inline_component_evidence_blocks_without_canonical_manifest(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / "out_missing_payload_evidence"
    events: list[str] = []

    def fake_enforce(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append("enforce")
        assert command[0] == "ssh"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        events.append(command[0])
        if command[0] == "scp":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "PHASE4_DIRECT_NODE=perf-eng-2\n"
                "PHASE4_DIRECT_MODE=run\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(direct_node_preflight, "enforce_command_request", fake_enforce)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            "run_missing_payload_evidence",
            "--direct-run-id",
            "direct-missing-payload-evidence",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "run",
        ]
    )

    assert result == 1
    assert events == ["enforce", "scp", "ssh"]
    assert not (output_dir / "phase3_command_log_manifest.json").exists()
    assessment = _load_assessment(output_dir, "run_missing_payload_evidence")
    assert assessment["claim_boundary"] == "phase4_direct_node_dev_preflight_only_not_phase3_promotion"
    assert assessment["promotional"] is False
    assert assessment["scorecard_update_allowed"] is False
    assert assessment["mode"] == "run"
    assert assessment["exit_code"] == 0
    assert assessment["passed"] is False
    assert assessment["status"] == "blocked"
    assert assessment["blocked_reason"] == "payload_evidence_missing"
    assert assessment["canonical_phase3_command_log_manifest_eligible"] is False
    assert assessment["canonical_phase3_command_log_manifest_reason"] == "not_slurm_direct_ssh_preflight"
    assert assessment["component_reports"] == []

def test_run_command_builder_orders_runtime_gates_before_payload_unpack() -> None:
    remote = _remote_run_command(
        direct_run_id="direct-run",
        command_id="payload_probe",
        remote_zip="/tmp/payload_probe.zip",
        remote_root="/shared/bb-p3-root",
        image="rocm-image:dev",
        hip_visible_devices="0",
    )
    phase3_target_fragments = [
        fragment.strip()
        for fragment in remote.split(";")
        if "PHASE3_TARGET_RUN_ID" in fragment
    ]

    assert phase3_target_fragments == ["export PHASE3_TARGET_RUN_ID="]
    assert "export PHASE3_TARGET_RUN_ID=direct" not in remote
    assert not any("direct-run" in fragment for fragment in phase3_target_fragments)
    assert not any("PHASE4_DIRECT_RUN_ID" in fragment for fragment in phase3_target_fragments)

    assert remote.index("direct_runtime_root_missing") < remote.index("WORK=$(mktemp")
    assert remote.index("direct_runtime_venv_missing") < remote.index("WORK=$(mktemp")
    assert remote.index("direct_runtime_image_missing") < remote.index("WORK=$(mktemp")
    assert remote.index("WORK=$(mktemp") < remote.index("python3 -m zipfile -e")
    assert remote.index("python3 -m zipfile -e") < remote.index("test -f ./run.sh")
    assert remote.index("test -f ./run.sh") < remote.index("bash ./run.sh")
    assert "phase3_command_log_manifest.json" not in remote


def test_run_mode_scp_failure_writes_payload_transfer_assessment_without_canonical_manifest(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    payload = _payload_zip(tmp_path)
    output_dir = tmp_path / "out"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202, ARG001
        calls.append(command)
        assert command[0] == "scp"
        return subprocess.CompletedProcess(command, 23, stdout="", stderr="scp: permission denied\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = main(
        [
            "--ssh-alias",
            "target",
            "--command-id",
            "run_scp_failure",
            "--direct-run-id",
            "direct-scp-failure",
            "--payload-zip",
            str(payload),
            "--output-dir",
            str(output_dir),
            "--mode",
            "run",
        ]
    )

    assert result == 23
    assert [call[0] for call in calls] == ["scp"]
    assert not (output_dir / "phase3_command_log_manifest.json").exists()
    assessment = _load_assessment(output_dir, "run_scp_failure")
    assert assessment["passed"] is False
    assert assessment["status"] == "blocked"
    assert assessment["blocked_reason"] == "payload_transfer_failed"
    assert assessment["canonical_phase3_command_log_manifest_eligible"] is False
    raw_log = output_dir / "command_logs" / "run_scp_failure.log"
    assert raw_log.read_text() == "scp: permission denied\n"
    assert assessment["raw_log_sha256"] == _sha256_file(raw_log)
