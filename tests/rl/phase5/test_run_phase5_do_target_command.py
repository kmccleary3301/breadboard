from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentic_coder_prototype.compilation.contracts import canonical_json_bytes, canonical_json_loads

from scripts.rl_phase5.scan_phase5_artifact_secrets import scan_artifact
from scripts.rl_phase5.run_phase5_do_target_command import (
    _build_remote_command,
    _sanitize_metadata,
    _validate_payload,
    main,
)


def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = (0o100000 | mode) << 16
    return info


def _payload(path: Path) -> Path:
    run_raw = b"#!/bin/sh\nexit 0\n"
    manifest = canonical_json_bytes(
        {
            "member_count": 1,
            "members": [
                {
                    "mode": "0500",
                    "path": "run.sh",
                    "sha256": "sha256:" + hashlib.sha256(run_raw).hexdigest(),
                    "size_bytes": len(run_raw),
                }
            ],
        }
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(_zip_info("run.sh", 0o500), run_raw)
        archive.writestr(_zip_info("payload_manifest.json", 0o400), manifest)
    return path


def _f3_payload(
    path: Path,
    source_manifest: bytes,
    *,
    declared_sha256: str | None = None,
) -> Path:
    run_raw = b"#!/bin/sh\nexit 0\n"
    source_sha256 = declared_sha256 or (
        "sha256:" + hashlib.sha256(source_manifest).hexdigest()
    )
    members = [
        {
            "mode": "0500",
            "path": "run.sh",
            "sha256": "sha256:" + hashlib.sha256(run_raw).hexdigest(),
            "size_bytes": len(run_raw),
        },
        {
            "mode": "0400",
            "path": "source_manifest.json",
            "sha256": source_sha256,
            "size_bytes": len(source_manifest),
        },
    ]
    payload_manifest = canonical_json_bytes(
        {
            "member_count": len(members),
            "members": members,
            "source_manifest_sha256": source_sha256,
        }
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(_zip_info("run.sh", 0o500), run_raw)
        archive.writestr(
            _zip_info("source_manifest.json", 0o400), source_manifest
        )
        archive.writestr(
            _zip_info("payload_manifest.json", 0o400), payload_manifest
        )
    return path


def _mode_payload(
    path: Path,
    *,
    runtime_mode: int = 0o400,
    declared_runtime_mode: str = "0400",
    include_runtime: bool = True,
    runtime_symlink: bool = False,
) -> Path:
    run_raw = b"#!/bin/sh\nexit 0\n"
    runtime_raw = b"print('runtime')\n"
    rows = [
        {
            "mode": "0500",
            "path": "run.sh",
            "sha256": "sha256:" + hashlib.sha256(run_raw).hexdigest(),
            "size_bytes": len(run_raw),
        },
        {
            "mode": declared_runtime_mode,
            "path": "runtime.py",
            "sha256": "sha256:" + hashlib.sha256(runtime_raw).hexdigest(),
            "size_bytes": len(runtime_raw),
        },
    ]
    manifest = canonical_json_bytes(
        {"member_count": len(rows), "members": rows}
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(_zip_info("run.sh", 0o500), run_raw)
        if include_runtime:
            mode = 0o120777 if runtime_symlink else 0o100000 | runtime_mode
            info = zipfile.ZipInfo("runtime.py")
            info.external_attr = mode << 16
            archive.writestr(info, runtime_raw)
        archive.writestr(
            _zip_info("payload_manifest.json", 0o400), manifest
        )
    return path


def test_f3_payload_verifier_accepts_exact_canonical_source_manifest(
    tmp_path: Path,
) -> None:
    source_manifest = b'{"archive":{"path":"breadboard-source.tar.gz"},"members":[]}\n'

    _validate_payload(_f3_payload(tmp_path / "payload.zip", source_manifest))


@pytest.mark.parametrize(
    ("case", "kwargs", "message"),
    (
        ("missing", {"include_runtime": False}, "inventory"),
        ("writable", {"runtime_mode": 0o600}, "mode is unsafe"),
        ("executable", {"runtime_mode": 0o500}, "mode is unsafe"),
        ("symlink", {"runtime_symlink": True}, "symbolic link"),
        (
            "manifest-mode-mismatch",
            {"declared_runtime_mode": "0440"},
            "manifest-mode mismatch",
        ),
    ),
)
def test_payload_safe_mode_attacks_reject_before_transfer(
    tmp_path: Path,
    case: str,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    payload = _mode_payload(tmp_path / f"{case}.zip", **kwargs)

    with pytest.raises(ValueError, match=message):
        _validate_payload(payload)


@pytest.mark.parametrize(
    "source_manifest",
    (
        b'{"archive":{"path":"breadboard-source.tar.gz"},"members":[]}',
        b'{"archive":{"path":"breadboard-source.tar.gz"},"members":[]}\n\n',
        b'{"archive":{"path":"breadboard-source.tar.gz"},"members":[]} \n',
        b'{"members":[],"archive":{"path":"breadboard-source.tar.gz"}}\n',
    ),
    ids=("missing-lf", "extra-lf", "trailing-space", "reordered"),
)
def test_f3_payload_verifier_rejects_noncanonical_source_manifest_before_transfer(
    tmp_path: Path,
    source_manifest: bytes,
) -> None:
    payload = _f3_payload(tmp_path / "payload.zip", source_manifest)

    with pytest.raises(ValueError, match="source manifest"):
        _validate_payload(payload)


def test_f3_payload_verifier_rejects_source_manifest_digest_mismatch(
    tmp_path: Path,
) -> None:
    source_manifest = b'{"archive":{"path":"breadboard-source.tar.gz"},"members":[]}\n'
    payload = _f3_payload(
        tmp_path / "payload.zip",
        source_manifest,
        declared_sha256="sha256:" + "0" * 64,
    )

    with pytest.raises(ValueError, match="manifest"):
        _validate_payload(payload)


def test_f3_payload_verifier_rejects_runtime_pin_missing_from_source_members(
    tmp_path: Path,
) -> None:
    source_manifest = (
        b'{"members":[],"runtime_source_pins":'
        b'[{"path":"breadboard/rl/harness/service.py"}]}\n'
    )
    payload = _f3_payload(tmp_path / "payload.zip", source_manifest)

    with pytest.raises(ValueError, match="runtime pin"):
        _validate_payload(payload)


def _metadata(*, droplet_id: str = "99112233", hostname: str = "bb-scratch-1", region: str = "nyc3") -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.phase5-do-metadata.v1",
        "provider": "digitalocean",
        "droplet_id": droplet_id,
        "hostname": hostname,
        "region": region,
        "ip_addresses": [
            {"type": "private", "version": "ipv4", "ip_address": "10.20.0.8"},
            {"type": "public", "version": "ipv4", "ip_address": "203.0.113.8"},
        ],
        "features": {"dhcp_enabled": True},
        "tags": ["breadboard", "scratch"],
    }


def _component() -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.phase5-f6-restart-replay-report.v1",
        "report_id": "f6-do-scratch",
        "promotion_authority": False,
        "scorecard_authority": False,
    }


def _stdout(*, metadata: dict[str, Any] | None = None, components: tuple[dict[str, Any], ...] = ()) -> str:
    lines: list[bytes] = []
    if metadata is not None:
        lines.append(b"PHASE5_DO_METADATA_JSON=" + canonical_json_bytes(metadata))
    lines.extend(
        b"PHASE3_COMPONENT_REPORT_JSON=" + canonical_json_bytes(component)
        for component in components
    )
    return b"\n".join(lines).decode("utf-8") + ("\n" if lines else "")


def _argv(payload: Path, output: Path, *extra: str) -> list[str]:
    receipt_path = output.with_name(output.name + "-secret-scan.json")
    receipt_path.write_bytes(canonical_json_bytes(scan_artifact(payload)))
    receipt_sha256 = "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    runtime_path = output.with_name(output.name + "-runtime-input.json")
    runtime_path.write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "bb.rl.phase5-do-runtime-input.v1",
                "command_id": "f6-scratch",
                "target_run_id": "20260713T120000Z-do-scratch-1",
                "ssh_alias": "do-scratch",
                "provider": "digitalocean",
                "expected_provider_identity": {
                    "droplet_id": "99112233",
                    "region": "nyc3",
                    "hostname": "bb-scratch-1",
                },
                "expected_image": {
                    "id": "sha256:" + "1" * 64,
                    "reference": "example/image:pinned",
                },
                "payload_sha256": "sha256:"
                + hashlib.sha256(payload.read_bytes()).hexdigest(),
                "secret_scan_receipt_sha256": receipt_sha256,
            }
        )
    )
    return [
        "--runtime-input",
        str(runtime_path),
        "--secret-scan-receipt",
        str(receipt_path),
        "--payload-zip",
        str(payload),
        "--output-dir",
        str(output),
        *extra,
    ]


def _install_runs(monkeypatch: pytest.MonkeyPatch, remote: SimpleNamespace) -> list[list[str]]:
    calls: list[list[str]] = []
    responses = [SimpleNamespace(returncode=0, stdout="", stderr=""), remote]

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(argv)
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _manifest(output: Path) -> dict[str, Any]:
    raw = (output / "phase5_do_target_command_manifest.json").read_bytes()
    value = canonical_json_loads(raw)
    assert raw == canonical_json_bytes(value)
    return value


def test_do_runner_persists_sanitized_component_and_canonical_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    calls = _install_runs(
        monkeypatch,
        SimpleNamespace(
            returncode=0,
            stdout=_stdout(metadata=_metadata(), components=(_component(),)),
            stderr="",
        ),
    )

    result = main(
        _argv(
            payload,
            output,
            "--maximum-cost-usd",
            "4.50",
            "--ttl-seconds",
            "7200",
            "--teardown-authority-ref",
            "do-teardown-ticket-123",
        )
    )

    assert result == 0
    assert calls[0][0:2] == ["scp", "--"]
    assert calls[1][0:3] == ["ssh", "--", "do-scratch"]
    remote_command = calls[1][3]
    assert "srun" not in remote_command
    assert "metadata/v1.json" in remote_command
    assert "PHASE3_TARGET_RUN_ID" in remote_command
    assert "BREADBOARD_TARGET_RUN_ID" in remote_command
    assert "mktemp -d" in remote_command

    manifest = _manifest(output)
    assert manifest["promotion_authority"] is False
    assert manifest["scorecard_authority"] is False
    row = manifest["commands"][0]
    assert row["status"] == "passed"
    assert row["provider"] == "digitalocean"
    assert row["observed_provider_metadata"] == _metadata()
    assert "vendor_data" not in json.dumps(row["observed_provider_metadata"])
    assert "public_keys" not in json.dumps(row["observed_provider_metadata"])
    assert row["provider_controls"]["provider_promotion_prerequisites_complete"] is True
    assert row["provider_controls"]["provider_promotion_blocked"] is True
    assert row["promotion_authority"] is False
    assert row["scorecard_authority"] is False
    component_path = output / row["component_report_paths"][0]
    assert canonical_json_loads(component_path.read_bytes()) == _component()


def test_do_runner_missing_cost_ttl_and_teardown_are_explicit_nonclaims_but_scratch_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    _install_runs(
        monkeypatch,
        SimpleNamespace(
            returncode=0,
            stdout=_stdout(metadata=_metadata(), components=(_component(),)),
            stderr="",
        ),
    )

    assert main(_argv(payload, output)) == 0

    row = _manifest(output)["commands"][0]
    assert row["status"] == "passed"
    assert row["scratch_workload_pass_independent_of_provider_promotion"] is True
    assert row["provider_controls"] == {
        "maximum_cost_usd": None,
        "ttl_seconds": None,
        "teardown_authority_ref": None,
        "missing_authorities": [
            "maximum_cost_usd",
            "ttl_seconds",
            "teardown_authority_ref",
        ],
        "provider_promotion_prerequisites_complete": False,
        "provider_promotion_blocked": True,
    }
    assert row["digitalocean_does_not_substitute_for_ibm_or_slurm"] is True


def test_do_runner_metadata_mismatch_fails_before_component_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    _install_runs(
        monkeypatch,
        SimpleNamespace(
            returncode=42,
            stdout="",
            stderr="PHASE5_DO_METADATA_MISMATCH=provider_identity\n",
        ),
    )

    assert main(_argv(payload, output)) == 42

    row = _manifest(output)["commands"][0]
    assert row["status"] == "failed"
    assert row["blocked_reason"] == "provider_metadata_mismatch"
    assert row["observed_provider_metadata"] is None
    assert row["component_report_count"] == 0


def test_do_runner_image_mismatch_fails_before_component_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    _install_runs(
        monkeypatch,
        SimpleNamespace(
            returncode=43,
            stdout="",
            stderr="PHASE5_DO_METADATA_MISMATCH=image_identity\n",
        ),
    )

    assert main(_argv(payload, output)) == 43
    row = _manifest(output)["commands"][0]
    assert row["blocked_reason"] == "image_metadata_mismatch"
    assert row["component_report_count"] == 0


def test_do_runner_cross_node_runtime_input_reuse_rejects_observed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    _install_runs(
        monkeypatch,
        SimpleNamespace(
            returncode=0,
            stdout=_stdout(
                metadata=_metadata(
                    droplet_id="other-node",
                    hostname="other-host",
                ),
                components=(_component(),),
            ),
            stderr="",
        ),
    )

    assert main(_argv(payload, output)) == 1
    row = _manifest(output)["commands"][0]
    assert row["blocked_reason"] == "provider_metadata_mismatch"
    assert row["component_report_count"] == 0


def test_do_runner_missing_or_tampered_secret_scan_blocks_before_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("transfer must not run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    missing = _argv(payload, tmp_path / "missing")
    del missing[2:4]
    with pytest.raises(SystemExit):
        main(missing)

    tampered = _argv(payload, tmp_path / "tampered")
    receipt = Path(tampered[3])
    value = json.loads(receipt.read_bytes())
    value["passed"] = False
    receipt.write_bytes(canonical_json_bytes(value))
    with pytest.raises(SystemExit):
        main(tampered)


def test_do_runner_no_component_report_cannot_false_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    _install_runs(
        monkeypatch,
        SimpleNamespace(returncode=0, stdout=_stdout(metadata=_metadata()), stderr=""),
    )

    assert main(_argv(payload, output)) == 1
    row = _manifest(output)["commands"][0]
    assert row["status"] == "failed"
    assert row["blocked_reason"] == "component_report_missing"
    assert row["component_report_paths"] == []


def test_do_runner_failed_rerun_revokes_stale_passed_row_and_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    responses = [
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(
            returncode=0,
            stdout=_stdout(metadata=_metadata(), components=(_component(),)),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=9, stdout=_stdout(metadata=_metadata()), stderr="runner failed\n"),
    ]

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert main(_argv(payload, output)) == 0
    first = _manifest(output)["commands"][0]
    stale_component = output / first["component_report_paths"][0]
    assert stale_component.exists()

    assert main(_argv(payload, output)) == 9

    manifest = _manifest(output)
    assert len(manifest["commands"]) == 1
    row = manifest["commands"][0]
    assert row["command_id"] == "f6-scratch"
    assert row["status"] == "failed"
    assert row["component_report_paths"] == []
    assert not stale_component.exists()


def test_do_runner_timeout_is_recorded_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")
    output = tmp_path / "output"
    calls = 0

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output="partial\n", stderr="hung\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(_argv(payload, output, "--run-timeout-seconds", "2")) == 124
    row = _manifest(output)["commands"][0]
    assert row["status"] == "failed"
    assert row["timed_out"] is True
    assert row["blocked_reason"] == "target_timeout"
    assert "partial" in (output / row["raw_log_path"]).read_text()


def test_do_runner_rejects_ssh_and_command_injection_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(tmp_path / "payload.zip")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    malicious_alias = _argv(payload, tmp_path / "alias")
    alias_runtime = Path(malicious_alias[1])
    alias_value = json.loads(alias_runtime.read_bytes())
    alias_value["ssh_alias"] = "host;touch /tmp/pwned"
    alias_runtime.write_bytes(canonical_json_bytes(alias_value))
    with pytest.raises(SystemExit):
        main(malicious_alias)

    malicious_command = _argv(payload, tmp_path / "command")
    command_runtime = Path(malicious_command[1])
    command_value = json.loads(command_runtime.read_bytes())
    command_value["command_id"] = "f6; rm -rf /"
    command_runtime.write_bytes(canonical_json_bytes(command_value))
    with pytest.raises(SystemExit):
        main(malicious_command)


def test_remote_command_quotes_every_dynamic_value_and_cleans_unique_scratch() -> None:
    command = _build_remote_command(
        target_run_id="20260713T120000Z-do-scratch-1",
        command_id="f6_scratch",
        remote_zip="/tmp/payload with spaces.zip",
        expected_droplet_id="99112233",
        expected_region="nyc3",
        expected_hostname="bb-scratch-1",
        expected_image_id="sha256:" + "1" * 64,
        expected_image_reference="example/image:pinned",
        secret_scan_receipt_sha256="sha256:" + "2" * 64,
    )

    assert "'/tmp/payload with spaces.zip'" in command
    assert "rm -rf -- \"$WORK\"" in command
    assert "trap cleanup EXIT" in command
    assert "> .metadata.env; . ./.metadata.env;" in command
    assert "eval " not in command
    assert "./run.sh" in command
    assert "srun" not in command
    assert "docker image inspect" in command
    assert "PHASE5_SECRET_SCAN_RECEIPT_SHA256" in command


def test_metadata_whitelist_rejects_vendor_data_and_public_keys() -> None:
    unsafe = {
        **_metadata(),
        "vendor_data": "#cloud-config\nsecret: do-not-capture",
        "public_keys": ["ssh-ed25519 AAAA..."],
    }

    with pytest.raises(ValueError, match="non-whitelisted"):
        _sanitize_metadata(unsafe)
