from __future__ import annotations

import base64
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from breadboard.rl.phase5 import f1_preflight as f1
from scripts.rl_phase5 import build_f1_phase3_payload as payload_builder
from scripts.rl_phase5 import f1_container_entry as entry

ATTEMPT = "f1-12345678"


def test_deterministic_fixture_tokens_include_generated_candidate() -> None:
    seed = bytes(range(32))
    derived = entry.derive_secrets(seed)
    values = entry.deterministic_token_hex_values(seed, derived)
    assert len(values) == 4
    assert values[:3] == tuple(
        derived[handle].split(b"-", 2)[-1].decode()
        for handle in ("api-auth", "policy-callback", "receipt-signing")
    )
    assert len(values[3]) == 24
    assert values == entry.deterministic_token_hex_values(seed, derived)


def _marker_lines(**change: object) -> bytes:
    markers = []
    for sequence, (kind, path) in enumerate(f1.TARGET_ARTIFACTS.items()):
        marker = {
            "schema_version": f1.MARKER_SCHEMA,
            "attempt_id": ATTEMPT,
            "sequence": sequence,
            "kind": kind,
            "artifact_path": path,
            "size_bytes": 2,
            "sha256": "a" * 64,
        }
        if sequence == 0:
            marker.update(change)
        markers.append(f1.MARKER_PREFIX.encode() + f1.canonical_json_bytes(marker))
    return b"\n".join(markers + [f1.RESULT_PREFIX.encode() + b"{}", b""])


def test_markers_require_canonical_exact_complete_inventory() -> None:
    assert len(f1.parse_artifact_markers(_marker_lines(), ATTEMPT)) == len(f1.TARGET_ARTIFACTS)
    with pytest.raises(f1.F1ValidationError, match="exact keys"):
        f1.parse_artifact_markers(_marker_lines(passed=True), ATTEMPT)
    noncanonical = _marker_lines().replace(b'"artifact_path":', b'"artifact_path": ', 1)
    with pytest.raises(f1.F1ValidationError, match="not canonical"):
        f1.parse_artifact_markers(noncanonical, ATTEMPT)
    with pytest.raises(f1.F1ValidationError, match="incomplete"):
        f1.parse_artifact_markers(b"\n".join(_marker_lines().splitlines()[1:]) + b"\n", ATTEMPT)


def test_archive_rejects_traversal_links_duplicates_and_directories(tmp_path: Path) -> None:
    cases = (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("directory", tarfile.DIRTYPE))
    for index, (name, member_type) in enumerate(cases):
        archive = tmp_path / f"bad-{index}.tgz"
        with tarfile.open(archive, "w:gz") as target:
            item = tarfile.TarInfo(name)
            item.type = member_type
            item.size = 0
            if member_type == tarfile.SYMTYPE:
                item.linkname = "target"
            target.addfile(item, io.BytesIO())
        with pytest.raises(f1.F1ValidationError):
            f1.safe_extract_archive(archive, tmp_path / f"out-{index}")


def _scheduler(**observed_change: object) -> dict[str, object]:
    observed = {"job_id": "42", "partition": "gpu", "node_list": "node1", "node_count": 1, "task_count": 1, "gpus_on_node": 1, "hostname": "node1"}
    observed.update(observed_change)
    return {"schema_version": "bb.rl.f1.scheduler-observation.v1", "target_alias": f1.TARGET_ALIAS, "requested": {"partition": "gpu", "nodes": 1, "tasks": 1, "gpus": 1}, "observed": observed, "started_utc": "2026-07-11T12:34:56Z", "scontrol": {"argv": ["scontrol", "show", "job", "-o", "42"], "exit_code": 0, "stdout": "JobId=42", "stderr": ""}}


def test_scheduler_rejects_topology_and_bool_laundering() -> None:
    assert f1._validate_scheduler(_scheduler()) == ("42", "20260711T123456Z")
    for change in ({"node_count": 2}, {"task_count": True}, {"partition": "other"}, {"gpus_on_node": 0}, {"node_list": "other-node"}):
        with pytest.raises(f1.F1ValidationError):
            f1._validate_scheduler(_scheduler(**change))


def _image_observations() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    image_id = "sha256:" + "b" * 64
    requested_digest = f1.IMAGE_REF.split("@", 1)[1]
    image = {"schema_version": "bb.rl.f1.image-observation.v1", "requested_ref": f1.IMAGE_REF, "transport": "docker_registry", "pull": {"exit_code": 0, "stdout": "", "stderr": ""}, "inspect": {"id": image_id, "repo_digests": ["python@" + requested_digest], "os": "linux", "architecture": "amd64"}}
    container = {"schema_version": "bb.rl.f1.container-observation.v1", "container_id": "cid", "name": "bb-" + ATTEMPT, "label": "bb.rl.f1.attempt=" + ATTEMPT, "image_id": image_id, "create_exit_code": 0, "start_exit_code": 0}
    cleanup = {"schema_version": "bb.rl.f1.container-cleanup-observation.v1", "remove_exit_code": 0, "name_matches": [], "label_matches": []}
    return image, container, cleanup


def test_image_join_and_cleanup_residue_are_reconstructed() -> None:
    image, container, cleanup = _image_observations()
    f1._validate_image(image, container, cleanup, ATTEMPT)
    container["image_id"] = "sha256:" + "c" * 64
    with pytest.raises(f1.F1ValidationError, match="identity"):
        f1._validate_image(image, container, cleanup, ATTEMPT)
    container["image_id"] = image["inspect"]["id"]  # type: ignore[index]
    cleanup["label_matches"] = ["residue"]
    with pytest.raises(f1.F1ValidationError, match="residue"):
        f1._validate_image(image, container, cleanup, ATTEMPT)


def test_structured_secret_and_absolute_path_rejected() -> None:
    for value in ({"authorization": "redacted"}, {"nested": {"secret_path": "relative"}}, {"path": "/private/seed"}, {"line": "Authorization: Bearer x"}):
        with pytest.raises(f1.F1ValidationError):
            f1._reject_sensitive_structured(value, "artifact")
    f1._reject_sensitive_structured(
        {"export_authorization_refs": ["sha256:" + "a" * 64]},
        "artifact",
    )
    f1._reject_sensitive_structured(
        {
            "request": {"path": "/v2/episodes/episode-1/envelopes/closed"},
            "callback": {"path": "/v1/responses"},
        },
        "artifact",
    )


def test_composition_evidence_omits_live_paths_and_preserves_hash_identity(
    tmp_path: Path,
) -> None:
    private = tmp_path / "f1-private"
    manifest = entry.canon(
        {
            "schema_version": "bb.rl.harness-composition-manifest.v1",
            "composition_id": "production-fixture-composition",
            "secret_handles": {
                "records": [
                    {"handle_id": "api-auth"},
                    {"handle_id": "policy-callback"},
                    {"handle_id": "receipt-signing"},
                ]
            },
            "runtime": {"executable_path": str(private / "runtime/python")},
        }
    )
    reference = entry.canon(
        {
            "schema_version": "bb.rl.harness-composition-ref.v1",
            "manifest_path": str(private / "composition-manifest.json"),
            "manifest_sha256": "sha256:" + entry.digest(manifest),
            "manifest_size_bytes": len(manifest),
            "manifest_media_type": "application/vnd.breadboard.harness-composition+json;version=1",
        }
    )
    ref_observation, manifest_observation, inspect_observation = (
        entry.composition_evidence_observations(
            composition_ref=reference,
            composition_manifest=manifest,
            inspect_stdout=manifest,
            inspect_stderr=b"",
            inspect_exit_code=0,
            private_root=private,
        )
    )
    rendered = entry.canon(
        [ref_observation, manifest_observation, inspect_observation]
    )
    assert str(private).encode() not in rendered
    assert b"composition-manifest.json" not in rendered
    assert (
        ref_observation["composition_ref_sha256"] == entry.digest(reference)
    )
    assert manifest_observation["semantic"] == inspect_observation["semantic"]
    f1._reject_absolute_paths(
        manifest_observation["semantic"], "composition semantic"
    )

def test_composition_inspect_binds_real_summary_to_manifest() -> None:
    digest = lambda character: "sha256:" + character * 64
    compiler = {"compiler_id": "breadboard.server-config-compiler"}
    manifest = {
        "composition_id": "production-fixture-composition",
        "config_bundle_ref": {"sha256": digest("1")},
        "admitted_set_ref": {"sha256": digest("2")},
        "authority_bundle_ref": {"sha256": digest("3")},
        "control_plane": {
            "compiler": compiler,
            "admission_policy_ref": {"sha256": digest("4")},
            "receipt_authenticator": {
                "algorithm": "hmac-sha256-v1",
                "key_id": "production-receipt-key",
            },
        },
        "secret_handles": {
            "records": [
                {"handle_id": "api-auth"},
                {"handle_id": "receipt-signing"},
            ]
        },
        "selector_catalog": {
            "direct": [{"sha256": digest("5")}],
            "weighted": [],
        },
        "stores": {
            "cas": {"authority_id": "cas"},
            "workspace": {"authority_id": "workspace"},
            "lease_ttl_seconds": 300,
        },
    }
    summary = {
        "schema_version": "bb.rl.harness-composed.v1",
        "composition_id": manifest["composition_id"],
        "input_manifest_digest": digest("6"),
        "compiler_identity": compiler,
        "config_bundle_digest": digest("1"),
        "admitted_set_digest": digest("2"),
        "authority_bundle_digest": digest("3"),
        "admission_policy_digest": digest("4"),
        "receipt_algorithm": "hmac-sha256-v1",
        "receipt_key_id": "production-receipt-key",
        "secret_handle_ids": ["api-auth", "receipt-signing"],
        "selector_digests": [digest("5")],
        "evidence_authority_digest": digest("7"),
        "installed_authority_digest": digest("8"),
        "registry_snapshot_digest": digest("9"),
        "revocation_state_digests": [digest("a")],
        "runner_registry_digest": digest("b"),
        "server_authority_digest": digest("c"),
        "store_authority_digests": [digest("d"), digest("e")],
    }
    f1._validate_composition_inspect(summary, manifest, digest("6"))
    summary["input_manifest_digest"] = digest("f")
    with pytest.raises(f1.F1ValidationError, match="semantic join"):
        f1._validate_composition_inspect(summary, manifest, digest("6"))


def test_target_private_scanner_handles_non_utf8_seed(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    output = artifacts / "output.json"
    output.write_bytes(b"safe")
    seed = b"\xff" * 32
    entry.reject_private_material(artifacts, (seed,), ())
    output.write_bytes(base64.b64encode(seed))
    with pytest.raises(RuntimeError, match="private seed"):
        entry.reject_private_material(artifacts, (seed,), ())


def test_outer_phase3_target_identity_is_bound_without_scheduler_synthesis(
    tmp_path: Path,
) -> None:
    attempt = tmp_path / "renamed-staging"
    outer = attempt / "outer"
    outer.mkdir(parents=True)
    raw_log = b"PHASE3_NODE=cnode-42\nPHASE3_SLURM_JOB_ID=314159\n"
    target_run_id = "20260711T190050Z-slurm-314159"
    manifest = {
        "schema_version": "bb.rl.phase3.command_log_manifest.v1",
        "target_run_id": target_run_id,
        "commands": [
            {
                "command_id": ATTEMPT,
                "argv": [
                    "run_phase3_target_command.py",
                    "--ssh-alias",
                    f1.TARGET_ALIAS,
                    "--partition",
                    f1.PARTITION,
                    "--command-id",
                    ATTEMPT,
                    "--target-run-id",
                    "20260711T190050Z-slurm-pending",
                ],
                "status": "passed",
                "exit_code": 0,
                "blocked_reason": "",
                "component_failed_count": 0,
                "slurm_job_id": "314159",
                "node": "cnode-42",
                "raw_log_sha256": "sha256:" + f1.sha256_bytes(raw_log),
                "target_run_id": target_run_id,
            }
        ],
    }
    (outer / "phase3-command-log-manifest.json").write_bytes(
        f1.canonical_json_bytes(manifest)
    )
    (outer / "phase3-command.log").write_bytes(raw_log)
    scheduler = {
        "observed": {"job_id": "314159", "hostname": "cnode-42"}
    }
    attempt_record = {"outer_target_run_id": target_run_id}
    inventory, observed_target_run_id = f1._validate_outer_phase3(
        attempt, ATTEMPT, scheduler, attempt_record
    )
    assert observed_target_run_id == target_run_id
    assert {item["path"] for item in inventory} == f1.OUTER_ARTIFACTS
    with pytest.raises(f1.F1ValidationError, match="target run identity"):
        f1._validate_outer_phase3(
            attempt,
            ATTEMPT,
            scheduler,
            {"outer_target_run_id": "20260711T190051Z-slurm-314159"},
        )

def test_canonical_top_level_symlink_is_rejected_before_resolution(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target-run"
    target.mkdir()
    link = tmp_path / "canonical-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(f1.F1ValidationError, match="must not be a symlink"):
        f1.verify_canonical(link)


def test_phase3_payload_is_deterministic_secret_free_and_has_no_nested_srun(
    tmp_path: Path,
) -> None:
    breadboard_root = Path(__file__).resolve().parents[3]
    wrapper_root = breadboard_root.parent / "verl_wrapper_breadboard_integration_20260709"
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_result = payload_builder.build_payload(
        breadboard_root=breadboard_root,
        wrapper_root=wrapper_root,
        output=first,
        attempt_id=ATTEMPT,
    )
    second_result = payload_builder.build_payload(
        breadboard_root=breadboard_root,
        wrapper_root=wrapper_root,
        output=second,
        attempt_id=ATTEMPT,
    )
    assert first.read_bytes() == second.read_bytes()
    assert first_result == second_result
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {
            "F1_PAYLOAD_MANIFEST.json",
            "f1-source-bundle.tar.gz",
            "run.sh",
        }
        run_script = archive.read("run.sh").decode()
        assert "run_f1_target_command.py\" remote" in run_script
        assert "srun" not in run_script
        assert "ssh" not in run_script
        assert "seed" not in run_script.lower()
        assert archive.getinfo("run.sh").external_attr >> 16 & 0o777 == 0o700
