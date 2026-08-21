from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from scripts.e4_parity.immutable_inputs import (
    ImmutableInputError,
    provision_immutable_inputs,
    verify_immutable_inputs,
)
from scripts.e4_parity.provision_immutable_inputs import main as provision_main

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
REGULAR_MODE = stat.S_IFREG | 0o644
REAL_BUNDLE = Path("config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.zip")
REAL_MANIFEST = Path("config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.manifest.json")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_bundle(
    directory: Path,
    archive_entries: dict[str, bytes],
    *,
    manifest_entries: dict[str, bytes] | None = None,
    modes: dict[str, int] | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    archive_path = directory / "inputs.zip"
    manifest_path = directory / "inputs.manifest.json"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(archive_entries.items()):
            info = zipfile.ZipInfo(path, date_time=FIXED_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (modes or {}).get(path, REGULAR_MODE) << 16
            archive.writestr(info, data)
    rows = []
    for path, data in sorted((manifest_entries or archive_entries).items()):
        namespace, destination = path.split("/", 1)
        rows.append(
            {
                "bytes": len(data),
                "classification": "synthetic_test_input",
                "destination": destination,
                "namespace": namespace,
                "path": path,
                "sha256": _hash(data),
            }
        )
    archive_data = archive_path.read_bytes()
    document: dict[str, object] = {
        "archive": {
            "bytes": len(archive_data),
            "file": archive_path.name,
            "sha256": _hash(archive_data),
        },
        "format_version": 1,
        "members": rows,
    }
    _write_json(manifest_path, document)
    return archive_path, manifest_path, document


def test_two_namespace_provisioning_is_verified_and_idempotent(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    archive, manifest, _document = _write_bundle(
        bundle,
        {
            "repo/docs/packet.json": b'{"packet":true}\n',
            "workspace/implementations/tools/defs/read.yaml": b"name: read\n",
        },
    )
    repo_root = tmp_path / "repo"
    workspace_root = tmp_path / "workspace"
    repo_root.mkdir()
    workspace_root.mkdir()

    first = provision_immutable_inputs(
        archive, manifest, repo_root=repo_root, workspace_root=workspace_root
    )

    assert first.member_count == 2
    assert first.existing == ()
    assert first.written == (
        "repo/docs/packet.json",
        "workspace/implementations/tools/defs/read.yaml",
    )
    assert (repo_root / "docs/packet.json").read_bytes() == b'{"packet":true}\n'
    assert (workspace_root / "implementations/tools/defs/read.yaml").read_bytes() == b"name: read\n"
    assert stat.S_IMODE((repo_root / "docs/packet.json").stat().st_mode) == 0o644

    second = provision_immutable_inputs(
        archive, manifest, repo_root=repo_root, workspace_root=workspace_root
    )

    assert second.written == ()
    assert second.existing == first.written


def test_cli_requires_explicit_roots_and_reports_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    archive, manifest, _document = _write_bundle(bundle, {"repo/input": b"data"})
    repo_root = tmp_path / "repo"
    workspace_root = tmp_path / "workspace"
    repo_root.mkdir()
    workspace_root.mkdir()

    assert provision_main(
        [
            "--archive",
            str(archive),
            "--manifest",
            str(manifest),
            "--repo-root",
            str(repo_root),
            "--workspace-root",
            str(workspace_root),
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["written"] == ["repo/input"]


def test_conflicting_existing_bytes_fail_before_any_bundle_file_is_written(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    archive, manifest, _document = _write_bundle(
        bundle, {"repo/first": b"first", "workspace/conflict": b"expected"}
    )
    repo_root = tmp_path / "repo"
    workspace_root = tmp_path / "workspace"
    repo_root.mkdir()
    workspace_root.mkdir()
    (workspace_root / "conflict").write_bytes(b"different")

    with pytest.raises(ImmutableInputError, match="conflicting existing bytes"):
        provision_immutable_inputs(
            archive, manifest, repo_root=repo_root, workspace_root=workspace_root
        )

    assert not (repo_root / "first").exists()
    assert (workspace_root / "conflict").read_bytes() == b"different"


@pytest.mark.parametrize("field", ["sha256", "bytes"])
def test_archive_hash_or_size_drift_is_rejected(tmp_path: Path, field: str) -> None:
    archive, manifest, document = _write_bundle(tmp_path, {"repo/input": b"data"})
    archive_row = document["archive"]
    assert isinstance(archive_row, dict)
    archive_row[field] = "0" * 64 if field == "sha256" else int(archive_row[field]) + 1
    _write_json(manifest, document)

    with pytest.raises(ImmutableInputError, match="archive"):
        verify_immutable_inputs(archive, manifest)


@pytest.mark.parametrize("field", ["sha256", "bytes"])
def test_member_hash_or_size_drift_is_rejected(tmp_path: Path, field: str) -> None:
    archive, manifest, document = _write_bundle(tmp_path, {"repo/input": b"data"})
    rows = document["members"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    rows[0][field] = "0" * 64 if field == "sha256" else int(rows[0][field]) + 1
    _write_json(manifest, document)

    with pytest.raises(ImmutableInputError, match="member"):
        verify_immutable_inputs(archive, manifest)


def test_archive_member_set_drift_is_rejected(tmp_path: Path) -> None:
    archive, manifest, _document = _write_bundle(
        tmp_path,
        {"repo/expected": b"expected", "repo/unexpected": b"unexpected"},
        manifest_entries={"repo/expected": b"expected"},
    )

    with pytest.raises(ImmutableInputError, match="member set mismatch"):
        verify_immutable_inputs(archive, manifest)


@pytest.mark.parametrize(
    ("destination", "member_path", "message"),
    [
        ("../escape", "repo/../escape", "traversal"),
        ("/absolute", "repo//absolute", "relative"),
        ("bad\\path", "repo/bad\\path", "backslash"),
        ("bad\x00path", "repo/bad\x00path", "NUL"),
    ],
)
def test_unsafe_manifest_paths_are_rejected(
    tmp_path: Path, destination: str, member_path: str, message: str
) -> None:
    archive, manifest, document = _write_bundle(tmp_path, {"repo/input": b"data"})
    rows = document["members"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    rows[0]["destination"] = destination
    rows[0]["path"] = member_path
    _write_json(manifest, document)

    with pytest.raises(ImmutableInputError, match=message):
        verify_immutable_inputs(archive, manifest)


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644])
def test_symlink_and_special_file_zip_modes_are_rejected(tmp_path: Path, mode: int) -> None:
    archive, manifest, _document = _write_bundle(
        tmp_path, {"repo/input": b"data"}, modes={"repo/input": mode}
    )

    with pytest.raises(ImmutableInputError, match="0644 regular file"):
        verify_immutable_inputs(archive, manifest)


def test_duplicate_manifest_destinations_are_rejected(tmp_path: Path) -> None:
    archive, manifest, document = _write_bundle(tmp_path, {"repo/input": b"data"})
    rows = document["members"]
    assert isinstance(rows, list)
    rows.append(dict(rows[0]))
    _write_json(manifest, document)

    with pytest.raises(ImmutableInputError, match="duplicate destination"):
        verify_immutable_inputs(archive, manifest)


def test_duplicate_destinations_across_namespaces_are_rejected_when_roots_alias(
    tmp_path: Path,
) -> None:
    archive, manifest, _document = _write_bundle(
        tmp_path, {"repo/shared": b"same", "workspace/shared": b"same"}
    )
    shared_root = tmp_path / "shared-root"
    shared_root.mkdir()

    with pytest.raises(ImmutableInputError, match="duplicate resolved destination"):
        provision_immutable_inputs(
            archive, manifest, repo_root=shared_root, workspace_root=shared_root
        )


def test_destination_escape_through_existing_symlink_is_rejected(tmp_path: Path) -> None:
    archive, manifest, _document = _write_bundle(
        tmp_path, {"repo/link/escaped": b"must not escape"}
    )
    repo_root = tmp_path / "repo"
    workspace_root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    workspace_root.mkdir()
    outside.mkdir()
    try:
        (repo_root / "link").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(ImmutableInputError, match="escapes namespace root"):
        provision_immutable_inputs(
            archive, manifest, repo_root=repo_root, workspace_root=workspace_root
        )

    assert not (outside / "escaped").exists()


def test_committed_bundle_matches_manifest_and_source_allowlist() -> None:
    verified = verify_immutable_inputs(REAL_BUNDLE, REAL_MANIFEST)
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    members = manifest["members"]
    with zipfile.ZipFile(REAL_BUNDLE) as archive:
        phase16_progress = json.loads(
            archive.read("workspace/docs_tmp/phase_16/BB_ER_PROGRESS.json")
        )
    phase16_required = {
        "docs_tmp/phase_16/BB_ER_PROGRESS.json",
        *(
            evidence["path"]
            for workstream in phase16_progress["workstreams"]
            for item in workstream["items"]
            for evidence in item.get("evidence", [])
        ),
    }
    assert {
        row["destination"]
        for row in members
        if row["classification"] in {"phase16_score_authority", "phase16_score_evidence"}
    } == phase16_required

    assert verified.archive_sha256 == manifest["archive"]["sha256"]
    assert len(verified.members) == len(members) > 0
    assert manifest["secret_scan"] == {
        "finding_count": 0,
        "patterns": [
            "authorization_bearer",
            "aws_access_key",
            "env_secret_assignment",
            "github_token",
            "private_key",
            "provider_key",
            "slack_token",
        ],
        "result": "passed",
        "scanned_member_count": len(members),
    }
    assert {row["classification"] for row in members} == {
        "accepted_immutable_packet",
        "external_tool_definition",
        "phase15_pi_source_archive",
        "phase15_static_validator_input",
        "phase15_static_report_authority",
        "raw_target_input",
        "source_freeze_provenance",
        "phase16_score_authority",
        "phase16_score_evidence",
    }

    full_packet_roots = {
        "codex_cli_e4_capture_probe_v1",
        "oh_my_pi_p6_0_l1_config_context_tool_surface",
        "oh_my_pi_p6_0_l2_tool_execution",
        "oh_my_pi_p6_0_l3_command_network_hook",
        "oh_my_pi_p6_0_l4_mcp_browser_resource",
    }
    raw_allowed = {
        "pi_p5_l1_cli_config_context_tool_surface": {
            "pi_p5_target_probe.mjs",
            "target_probe_output.json",
            "target_setup_and_capture_report.json",
        },
        "pi_p5_l2_extension_session_residual": {
            "pi_p5_residual_target_probe.mjs",
            "target_probe_output.json",
            "target_setup_and_capture_report.json",
        },
        "oh_my_pi_p6_0_l5_memory_compaction": {
            "oh_my_pi_p6_0_l5_memory_compaction_probe.mjs",
            "target_probe_output.json",
            "target_setup_and_capture_report.json",
            "target_transcript_fixture.json",
        },
        "oh_my_pi_p6_0_l6_tui_projection": {
            "oh_my_pi_p6_0_l6_tui_projection_probe.mjs",
            "target_probe_output.json",
            "target_setup_and_capture_report.json",
        },
    }
    workspace_exact = {
        "docs_tmp/phase_15/JUNE_26_FEATURE_AUDIT_PRO_ATTACHMENTS_FLAT/01_pi_mono_git_tracked.zip",
        "docs_tmp/phase_15/source_freezes/pi_mono_0_57_1_archive_freeze_provenance.json",
        "docs_tmp/phase_15/source_freezes/oh_my_pi_main_5356713e_freeze_provenance.json",
        "docs_tmp/phase_15/BB_E4_CURRENT_BASELINE.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_PROGRESS.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_VALIDATION_REPORT.json",
        "docs_tmp/phase_15/oh_my_pi_p6/BB_E4_OH_MY_PI_P6_TERMINAL_HASH_MANIFEST.json",
        "docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_MASTER_PLAN.md",
        "docs_tmp/phase_15/BB_E4_PRIMITIVE_PARITY_SCORECARD.json",
        "docs_tmp/phase_15/BB_E4_SCORE_SUBLEDGER.json",
        "docs_tmp/phase_15/BB_E4_EVIDENCE_GOVERNANCE.json",
        "docs_tmp/phase_15/BB_E4_NEGATIVE_PRIMITIVE_LEDGER.json",
        "docs_tmp/phase_15/BB_E4_OH_MY_PI_P6_COMPLETION_PLAYBOOK.md",
        "docs_tmp/phase_15/BB_E4_PRIMITIVE_DECISION_LEDGER.json",
        "docs_tmp/phase_15/BB_E4_REMAINING_PREREQUISITES_AFTER_L6.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_HASH_MANIFEST.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_BLOCKED_READY_REPORT.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_BLOCKED_READY_REPORT.md",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_FINAL_HANDOFF.md",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_FINAL_HASH_MANIFEST.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_FINAL_VALIDATION_REPORT.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_HASH_REPORT.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_POST_HASH_VALIDATION.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/BB_E4_TARGET_SUPPORT_VALIDATION_REPORT.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/bb_replay_result.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/canonical_capture/exports/replay_session.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/canonical_capture/rollout.jsonl",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/canonical_capture/exit_code.txt",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/canonical_capture/scenario.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/canonical_capture/stderr.txt",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/comparator_report.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/parity_results.jsonl",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/raw_capture_manifest.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/secret_scan_report.json",
        "docs_tmp/phase_15/pro_requests/e4_breakthrough_20260629/execution/codex_gpt55_capture_probe/lineage_report.json",
    }
    forbidden_l5_l6_names = {
        "bb_replay_result.json",
        "comparator_report.json",
        "memory_compaction_plan.v1.json",
        "parity_results.json",
        "prevalidation_report.json",
        "projection_events.json",
        "raw_capture_manifest.json",
        "secret_scan_report.json",
        "transcript_continuation_patch.v1.json",
    }
    cache_parts = {"__pycache__", ".pytest_cache", "cache", "caches", "bun-cache", "node_modules"}

    for row in members:
        destination = row["destination"]
        parts = Path(destination).parts
        lowered = {part.lower() for part in parts}
        assert "target_home" not in lowered
        assert not (cache_parts & lowered)
        assert "node_gate" not in destination.lower()
        assert "support_claims" not in lowered
        assert "source_index" not in destination.lower()
        if row["classification"] != "phase16_score_evidence":
            assert "atomic_feature_ledger" not in destination.lower()
        if row["namespace"] == "workspace":
            assert (
                destination in workspace_exact
                or destination in phase16_required
                or destination.startswith("implementations/tools/defs/")
            )
            continue
        prefix = "docs/conformance/e4_target_support/"
        assert destination.startswith(prefix)
        packet, relative = destination[len(prefix) :].split("/", 1)
        if packet in full_packet_roots:
            continue
        assert packet in raw_allowed
        assert relative.startswith("raw/") or relative in raw_allowed[packet]
        assert Path(relative).name not in forbidden_l5_l6_names
