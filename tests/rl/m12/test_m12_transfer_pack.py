from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from breadboard.rl.m12 import (
    apply_m12_transfer_overlay,
    build_m12_readiness_summary,
    build_m12_test_commands_script,
    build_m12_transfer_manifest,
    build_m12_transfer_summary,
    validate_m12_readiness_summary,
    validate_m12_test_commands_script,
    validate_m12_transfer_archive_manifest,
    validate_m12_transfer_overlay_report,
    validate_m12_transfer_summary,
    write_m12_transfer_archive,
    write_m12_transfer_pack,
)
from breadboard.rl.m12.transfer import (
    COMMAND_LOG_MANIFEST_TEMPLATE,
    EXPECTED_OUTPUTS,
    LOAD_LADDER_REPORT_TEMPLATE,
    M12_TEST_COMMAND_ROWS,
    M12_TEST_COMMANDS,
    REQUIRED_TRANSFER_ARTIFACTS,
    SOAK_REPORT_TEMPLATE,
    TRANSFER_PREP_FILES,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_ARCHIVE_ROOT = Path("workspace") / REPO_ROOT.name


def _overlay_repo_root(workspace_root: Path) -> Path:
    return workspace_root / REPO_ROOT.name

FINAL_REPORT_TARGET_ARGUMENTS = (
    "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json",
    "--archive-verify-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json",
    "--preflight-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_target_preflight/m12_preflight_report.json",
    "--swe-run-summary ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_swe_probe/run_summary.json",
    "--verl-smoke-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_verl_probe/smoke_consumer_report.json",
    "--ray-probe-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/ray_probe_report.json",
    "--warm-vs-cold-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_ray_probe/warm_vs_cold_report.json",
    "--load-ladder-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json",
    "--soak-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_soak/soak_report.json",
)
FINAL_REPORT_MANIFEST_ARGUMENTS = (
    *FINAL_REPORT_TARGET_ARGUMENTS,
    "--command-log-manifest ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json",
)
FINAL_REPORT_SCRIPT_ARGUMENTS = (
    *FINAL_REPORT_TARGET_ARGUMENTS,
    '--command-log-manifest "$COMMAND_LOG_MANIFEST"',
)


def _write_archive_sidecar_and_manifest(
    *,
    manifest_path: Path,
    manifest: dict,
    mutated_archive_path: Path,
) -> Path:
    archive_sha = "sha256:" + hashlib.sha256(mutated_archive_path.read_bytes()).hexdigest()
    manifest["archive_name"] = mutated_archive_path.name
    manifest["archive_path"] = mutated_archive_path.name
    manifest["archive_sha256"] = archive_sha
    manifest["archive_size_bytes"] = mutated_archive_path.stat().st_size
    manifest["archive_sha256_file"] = mutated_archive_path.with_suffix(mutated_archive_path.suffix + ".sha256").name
    mutated_archive_path.with_suffix(mutated_archive_path.suffix + ".sha256").write_text(
        f"{archive_sha}  {mutated_archive_path.name}\n",
        encoding="utf-8",
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _write_archive_with_test_command_mutation(tmp_path: Path, mutate) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_archive_path = tmp_path / "m12_transfer_evidence_pack.tar.gz"
    mutated_archive_path = tmp_path / "m12_transfer_evidence_pack_mutated.tar.gz"

    with tarfile.open(original_archive_path, "r:gz") as source_archive:
        members = [member for member in source_archive.getmembers() if member.isfile()]
        payloads = {}
        for member in members:
            extracted = source_archive.extractfile(member)
            assert extracted is not None
            payload = extracted.read()
            if member.name.endswith("/m12_test_commands.sh"):
                payload = mutate(payload)
                for entry in manifest["included_entries"]:
                    if entry["archive_path"] == member.name:
                        entry["size_bytes"] = len(payload)
                        entry["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
                        break
            payloads[member.name] = payload
        with mutated_archive_path.open("wb") as raw_archive:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as gzip_archive:
                with tarfile.open(fileobj=gzip_archive, mode="w", format=tarfile.PAX_FORMAT) as mutated_archive:
                    for member in members:
                        payload = payloads[member.name]
                        info = tarfile.TarInfo(member.name)
                        info.size = len(payload)
                        info.mode = member.mode
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        mutated_archive.addfile(info, io.BytesIO(payload))

    return _write_archive_sidecar_and_manifest(
        manifest_path=manifest_path,
        manifest=manifest,
        mutated_archive_path=mutated_archive_path,
    )


def _write_archive_with_test_command_target_run_removed(tmp_path: Path) -> Path:
    return _write_archive_with_test_command_mutation(
        tmp_path,
        lambda payload: payload.replace(b'--target-run-id "$M12_TARGET_RUN_ID" ', b"", 1),
    )


def _write_archive_with_test_command_closeout_guard_removed(tmp_path: Path) -> Path:
    return _write_archive_with_test_command_mutation(
        tmp_path,
        lambda payload: payload.replace(
            b"Existing M12 close-out artifact would make target evidence ambiguous",
            b"Existing M12 close-out artifact guard removed",
            1,
        ),
    )


def _write_archive_with_json_member_mutation(tmp_path: Path, *, suffix: str, mutate) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_archive_path = tmp_path / "m12_transfer_evidence_pack.tar.gz"
    mutated_archive_path = tmp_path / "m12_transfer_evidence_pack_json_mutated.tar.gz"

    with tarfile.open(original_archive_path, "r:gz") as source_archive:
        members = [member for member in source_archive.getmembers() if member.isfile()]
        payloads = {}
        found = False
        for member in members:
            extracted = source_archive.extractfile(member)
            assert extracted is not None
            payload = extracted.read()
            if member.name.endswith(suffix):
                document = json.loads(payload.decode("utf-8"))
                mutate(document)
                payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
                found = True
                for entry in manifest["included_entries"]:
                    if entry["archive_path"] == member.name:
                        entry["size_bytes"] = len(payload)
                        entry["sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
                        break
            payloads[member.name] = payload
        assert found, suffix
        with mutated_archive_path.open("wb") as raw_archive:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as gzip_archive:
                with tarfile.open(fileobj=gzip_archive, mode="w", format=tarfile.PAX_FORMAT) as mutated_archive:
                    for member in members:
                        payload = payloads[member.name]
                        info = tarfile.TarInfo(member.name)
                        info.size = len(payload)
                        info.mode = member.mode
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        mutated_archive.addfile(info, io.BytesIO(payload))

    return _write_archive_sidecar_and_manifest(
        manifest_path=manifest_path,
        manifest=manifest,
        mutated_archive_path=mutated_archive_path,
    )


def _write_archive_with_nonzero_gzip_mtime(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_archive_path = tmp_path / "m12_transfer_evidence_pack.tar.gz"
    mutated_archive_path = tmp_path / "m12_transfer_evidence_pack_nonzero_mtime.tar.gz"
    payload = bytearray(original_archive_path.read_bytes())
    payload[4:8] = (1).to_bytes(4, "little")
    mutated_archive_path.write_bytes(bytes(payload))
    return _write_archive_sidecar_and_manifest(
        manifest_path=manifest_path,
        manifest=manifest,
        mutated_archive_path=mutated_archive_path,
    )


def _write_archive_with_reversed_member_order(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_archive_path = tmp_path / "m12_transfer_evidence_pack.tar.gz"
    mutated_archive_path = tmp_path / "m12_transfer_evidence_pack_reversed.tar.gz"

    with tarfile.open(original_archive_path, "r:gz") as source_archive:
        members = [member for member in source_archive.getmembers() if member.isfile()]
        payloads = {}
        for member in members:
            extracted = source_archive.extractfile(member)
            assert extracted is not None
            payloads[member.name] = extracted.read()
        with mutated_archive_path.open("wb") as raw_archive:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as gzip_archive:
                with tarfile.open(fileobj=gzip_archive, mode="w", format=tarfile.PAX_FORMAT) as mutated_archive:
                    for member in reversed(members):
                        payload = payloads[member.name]
                        info = tarfile.TarInfo(member.name)
                        info.size = len(payload)
                        info.mode = member.mode
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        mutated_archive.addfile(info, io.BytesIO(payload))

    return _write_archive_sidecar_and_manifest(
        manifest_path=manifest_path,
        manifest=manifest,
        mutated_archive_path=mutated_archive_path,
    )


def _write_archive_with_duplicate_manifest_entry(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["included_entries"].append(dict(manifest["included_entries"][0]))
    manifest["included_entry_count"] = len(manifest["included_entries"])
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _write_archive_with_absolute_source_path(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["included_entries"][0]["source_path"] = str(REPO_ROOT / "leaked_local_path.py")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _write_archive_with_private_source_key(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["included_entries"][0]["_local_source_path"] = str(REPO_ROOT / "leaked_local_path.py")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _write_archive_with_absolute_top_level_archive_path(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_path"] = str((tmp_path / "m12_transfer_evidence_pack.tar.gz").resolve())
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _write_archive_with_duplicate_tar_member(tmp_path: Path) -> Path:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_archive_path = tmp_path / "m12_transfer_evidence_pack.tar.gz"
    mutated_archive_path = tmp_path / "m12_transfer_evidence_pack_duplicate_member.tar.gz"

    with tarfile.open(original_archive_path, "r:gz") as source_archive:
        members = [member for member in source_archive.getmembers() if member.isfile()]
        payloads = {}
        for member in members:
            extracted = source_archive.extractfile(member)
            assert extracted is not None
            payloads[member.name] = extracted.read()
        with mutated_archive_path.open("wb") as raw_archive:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as gzip_archive:
                with tarfile.open(fileobj=gzip_archive, mode="w", format=tarfile.PAX_FORMAT) as mutated_archive:
                    for member in members:
                        payload = payloads[member.name]
                        info = tarfile.TarInfo(member.name)
                        info.size = len(payload)
                        info.mode = member.mode
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        mutated_archive.addfile(info, io.BytesIO(payload))
                    first = members[0]
                    payload = payloads[first.name]
                    duplicate_info = tarfile.TarInfo(first.name)
                    duplicate_info.size = len(payload)
                    duplicate_info.mode = first.mode
                    duplicate_info.mtime = 0
                    duplicate_info.uid = 0
                    duplicate_info.gid = 0
                    duplicate_info.uname = ""
                    duplicate_info.gname = ""
                    mutated_archive.addfile(duplicate_info, io.BytesIO(payload))

    return _write_archive_sidecar_and_manifest(
        manifest_path=manifest_path,
        manifest=manifest,
        mutated_archive_path=mutated_archive_path,
    )


def _run_generated_overlay_script(
    *,
    script_path: Path,
    manifest_path: Path,
    report_path: Path,
    workspace_root: Path,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
        ],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_m12_transfer_manifest_is_complete_and_non_scoring() -> None:
    manifest = build_m12_transfer_manifest(REPO_ROOT)

    assert manifest["manifest_id"] == "bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
    assert manifest["claim_boundary"] == "transfer_preparation_only_not_m12_validation"
    assert manifest["repo"]["root"] == REPO_ROOT.name
    assert manifest["repo"]["root_path_portable"] is True
    assert not Path(manifest["repo"]["root"]).is_absolute()
    assert ".." not in Path(manifest["repo"]["root"]).parts
    assert manifest["all_required_artifacts_present"] is True
    assert manifest["all_transfer_requirements_covered"] is True
    assert [artifact["path"] for artifact in manifest["artifacts"]] == REQUIRED_TRANSFER_ARTIFACTS
    assert manifest["test_commands"] == M12_TEST_COMMANDS
    assert len(manifest["test_commands"]) == len(M12_TEST_COMMAND_ROWS)
    assert validate_m12_test_commands_script(build_m12_test_commands_script(), manifest) == []
    assert "requirements.txt" in REQUIRED_TRANSFER_ARTIFACTS
    assert "breadboard/rl" in REQUIRED_TRANSFER_ARTIFACTS
    assert "scripts/rl_phase1" in REQUIRED_TRANSFER_ARTIFACTS
    assert "tests/rl" in REQUIRED_TRANSFER_ARTIFACTS
    assert "tests/test_rl_phase1_scorecard_schema.py" in REQUIRED_TRANSFER_ARTIFACTS
    assert "tests/test_rl_phase1_claim_ledger.py" in REQUIRED_TRANSFER_ARTIFACTS
    assert "docs/rl_phase1" in REQUIRED_TRANSFER_ARTIFACTS
    assert "examples/rl_env_packages" in REQUIRED_TRANSFER_ARTIFACTS
    assert "../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md" in REQUIRED_TRANSFER_ARTIFACTS
    assert "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/row_evidence" in REQUIRED_TRANSFER_ARTIFACTS
    assert "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/smoke_consumer_report.json" in REQUIRED_TRANSFER_ARTIFACTS
    assert "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m8_ray_warm_pool_probe/warm_vs_cold_report.json" in REQUIRED_TRANSFER_ARTIFACTS
    assert "verify_m12_transfer_archive.py" in manifest["test_commands"][0]
    preflight_command = next(command for command in manifest["test_commands"] if "run_m12_preflight.py" in command)
    assert "--require-pass" in preflight_command
    assert "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.parquet" in REQUIRED_TRANSFER_ARTIFACTS
    assert "../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/projection_manifest.json" in REQUIRED_TRANSFER_ARTIFACTS
    assert "m12_node_verl_probe/projection_manifest.json" in manifest["expected_outputs"]
    assert "m12_archive_verify/m12_archive_verify_report.json" in manifest["expected_outputs"]
    assert "m12_node_load_ladder/load_ladder_report.json" in manifest["expected_outputs"]
    assert "m12_node_soak/soak_report.json" in manifest["expected_outputs"]
    assert "m12_command_logs/command_log_manifest.json" in manifest["expected_outputs"]
    assert "m12_final_report/m12_final_report.json" in manifest["expected_outputs"]
    assert "m12_promotion_audit/m12_promotion_audit.json" in manifest["expected_outputs"]
    assert manifest["expected_outputs"] == EXPECTED_OUTPUTS
    final_command = next(command for command in manifest["test_commands"] if "build_m12_final_report.py" in command)
    promotion_command = next(command for command in manifest["test_commands"] if "audit_m12_score_promotion.py" in command)
    assert "--command-log-manifest" in final_command
    assert all(argument in final_command for argument in FINAL_REPORT_MANIFEST_ARGUMENTS)
    assert "--require-eligible" in final_command
    assert "--command-log-manifest" in promotion_command
    assert "--require-ready" in promotion_command
    assert "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json" in promotion_command
    assert "--final-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json" in promotion_command
    assert "--scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml" in promotion_command
    assert "--claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md" in promotion_command
    assert (
        "--command-log-manifest ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_command_logs/command_log_manifest.json"
        in promotion_command
    )
    covered = {item["requirement"] for item in manifest["transfer_requirement_coverage"]}
    assert {
        "Repo snapshot or commit SHA",
        "Python environment lock",
        "RL Phase 1 source/test/doc overlay",
        "ROCm/PyTorch/VeRL/Ray versions",
        "EnvPackage set",
        "Run manifests",
        "Test command list",
        "Expected outputs",
        "Rollback plan",
        "Final target report contract",
        "Score promotion audit",
        "Load ladder and soak evidence",
        "Raw command log archive",
    }.issubset(covered)
    overlay_coverage = next(item for item in manifest["transfer_requirement_coverage"] if item["requirement"] == "RL Phase 1 source/test/doc overlay")
    assert "breadboard/rl" in overlay_coverage["covered_by"]
    assert "tests/rl" in overlay_coverage["covered_by"]
    final_report_coverage = next(item for item in manifest["transfer_requirement_coverage"] if item["requirement"] == "Final target report contract")
    assert "breadboard/rl/m12/final_report.py" in final_report_coverage["covered_by"]
    assert "scripts/rl_phase1/build_m12_final_report.py" in final_report_coverage["covered_by"]
    assert "scripts/rl_phase1/summarize_m12_final_report_remediations.py" in final_report_coverage["covered_by"]
    promotion_coverage = next(item for item in manifest["transfer_requirement_coverage"] if item["requirement"] == "Score promotion audit")
    assert "breadboard/rl/m12/promotion_audit.py" in promotion_coverage["covered_by"]
    assert "scripts/rl_phase1/audit_m12_score_promotion.py" in promotion_coverage["covered_by"]
    load_soak_coverage = next(item for item in manifest["transfer_requirement_coverage"] if item["requirement"] == "Load ladder and soak evidence")
    assert "breadboard/rl/m12/load_soak.py" in load_soak_coverage["covered_by"]
    assert "scripts/rl_phase1/run_m12_load_ladder.py" in load_soak_coverage["covered_by"]
    assert "scripts/rl_phase1/run_m12_soak.py" in load_soak_coverage["covered_by"]
    assert "Archive preflight, run reports, and raw command logs with sha256 hashes." in manifest["rollback_plan"]
    assert "If load/soak artifacts are missing or non-eligible, preserve the final report as a blocked target outcome." in manifest["rollback_plan"]
    assert "Build and validate m12_final_report.json before any scorecard edit." in manifest["rollback_plan"]
    assert "Do not update scorecard unless M12 target evidence satisfies the gate." in manifest["rollback_plan"]
    assert all(artifact["exists"] for artifact in manifest["artifacts"])
    assert all(artifact.get("sha256", "").startswith("sha256:") for artifact in manifest["artifacts"] if artifact.get("kind") == "file")


def test_m12_transfer_pack_writes_portable_execution_files(tmp_path) -> None:
    manifest = write_m12_transfer_pack(repo_root=REPO_ROOT, output_dir=tmp_path)

    manifest_path = tmp_path / "m12_transfer_manifest.json"
    commands_path = tmp_path / "m12_test_commands.sh"
    rollback_path = tmp_path / "m12_rollback_plan.md"
    readiness_path = tmp_path / "m12_readiness_summary.json"
    load_template_path = tmp_path / "m12_load_ladder_report_template.json"
    soak_template_path = tmp_path / "m12_soak_report_template.json"
    command_log_template_path = tmp_path / "m12_command_log_manifest_template.json"
    overlay_script_path = tmp_path / "m12_apply_overlay.py"
    bootstrap_script_path = tmp_path / "m12_target_bootstrap.sh"

    assert manifest_path.exists()
    assert commands_path.exists()
    assert overlay_script_path.exists()
    assert bootstrap_script_path.exists()
    assert rollback_path.exists()
    assert readiness_path.exists()
    assert load_template_path.exists()
    assert soak_template_path.exists()
    assert command_log_template_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["manifest_id"] == manifest["manifest_id"]

    commands = commands_path.read_text(encoding="utf-8")
    assert 'REPO_ROOT="${REPO_ROOT:-$(pwd)}"' in commands
    assert "Set REPO_ROOT to the BreadBoard repository root" in commands
    assert "run_m12_preflight.py" in commands
    assert "run_m12_logged_command.py" in commands
    assert 'COMMAND_LOG_MANIFEST="$COMMAND_LOG_DIR/command_log_manifest.json"' in commands
    assert 'M12_FINAL_REPORT_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json"' in commands
    assert 'M12_REMEDIATION_SUMMARY_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_remediation_summary.json"' in commands
    assert "m12_on_error()" in commands
    assert "trap m12_on_error ERR" in commands
    assert "summarize_m12_final_report_remediations.py" in commands
    assert 'M12_TARGET_RUN_ID="${M12_TARGET_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"' in commands
    assert 'echo "m12_target_run_id=$M12_TARGET_RUN_ID"' in commands
    assert "Existing M12 command log manifest belongs to different target run id(s)" in commands
    assert '--target-run-id "$M12_TARGET_RUN_ID"' in commands
    assert "--command-id target_transfer_archive_verify" in commands
    assert "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_archive_verify/m12_archive_verify_report.json" in commands
    assert "--command-id phase1_validation_suite" in commands
    assert "--command-id target_preflight" in commands
    assert "--command-id target_swe_probe" in commands
    assert "--command-id target_verl_export" in commands
    assert "--command-id target_ray_warm_pool" in commands
    assert "--distributed" in commands
    assert "--num-workers 20" in commands
    assert "--command-id target_load_ladder" in commands
    assert "--command-id target_soak" in commands
    assert "--command-id final_report" in commands
    assert "--command-id promotion_audit" in commands
    assert "run_m12_load_ladder.py" in commands
    assert "run_m12_soak.py" in commands
    assert "--require-pass" in commands
    assert "build_m12_final_report.py" in commands
    assert "--command-log-manifest" in commands
    assert all(argument in commands for argument in FINAL_REPORT_SCRIPT_ARGUMENTS)
    assert "--require-eligible" in commands
    assert "audit_m12_score_promotion.py" in commands
    assert "--output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json" in commands
    assert "--final-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json" in commands
    assert "--scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml" in commands
    assert "--claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md" in commands
    assert "--require-ready" in commands
    assert validate_m12_test_commands_script(commands, manifest) == []
    assert [
        line.split("--command-id ", 1)[1].split(" ", 1)[0]
        for line in commands.splitlines()
        if "run_m12_logged_command.py" in line
    ] == [command_id for command_id, _ in M12_TEST_COMMAND_ROWS]

    rollback = rollback_path.read_text(encoding="utf-8")
    assert "Archive preflight, run reports, and raw command logs with sha256 hashes." in rollback
    assert "If load/soak artifacts are missing or non-eligible, preserve the final report as a blocked target outcome." in rollback
    assert "Build and validate m12_final_report.json before any scorecard edit." in rollback
    assert "Do not update scorecard unless M12 target evidence satisfies the gate." in rollback

    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert readiness["summary_id"] == "bb_zyphra_rl_phase1_m12_readiness_summary_v1"
    assert readiness["scorecard_update_allowed"] is False
    assert readiness["m12_points_awarded"] is False
    assert readiness["artifact_count"] == len(manifest["artifacts"])
    assert readiness["command_count"] == len(manifest["test_commands"])
    assert readiness["expected_output_count"] == len(manifest["expected_outputs"])
    assert readiness["target_script_fail_closed"]["archive_verifier_runs_first"] is True
    assert readiness["target_script_fail_closed"]["preflight_requires_pass"] is True
    assert readiness["target_script_fail_closed"]["final_report_requires_eligible"] is True
    assert readiness["target_script_fail_closed"]["promotion_audit_requires_ready"] is True
    assert readiness["target_script_fail_closed"]["promotion_audit_uses_explicit_score_inputs"] is True
    assert readiness["target_script_fail_closed"]["promotion_audit_uses_explicit_target_paths"] is True
    assert readiness["target_script_fail_closed"]["generated_script_uses_logged_command_wrapper"] is True
    assert readiness["target_script_fail_closed"]["generated_script_runs_concrete_load_soak"] is True
    assert readiness["target_script_fail_closed"]["bootstrap_rejects_dirty_checkout_by_default"] is True
    assert readiness["target_script_fail_closed"]["bootstrap_runs_overlaid_test_commands"] is True
    assert readiness["target_script_fail_closed"]["bootstrap_cds_to_repo_root_before_handoff"] is True
    assert readiness["target_script_fail_closed"]["generated_script_sets_target_run_id"] is True
    assert readiness["target_script_fail_closed"]["generated_script_rejects_mixed_target_run_logs"] is True
    assert readiness["target_script_fail_closed"]["generated_script_rejects_stale_closeout_artifacts"] is True
    assert readiness["target_script_fail_closed"]["generated_script_summarizes_final_report_remediations_on_error"] is True
    assert readiness["target_script_fail_closed"]["generated_script_manifest_consistent"] is True
    assert readiness["generated_script_validation_errors"] == []
    assert "m12_node_load_ladder/load_ladder_report.json" in readiness["target_only_required_outputs"]
    assert "m12_command_logs/command_log_manifest.json" in readiness["target_only_required_outputs"]
    assert "m12_promotion_audit/m12_promotion_audit.json" in readiness["target_only_required_outputs"]
    assert "m12_final_report.json has m12_score_eligible=true" in readiness["score_promotion_rule"]
    assert "sha256 hashes" in readiness["score_promotion_rule"]
    transfer_summary = json.loads((tmp_path / "m12_transfer_summary.json").read_text(encoding="utf-8"))
    assert transfer_summary["bootstrap_overlaid_test_commands_handoff"] is True
    assert transfer_summary["bootstrap_repo_root_cwd_handoff"] is True
    assert transfer_summary["target_run_log_reuse_guard"] is True
    assert transfer_summary["target_closeout_artifact_reuse_guard"] is True
    assert validate_m12_readiness_summary(readiness, manifest) == []
    assert validate_m12_transfer_summary(transfer_summary, manifest) == []

    load_template = json.loads(load_template_path.read_text(encoding="utf-8"))
    assert load_template == LOAD_LADDER_REPORT_TEMPLATE
    assert [item["target_sessions"] for item in load_template["concurrency_levels"]] == [5, 20, 50, 100]
    assert load_template["policy_version_integrity"] is None
    assert load_template["queue_backpressure_integrity"] is None

    soak_template = json.loads(soak_template_path.read_text(encoding="utf-8"))
    assert soak_template == SOAK_REPORT_TEMPLATE
    assert soak_template["minimum_duration_seconds"] == 7200
    assert soak_template["runtime_failure_count"] is None

    command_log_template = json.loads(command_log_template_path.read_text(encoding="utf-8"))
    assert command_log_template == COMMAND_LOG_MANIFEST_TEMPLATE
    assert command_log_template["manifest_id"] == "bb_zyphra_rl_phase1_m12_command_log_manifest_v1"
    assert command_log_template["all_required_logs_archived"] is False
    assert command_log_template["all_required_commands_passed"] is False
    assert [item["command_id"] for item in command_log_template["commands"]] == [
        "target_transfer_archive_verify",
        "phase1_validation_suite",
        "target_preflight",
        "target_swe_probe",
        "target_verl_export",
        "target_ray_warm_pool",
        "target_load_ladder",
        "target_soak",
        "final_report",
        "promotion_audit",
    ]
    assert command_log_template["required_command_ids"][0] == "target_transfer_archive_verify"
    final_report_entry = next(item for item in command_log_template["commands"] if item["command_id"] == "final_report")
    assert final_report_entry["required"] is False
    promotion_audit_entry = next(item for item in command_log_template["commands"] if item["command_id"] == "promotion_audit")
    assert promotion_audit_entry["required"] is False


def test_m12_readiness_and_transfer_summary_validators_reject_stale_counts() -> None:
    manifest = build_m12_transfer_manifest(REPO_ROOT)
    readiness = build_m12_readiness_summary(manifest)
    readiness["artifact_count"] += 1
    readiness["target_script_fail_closed"]["preflight_requires_pass"] = False

    readiness_errors = validate_m12_readiness_summary(readiness, manifest)

    assert "artifact_count must match transfer manifest" in readiness_errors
    assert "target_script_fail_closed.preflight_requires_pass must match transfer manifest" in readiness_errors

    transfer_summary = build_m12_transfer_summary(manifest)
    transfer_summary["command_count"] -= 1
    transfer_summary["final_command_explicit_target_artifact_args"] = False
    transfer_summary["promotion_audit_explicit_target_paths"] = False
    transfer_summary["target_run_log_reuse_guard"] = False
    transfer_summary["target_closeout_artifact_reuse_guard"] = False
    transfer_summary["generated_script_manifest_consistent"] = False

    transfer_errors = validate_m12_transfer_summary(transfer_summary, manifest)

    assert "command_count must match transfer manifest" in transfer_errors
    assert "final_command_explicit_target_artifact_args must match transfer manifest" in transfer_errors
    assert "promotion_audit_explicit_target_paths must match transfer manifest" in transfer_errors
    assert "target_run_log_reuse_guard must match transfer manifest" in transfer_errors
    assert "target_closeout_artifact_reuse_guard must match transfer manifest" in transfer_errors
    assert "generated_script_manifest_consistent must match transfer manifest" in transfer_errors


def test_m12_test_command_validator_rejects_script_manifest_drift() -> None:
    manifest = build_m12_transfer_manifest(REPO_ROOT)
    script = build_m12_test_commands_script()

    missing_target_run = script.replace('--target-run-id "$M12_TARGET_RUN_ID" ', "", 1)
    errors = validate_m12_test_commands_script(missing_target_run, manifest)
    assert "logged command line mismatch at position 1: target_transfer_archive_verify" in errors
    assert "logged command line missing target run binding: target_transfer_archive_verify" in errors

    missing_log_reuse_guard = script.replace(
        "Existing M12 command log manifest belongs to different target run id(s)",
        "Existing M12 command log manifest guard removed",
    )
    errors = validate_m12_test_commands_script(missing_log_reuse_guard, manifest)
    assert "m12_test_commands.sh must reject mixed target-run command logs" in errors

    assert "M12_PROMOTION_AUDIT_PATH" in script
    assert "Existing M12 close-out artifact would make target evidence ambiguous" in script
    missing_closeout_guard = script.replace(
        "Existing M12 close-out artifact would make target evidence ambiguous",
        "Existing M12 close-out artifact guard removed",
    )
    errors = validate_m12_test_commands_script(missing_closeout_guard, manifest)
    assert "m12_test_commands.sh must reject stale close-out artifacts" in errors

    missing_scorecard = script.replace(
        " --scorecard ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml",
        "",
    )
    errors = validate_m12_test_commands_script(missing_scorecard, manifest)
    assert "promotion_audit command must pass explicit --scorecard path" in errors

    missing_claim_ledger = script.replace(
        " --claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md",
        "",
    )
    errors = validate_m12_test_commands_script(missing_claim_ledger, manifest)
    assert "promotion_audit command must pass explicit --claim-ledger path" in errors

    missing_promotion_output = script.replace(
        " --output ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json",
        "",
        1,
    )
    errors = validate_m12_test_commands_script(missing_promotion_output, manifest)
    assert "promotion_audit command must pass explicit --output path" in errors

    missing_promotion_final_report = script.replace(
        " --final-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_final_report.json",
        "",
    )
    errors = validate_m12_test_commands_script(missing_promotion_final_report, manifest)
    assert "promotion_audit command must pass explicit --final-report path" in errors

    missing_promotion_manifest = script.replace(
        ' --claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md --command-log-manifest "$COMMAND_LOG_MANIFEST"',
        " --claim-ledger ../docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_CLAIM_LEDGER.md",
        1,
    )
    errors = validate_m12_test_commands_script(missing_promotion_manifest, manifest)
    assert "promotion_audit command must pass explicit --command-log-manifest path" in errors

    missing_promotion_require_ready = script.replace(" --require-ready", "", 1)
    errors = validate_m12_test_commands_script(missing_promotion_require_ready, manifest)
    assert "promotion_audit command must require ready evidence" in errors

    missing_final_report_input = script.replace(
        " --load-ladder-report ../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_node_load_ladder/load_ladder_report.json",
        "",
    )
    errors = validate_m12_test_commands_script(missing_final_report_input, manifest)
    assert "final_report command must pass explicit --load-ladder-report path" in errors

    wrong_manifest = dict(manifest)
    wrong_manifest["test_commands"] = list(manifest["test_commands"][:-1])
    errors = validate_m12_test_commands_script(script, wrong_manifest)
    assert "transfer manifest test_commands must match M12_TEST_COMMANDS" in errors
    assert "transfer manifest test_commands count must match M12_TEST_COMMAND_ROWS" in errors


def test_m12_test_command_validator_requires_failure_remediation_trap() -> None:
    manifest = build_m12_transfer_manifest(REPO_ROOT)
    script = build_m12_test_commands_script()
    assert "trap m12_on_error ERR" in script
    assert "summarize_m12_final_report_remediations.py" in script
    assert "M12_REMEDIATION_SUMMARY_PATH" in script

    missing_trap = script.replace("trap m12_on_error ERR\n\n", "")
    errors = validate_m12_test_commands_script(missing_trap, manifest)
    assert "m12_test_commands.sh must install ERR trap for final-report remediation summary" in errors

    missing_summary = script.replace("scripts/rl_phase1/summarize_m12_final_report_remediations.py", "")
    errors = validate_m12_test_commands_script(missing_summary, manifest)
    assert "m12_test_commands.sh must summarize final-report remediations on failure" in errors

    missing_summary_path = script.replace(
        'M12_REMEDIATION_SUMMARY_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_final_report/m12_remediation_summary.json"\n',
        "",
    )
    errors = validate_m12_test_commands_script(missing_summary_path, manifest)
    assert "m12_test_commands.sh must define M12_REMEDIATION_SUMMARY_PATH" in errors

    missing_promotion_path = script.replace(
        'M12_PROMOTION_AUDIT_PATH="../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_promotion_audit/m12_promotion_audit.json"\n',
        "",
    )
    errors = validate_m12_test_commands_script(missing_promotion_path, manifest)
    assert "m12_test_commands.sh must define M12_PROMOTION_AUDIT_PATH" in errors


def test_m12_transfer_archive_is_portable_non_scoring_evidence_pack(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)

    archive_path = tmp_path / archive_manifest["archive_path"]
    sha_path = tmp_path / archive_manifest["archive_sha256_file"]
    archive_manifest_path = tmp_path / "m12_transfer_archive_manifest.json"

    assert archive_path.exists()
    assert sha_path.exists()
    assert archive_manifest_path.exists()
    assert archive_manifest["archive_path"] == archive_path.name
    assert archive_manifest["archive_sha256_file"] == sha_path.name
    assert not Path(archive_manifest["archive_path"]).is_absolute()
    assert not Path(archive_manifest["archive_sha256_file"]).is_absolute()
    assert archive_manifest["archive_manifest_id"] == "bb_zyphra_rl_phase1_m12_transfer_archive_manifest_v1"
    assert archive_manifest["claim_boundary"] == "transfer_archive_only_not_m12_validation"
    assert archive_manifest["scorecard_update_allowed"] is False
    assert archive_manifest["m12_points_awarded"] is False
    assert archive_manifest["archive_is_repo_replacement"] is False
    assert archive_manifest["archive_contains_source_overlay"] is True
    assert archive_manifest["archive_excludes_pycache"] is True
    assert archive_manifest["archive_deterministic"] is True
    assert archive_manifest["source_paths_portable"] is True
    assert archive_manifest["deterministic_archive_metadata"] == {
        "gzip_mtime": 0,
        "member_gid": 0,
        "member_gname": "",
        "member_mtime": 0,
        "member_order": "sorted_by_archive_path",
        "member_uid": 0,
        "member_uname": "",
    }
    assert "breadboard/rl" in archive_manifest["source_overlay_paths"]
    assert "tests/rl" in archive_manifest["source_overlay_paths"]
    assert "exact repo SHA" in archive_manifest["required_operator_repo_step"]
    assert "overlay the archived RL Phase 1" in archive_manifest["required_operator_repo_step"]
    assert archive_manifest["archive_sha256"].startswith("sha256:")
    assert archive_manifest["all_required_artifacts_present"] is True
    assert archive_manifest["all_transfer_requirements_covered"] is True
    assert archive_manifest["included_entry_count"] == len(archive_manifest["included_entries"])
    assert archive_manifest["included_entry_count"] > len(REQUIRED_TRANSFER_ARTIFACTS)
    assert archive_manifest["generated_transfer_files"] == TRANSFER_PREP_FILES
    assert sha_path.read_text(encoding="utf-8").startswith(archive_manifest["archive_sha256"])
    assert all(entry["mode"] in {0o644, 0o755} for entry in archive_manifest["included_entries"])
    assert all(entry["source_path"] == entry["archive_path"] for entry in archive_manifest["included_entries"])
    assert all(not Path(entry["source_path"]).is_absolute() for entry in archive_manifest["included_entries"])
    assert all(".." not in Path(entry["source_path"]).parts for entry in archive_manifest["included_entries"])
    assert all(not any(str(key).startswith("_") for key in entry) for entry in archive_manifest["included_entries"])
    assert int.from_bytes(archive_path.read_bytes()[4:8], "little") == 0

    archived_paths = {entry["archive_path"] for entry in archive_manifest["included_entries"]}
    assert (REPO_ARCHIVE_ROOT / "scripts/rl_phase1/build_m12_transfer_archive.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "scripts/rl_phase1/audit_m12_score_promotion.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "scripts/rl_phase1/check_m12_evidence_consistency.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "breadboard/rl/m12/promotion_audit.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "breadboard/rl/m12/evidence_consistency.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "tests/rl/m12/test_m12_transfer_pack.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "tests/test_rl_phase1_scorecard_schema.py").as_posix() in archived_paths
    assert (REPO_ARCHIVE_ROOT / "docs/rl_phase1/m12_transfer_pack.md").as_posix() in archived_paths
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in archived_paths)
    assert "workspace/docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_M12_VALIDATION_REPORT.md" in archived_paths
    assert "workspace/docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_summary.json" in archived_paths
    assert any(path.endswith("/m12_test_commands.sh") for path in archived_paths)
    assert any(path.endswith("/m12_apply_overlay.py") for path in archived_paths)
    assert any(path.endswith("/m12_target_bootstrap.sh") for path in archived_paths)
    assert any(path.endswith("/m12_transfer_summary.json") for path in archived_paths)

    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        assert [member.name for member in members] == sorted(entry["archive_path"] for entry in archive_manifest["included_entries"])
        archive_names = {member.name for member in members}
        by_name = {member.name: member for member in members}
    assert archived_paths.issubset(archive_names)
    for entry in archive_manifest["included_entries"]:
        member = by_name[entry["archive_path"]]
        assert member.mtime == 0
        assert member.uid == 0
        assert member.gid == 0
        assert member.uname == ""
        assert member.gname == ""
        assert member.mode == entry["mode"]
    assert validate_m12_transfer_archive_manifest(archive_manifest_path) == []

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/verify_m12_transfer_archive.py",
            "--manifest",
            str(archive_manifest_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
    assert "status=passed" in result.stdout


def test_m12_transfer_archive_is_deterministic_for_same_inputs(tmp_path) -> None:
    first = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    second = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)

    assert second["archive_sha256"] == first["archive_sha256"]
    assert second["archive_size_bytes"] == first["archive_size_bytes"]
    assert second["included_entries"] == first["included_entries"]
    assert validate_m12_transfer_archive_manifest(tmp_path / "m12_transfer_archive_manifest.json") == []


def test_m12_transfer_overlay_dry_run_is_non_scoring_and_safe(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    workspace_root = tmp_path / "target_workspace"

    report = apply_m12_transfer_overlay(
        manifest_path=manifest_path,
        workspace_root=workspace_root,
        dry_run=True,
        allow_overwrite=False,
    )

    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_overlay_apply_report_v1"
    assert report["claim_boundary"] == "transfer_overlay_application_not_m12_validation"
    assert report["status"] == "passed"
    assert report["dry_run"] is True
    assert report["allow_overwrite"] is False
    assert report["scorecard_update_allowed"] is False
    assert report["m12_points_awarded"] is False
    assert report["would_write_count"] == archive_manifest["included_entry_count"]
    assert report["written_count"] == 0
    assert report["existing_destination_count"] == 0
    assert report["errors"] == []
    assert validate_m12_transfer_overlay_report(report) == []
    assert not (_overlay_repo_root(workspace_root) / "breadboard" / "rl" / "m12" / "transfer.py").exists()


def test_m12_transfer_overlay_report_validator_rejects_stale_or_ambiguous_summaries(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    workspace_root = tmp_path / "target_workspace"
    report = apply_m12_transfer_overlay(
        manifest_path=manifest_path,
        workspace_root=workspace_root,
        dry_run=True,
        allow_overwrite=False,
    )

    failed_without_errors = dict(report)
    failed_without_errors["status"] = "failed"
    assert "failed report must include at least one error" in validate_m12_transfer_overlay_report(failed_without_errors)

    stale_existing_count = dict(report)
    stale_existing_count["existing_destination_count"] = 1
    assert "existing_destination_count must equal entries with exists=true" in validate_m12_transfer_overlay_report(
        stale_existing_count
    )

    stale_write_count = dict(report)
    stale_write_count["written_count"] = len(report["entries"]) + 1
    assert "written_count must be between 0 and would_write_count" in validate_m12_transfer_overlay_report(
        stale_write_count
    )

    malformed_entry = dict(report)
    malformed_entry["entries"] = [dict(report["entries"][0], exists="yes")]
    malformed_entry["would_write_count"] = 1
    assert any(
        error.startswith("entry exists must be boolean:")
        for error in validate_m12_transfer_overlay_report(malformed_entry)
    )


def test_m12_transfer_overlay_apply_writes_verified_workspace_members(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    workspace_root = tmp_path / "target_workspace"

    report = apply_m12_transfer_overlay(
        manifest_path=manifest_path,
        workspace_root=workspace_root,
        dry_run=False,
        allow_overwrite=False,
    )

    assert report["status"] == "passed"
    assert report["dry_run"] is False
    assert report["scorecard_update_allowed"] is False
    assert report["m12_points_awarded"] is False
    assert report["would_write_count"] == archive_manifest["included_entry_count"]
    assert report["written_count"] == archive_manifest["included_entry_count"]
    assert report["errors"] == []
    assert validate_m12_transfer_overlay_report(report) == []
    assert (_overlay_repo_root(workspace_root) / "scripts" / "rl_phase1" / "apply_m12_transfer_overlay.py").exists()
    assert (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml").exists()


def test_m12_transfer_overlay_rejects_directory_destination_before_writes(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    workspace_root = tmp_path / "target_workspace"
    conflicting_destination = (
        workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh"
    )
    conflicting_destination.mkdir(parents=True)

    report = apply_m12_transfer_overlay(
        manifest_path=manifest_path,
        workspace_root=workspace_root,
        dry_run=False,
        allow_overwrite=True,
    )

    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert any("destination exists and is directory:" in error for error in report["errors"])
    assert validate_m12_transfer_overlay_report(report) == []
    assert conflicting_destination.is_dir()


def test_m12_transfer_overlay_reports_apply_time_write_failure(monkeypatch, tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    workspace_root = tmp_path / "target_workspace"

    def fail_write_bytes(self: Path, data: bytes) -> int:
        raise OSError("simulated write race")

    monkeypatch.setattr(Path, "write_bytes", fail_write_bytes)

    report = apply_m12_transfer_overlay(
        manifest_path=manifest_path,
        workspace_root=workspace_root,
        dry_run=False,
        allow_overwrite=True,
    )

    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert any("overlay write failed:" in error and "simulated write race" in error for error in report["errors"])
    assert validate_m12_transfer_overlay_report(report) == []


def test_generated_m12_overlay_script_runs_without_repo_imports(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    dry_run = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert dry_run.returncode == 0
    assert "status=passed" in dry_run.stdout
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["dry_run"] is True
    assert written["written_count"] == 0
    assert written["would_write_count"] == archive_manifest["included_entry_count"]

    apply_run = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
            "--apply",
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert apply_run.returncode == 0
    assert "status=passed" in apply_run.stdout
    applied = json.loads(report_path.read_text(encoding="utf-8"))
    assert applied["dry_run"] is False
    assert applied["written_count"] == archive_manifest["included_entry_count"]
    assert (_overlay_repo_root(workspace_root) / "scripts" / "rl_phase1" / "apply_m12_transfer_overlay.py").exists()
    assert (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_target_bootstrap_dry_run_checks_sha_and_overlay(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    bootstrap_path = tmp_path / "prep" / "m12_target_bootstrap.sh"
    bootstrap_text = bootstrap_path.read_text(encoding="utf-8")
    assert "Repo checkout is dirty before M12 overlay" in bootstrap_text
    assert "ALLOW_M12_DIRTY_CHECKOUT=1" in bootstrap_text
    assert "repo_dirty_check=clean" in bootstrap_text
    assert "repo_dirty_check=override" in bootstrap_text
    assert 'TARGET_TEST_COMMANDS="$TARGET_PREP_DIR/m12_test_commands.sh"' in bootstrap_text
    assert "Missing overlaid M12 test command script after overlay apply" in bootstrap_text
    assert 'cd "$REPO_ROOT"' in bootstrap_text
    assert 'bash "$TARGET_TEST_COMMANDS"' in bootstrap_text

    result = subprocess.run(
        ["bash", str(bootstrap_path)],
        cwd=REPO_ROOT,
        env={
            "PATH": str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", ""),
            "REPO_ROOT": str(REPO_ROOT),
            "WORKSPACE_ROOT": str(REPO_ROOT.parent),
            "BOOTSTRAP_DRY_RUN_ONLY": "1",
            "ALLOW_M12_DIRTY_CHECKOUT": "1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "bootstrap_dry_run_only=true" in result.stdout
    assert "repo_dirty_check=" in result.stdout
    report_path = tmp_path / "prep" / "m12_overlay_apply_dry_run_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["dry_run"] is True
    assert report["would_write_count"] == archive_manifest["included_entry_count"]
    assert report["written_count"] == 0
    assert report["existing_destination_count"] > 0


def test_generated_m12_target_bootstrap_hands_off_to_overlay_from_repo_root(tmp_path) -> None:
    repo_root = tmp_path / "breadboard_repo_integration_main_20260326"
    repo_root.mkdir()
    (repo_root / "README.md").write_text("target repo\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=m12-bootstrap-test@example.invalid",
            "-c",
            "user.name=M12 Bootstrap Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()

    prep_dir = tmp_path / "prep"
    write_m12_transfer_pack(repo_root=REPO_ROOT, output_dir=prep_dir)
    transfer_manifest_path = prep_dir / "m12_transfer_manifest.json"
    transfer_manifest = json.loads(transfer_manifest_path.read_text(encoding="utf-8"))
    transfer_manifest["repo"]["head"] = head
    transfer_manifest_path.write_text(json.dumps(transfer_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (prep_dir / "m12_transfer_archive_manifest.json").write_text("{}\n", encoding="utf-8")
    (prep_dir / "m12_transfer_evidence_pack.tar.gz").write_bytes(b"fake archive for bootstrap handoff test\n")
    (prep_dir / "m12_transfer_evidence_pack.tar.gz.sha256").write_text(
        "sha256:" + ("0" * 64) + "  m12_transfer_evidence_pack.tar.gz\n",
        encoding="utf-8",
    )

    (prep_dir / "m12_test_commands.sh").write_text("exit 42\n", encoding="utf-8")
    (prep_dir / "m12_apply_overlay.py").write_text(
        """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--manifest")
parser.add_argument("--workspace-root", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--apply", action="store_true")
parser.add_argument("--allow-overwrite", action="store_true")
args = parser.parse_args()
if args.apply:
    target = Path(args.workspace_root) / "docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep"
    target.mkdir(parents=True, exist_ok=True)
    (target / "m12_test_commands.sh").write_text(
        'printf "overlaid\\\\n" > "$M12_HANDOFF_MARKER"\\n'
        'pwd > "$M12_HANDOFF_CWD"\\n',
        encoding="utf-8",
    )
report = {
    "status": "passed",
    "dry_run": not args.apply,
    "written_count": 1 if args.apply else 0,
    "would_write_count": 1,
    "errors": [],
}
Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print("status=passed")
""",
        encoding="utf-8",
    )

    handoff_marker = tmp_path / "handoff_marker.txt"
    handoff_cwd = tmp_path / "handoff_cwd.txt"
    result = subprocess.run(
        ["bash", str(prep_dir / "m12_target_bootstrap.sh")],
        cwd=tmp_path,
        env={
            "PATH": str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", ""),
            "REPO_ROOT": str(repo_root),
            "WORKSPACE_ROOT": str(tmp_path),
            "M12_HANDOFF_MARKER": str(handoff_marker),
            "M12_HANDOFF_CWD": str(handoff_cwd),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert handoff_marker.read_text(encoding="utf-8") == "overlaid\n"
    assert handoff_cwd.read_text(encoding="utf-8").strip() == str(repo_root)


def test_m12_transfer_archive_validator_detects_sidecar_mismatch(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    sha_path = tmp_path / archive_manifest["archive_sha256_file"]
    sha_path.write_text("sha256:" + ("0" * 64) + "  m12_transfer_evidence_pack.tar.gz\n", encoding="utf-8")

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archive sha256 sidecar does not match archive manifest" in errors


def test_m12_transfer_archive_verifier_cli_writes_non_scoring_report(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    report_path = tmp_path / "m12_archive_verify_report.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/verify_m12_transfer_archive.py",
            "--manifest",
            str(manifest_path),
            "--output",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "status=passed archive_manifest_verified=true" in result.stdout
    assert str(report_path) in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_archive_verify_report_v1"
    assert report["claim_boundary"] == "transfer_archive_verification_not_m12_validation"
    assert report["scorecard_update_allowed"] is False
    assert report["m12_points_awarded"] is False
    assert report["status"] == "passed"
    assert report["archive_sha256"] == archive_manifest["archive_sha256"]
    assert report["included_entry_count"] == archive_manifest["included_entry_count"]
    assert report["errors"] == []


def test_m12_transfer_archive_verifier_cli_writes_failed_report(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    report_path = tmp_path / "m12_archive_verify_report.json"
    sha_path = tmp_path / archive_manifest["archive_sha256_file"]
    sha_path.write_text("sha256:" + ("0" * 64) + "  m12_transfer_evidence_pack.tar.gz\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/verify_m12_transfer_archive.py",
            "--manifest",
            str(manifest_path),
            "--output",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 5
    assert "status=failed archive_manifest_verified=false" in result.stdout
    assert "archive sha256 sidecar does not match archive manifest" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["scorecard_update_allowed"] is False
    assert report["m12_points_awarded"] is False
    assert "archive sha256 sidecar does not match archive manifest" in report["errors"]


def test_m12_transfer_archive_validator_detects_member_hash_mismatch(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path)
    manifest_path = tmp_path / "m12_transfer_archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target_entry = manifest["included_entries"][0]
    target_entry["sha256"] = "sha256:" + ("0" * 64)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert f"archive member sha256 mismatch: {target_entry['archive_path']}" in errors


def test_m12_transfer_archive_validator_detects_nonzero_gzip_mtime(tmp_path) -> None:
    manifest_path = _write_archive_with_nonzero_gzip_mtime(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archive gzip mtime must be zero" in errors


def test_m12_transfer_archive_validator_detects_unsorted_members(tmp_path) -> None:
    manifest_path = _write_archive_with_reversed_member_order(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archive member order must match sorted included_entries" in errors


def test_m12_transfer_archive_validator_rejects_semantically_stale_readiness_summary(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path,
        suffix="/m12_readiness_summary.json",
        mutate=lambda document: (
            document.__setitem__("artifact_count", int(document["artifact_count"]) + 1),
            document["target_script_fail_closed"].__setitem__("preflight_requires_pass", False),
        ),
    )

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archived m12_readiness_summary.json invalid: artifact_count must match transfer manifest" in errors
    assert (
        "archived m12_readiness_summary.json invalid: target_script_fail_closed.preflight_requires_pass must match transfer manifest"
        in errors
    )


def test_m12_transfer_archive_validator_rejects_semantically_stale_transfer_summary(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path,
        suffix="/m12_transfer_summary.json",
        mutate=lambda document: (
            document.__setitem__("command_count", int(document["command_count"]) - 1),
            document.__setitem__("generated_script_manifest_consistent", False),
        ),
    )

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archived m12_transfer_summary.json invalid: command_count must match transfer manifest" in errors
    assert (
        "archived m12_transfer_summary.json invalid: generated_script_manifest_consistent must match transfer manifest"
        in errors
    )


def test_m12_transfer_archive_validator_rejects_inner_transfer_manifest_boundary_drift(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path,
        suffix="/m12_transfer_manifest.json",
        mutate=lambda document: (
            document.__setitem__("manifest_id", "stale_transfer_manifest"),
            document.__setitem__("claim_boundary", "scorecard_update_allowed"),
            document["repo"].__setitem__("root_path_portable", False),
        ),
    )

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert (
        "archived m12_transfer_manifest.json invalid: "
        "manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
        in errors
    )
    assert (
        "archived m12_transfer_manifest.json invalid: "
        "claim_boundary must remain transfer_preparation_only_not_m12_validation"
        in errors
    )
    assert "archived m12_transfer_manifest.json invalid: repo.root_path_portable must be true" in errors


def test_m12_transfer_archive_validator_detects_duplicate_manifest_entries(tmp_path) -> None:
    manifest_path = _write_archive_with_duplicate_manifest_entry(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "included_entries archive_path values must be unique" in errors


def test_m12_transfer_archive_validator_rejects_absolute_source_path(tmp_path) -> None:
    manifest_path = _write_archive_with_absolute_source_path(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert any(error.startswith("unsafe included source path: ") for error in errors)


def test_m12_transfer_archive_validator_rejects_private_source_key(tmp_path) -> None:
    manifest_path = _write_archive_with_private_source_key(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "included_entry private keys are not allowed: _local_source_path" in errors


def test_m12_transfer_archive_validator_rejects_absolute_top_level_archive_path(tmp_path) -> None:
    manifest_path = _write_archive_with_absolute_top_level_archive_path(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archive_path must be portable colocated file name: m12_transfer_evidence_pack.tar.gz" in errors


def test_m12_transfer_archive_validator_detects_duplicate_tar_members(tmp_path) -> None:
    manifest_path = _write_archive_with_duplicate_tar_member(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archive file member paths must be unique" in errors


def test_m12_transfer_archive_validator_detects_archived_script_manifest_drift(tmp_path) -> None:
    manifest_path = _write_archive_with_test_command_target_run_removed(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert (
        "archived m12_test_commands.sh invalid: logged command line mismatch at position 1: target_transfer_archive_verify"
        in errors
    )
    assert (
        "archived m12_test_commands.sh invalid: logged command line missing target run binding: target_transfer_archive_verify"
        in errors
    )


def test_m12_transfer_archive_validator_rejects_archived_script_without_closeout_guard(tmp_path) -> None:
    manifest_path = _write_archive_with_test_command_closeout_guard_removed(tmp_path)

    errors = validate_m12_transfer_archive_manifest(manifest_path)

    assert "archived m12_test_commands.sh invalid: m12_test_commands.sh must reject stale close-out artifacts" in errors


def test_generated_m12_overlay_script_rejects_nonzero_gzip_mtime_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_nonzero_gzip_mtime(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "archive gzip mtime must be zero" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_unsorted_members_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_reversed_member_order(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "archive member order must match sorted included_entries" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_duplicate_manifest_entries_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_duplicate_manifest_entry(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "included_entries archive_path values must be unique" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_absolute_source_path_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_absolute_source_path(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "unsafe included source path: " in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_absolute_top_level_archive_path_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_absolute_top_level_archive_path(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "archive_path must be portable colocated file name: m12_transfer_evidence_pack.tar.gz" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_duplicate_tar_members_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_duplicate_tar_member(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "archive file member paths must be unique" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_archived_script_manifest_drift_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_test_command_target_run_removed(tmp_path / "prep")
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "archived logged command line mismatch at position 1: target_transfer_archive_verify" in result.stdout
    assert "archived logged command line missing target run binding: target_transfer_archive_verify" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_inner_transfer_manifest_boundary_drift_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path / "prep",
        suffix="/m12_transfer_manifest.json",
        mutate=lambda document: (
            document.__setitem__("manifest_id", "stale_transfer_manifest"),
            document.__setitem__("claim_boundary", "scorecard_update_allowed"),
            document["repo"].__setitem__("root_path_portable", False),
        ),
    )
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert (
        "archived m12_transfer_manifest.json invalid: "
        "manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
        in result.stdout
    )
    assert (
        "archived m12_transfer_manifest.json invalid: "
        "claim_boundary must remain transfer_preparation_only_not_m12_validation"
        in result.stdout
    )
    assert "archived m12_transfer_manifest.json invalid: repo.root_path_portable must be true" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_stale_readiness_summary_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path / "prep",
        suffix="/m12_readiness_summary.json",
        mutate=lambda document: (
            document.__setitem__("artifact_count", int(document["artifact_count"]) + 1),
            document["target_script_fail_closed"].__setitem__("preflight_requires_pass", False),
        ),
    )
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "archived m12_readiness_summary.json invalid: artifact_count must match transfer manifest" in result.stdout
    assert (
        "archived m12_readiness_summary.json invalid: target_script_fail_closed.preflight_requires_pass must match transfer manifest"
        in result.stdout
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_readiness_boundary_drift_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path / "prep",
        suffix="/m12_readiness_summary.json",
        mutate=lambda document: (
            document.__setitem__("summary_id", "stale_readiness_summary"),
            document.__setitem__("scorecard_update_allowed", True),
            document.__setitem__("target_only_required_outputs", []),
        ),
    )
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert (
        "archived m12_readiness_summary.json invalid: "
        "summary_id must be bb_zyphra_rl_phase1_m12_readiness_summary_v1"
        in result.stdout
    )
    assert "archived m12_readiness_summary.json invalid: scorecard_update_allowed must be false" in result.stdout
    assert (
        "archived m12_readiness_summary.json invalid: target_only_required_outputs must match expected target outputs"
        in result.stdout
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_readiness_fail_closed_key_drift_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path / "prep",
        suffix="/m12_readiness_summary.json",
        mutate=lambda document: document["target_script_fail_closed"].__setitem__("extra_fail_open_gate", True),
    )
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert (
        "archived m12_readiness_summary.json invalid: "
        "target_script_fail_closed keys must match expected fail-closed checks"
        in result.stdout
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_stale_transfer_summary_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path / "prep",
        suffix="/m12_transfer_summary.json",
        mutate=lambda document: (
            document.__setitem__("command_count", int(document["command_count"]) - 1),
            document.__setitem__("generated_script_manifest_consistent", False),
        ),
    )
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert "archived m12_transfer_summary.json invalid: command_count must match transfer manifest" in result.stdout
    assert (
        "archived m12_transfer_summary.json invalid: generated_script_manifest_consistent must match transfer manifest"
        in result.stdout
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_transfer_summary_boundary_drift_before_writes(tmp_path) -> None:
    manifest_path = _write_archive_with_json_member_mutation(
        tmp_path / "prep",
        suffix="/m12_transfer_summary.json",
        mutate=lambda document: (
            document.__setitem__("manifest_id", "stale_transfer_manifest"),
            document.__setitem__("claim_boundary", "scorecard_update_allowed"),
            document.__setitem__("concrete_load_soak_scripts", False),
        ),
    )
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"

    result = _run_generated_overlay_script(
        script_path=script_path,
        manifest_path=manifest_path,
        report_path=report_path,
        workspace_root=workspace_root,
        cwd=tmp_path,
    )

    assert result.returncode == 6
    assert (
        "archived m12_transfer_summary.json invalid: "
        "manifest_id must be bb_zyphra_rl_phase1_m12_transfer_manifest_v1"
        in result.stdout
    )
    assert (
        "archived m12_transfer_summary.json invalid: "
        "claim_boundary must remain transfer_preparation_only_not_m12_validation"
        in result.stdout
    )
    assert "archived m12_transfer_summary.json invalid: concrete_load_soak_scripts must match transfer manifest" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_sidecar_mismatch_before_writes(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"
    (tmp_path / "prep" / archive_manifest["archive_sha256_file"]).write_text(
        "sha256:" + ("0" * 64) + "  m12_transfer_evidence_pack.tar.gz\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "archive sha256 sidecar does not match archive manifest" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_generated_file_coverage_drift_before_writes(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_transfer_files"] = manifest["generated_transfer_files"][:-1]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "generated_transfer_files must match GENERATED_TRANSFER_FILES" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_reports_corrupt_archive_before_writes(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"
    archive_path = tmp_path / "prep" / archive_manifest["archive_path"]
    archive_path.write_bytes(b"not a tar.gz archive")
    corrupt_sha = "sha256:" + hashlib.sha256(archive_path.read_bytes()).hexdigest()
    sha_path = tmp_path / "prep" / archive_manifest["archive_sha256_file"]
    sha_path.write_text(f"{corrupt_sha}  {archive_path.name}\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = corrupt_sha
    manifest["archive_size_bytes"] = archive_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "archive file is not readable tar.gz:" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert not (workspace_root / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m12_transfer_prep" / "m12_test_commands.sh").exists()


def test_generated_m12_overlay_script_rejects_parent_file_collision_before_writes(tmp_path) -> None:
    write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"
    workspace_root.mkdir()
    blocking_parent = workspace_root / "docs_tmp"
    blocking_parent.write_text("not a directory\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest_path),
            "--workspace-root",
            str(workspace_root),
            "--output",
            str(report_path),
            "--apply",
            "--allow-overwrite",
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 6
    assert "destination parent exists and is not directory:" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["written_count"] == 0
    assert blocking_parent.is_file()


def test_generated_m12_overlay_script_reports_apply_time_write_failure(tmp_path) -> None:
    archive_manifest = write_m12_transfer_archive(repo_root=REPO_ROOT, output_dir=tmp_path / "prep")
    manifest_path = tmp_path / "prep" / "m12_transfer_archive_manifest.json"
    script_path = tmp_path / "prep" / "m12_apply_overlay.py"
    report_path = tmp_path / "overlay_report.json"
    workspace_root = tmp_path / "target_workspace"
    unwritable_parent = _overlay_repo_root(workspace_root) / "breadboard" / "rl"
    unwritable_parent.mkdir(parents=True)
    unwritable_parent.chmod(0o500)
    try:
        if os.access(unwritable_parent, os.W_OK):
            pytest.skip("filesystem permissions allow writes despite chmod; cannot force write failure safely")
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--manifest",
                str(manifest_path),
                "--workspace-root",
                str(workspace_root),
                "--output",
                str(report_path),
                "--apply",
                "--allow-overwrite",
            ],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        unwritable_parent.chmod(0o700)

    assert result.returncode == 6
    assert "overlay write failed:" in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert 0 < report["written_count"] < archive_manifest["included_entry_count"]
