from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts.rl_phase5 import prepare_phase5_v2_migration as prepare_module
from scripts.rl_phase5 import replay_phase5_v2_prepared_handoff as replay_module


REPO_ROOT = Path(__file__).resolve().parents[3]
REVISION_ID = "v2.0.0-rc5-20260717"
FIXTURE_ARCHIVE = (
    Path(__file__).resolve().parent / "fixtures" / "phase5_v2_rc5_inputs.zip"
)
FIXTURE_ARCHIVE_SHA256 = (
    "sha256:9e69cf1fb66fd5094d036e8027e3a83b38b70d272f42922c6bd290d131d0ff07"
)
V1_ACTIVE_STATUS_SHA256 = (
    "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
)
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
MIGRATION_TRANSACTION_SHA256 = (
    "sha256:792702e6d6abdbc78244c37e6a464de974079aa4820243831dcb81822473673f"
)
PREPARE_SCRIPT = REPO_ROOT / "scripts/rl_phase5/prepare_phase5_v2_migration.py"
VALIDATE_SCRIPT = (
    REPO_ROOT / "scripts/rl_phase5/validate_phase5_v2_migration_preparation.py"
)
REPLAY_SCRIPT = REPO_ROOT / "scripts/rl_phase5/replay_phase5_v2_prepared_handoff.py"
SPEC_FREEZE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "phase5_v2_rc5_spec_freeze_decision.json"
)
SPEC_FREEZE_SHA256 = (
    "sha256:e06abb5bf8b0bcbeff6c26721a241eba8961856822b030811df69fd3b8d1da36"
)
BUNDLE_FILES = {
    "BEADS_RESOLUTION.json",
    "AFTER_IMAGES.json",
    "BEFORE_IMAGES.json",
    "BEFORE_IMAGE_beads_projection.bin",
    "BEFORE_IMAGE_root_active_selector.bin",
    "BEFORE_IMAGE_v2_event_log.bin",
    "EVENT_CHAIN.json",
    "EVENT_APPEND_METADATA.json",
    "EVENT_APPEND_PAYLOAD.json",
    "FRESH_WORKER_PREPARATION_REPORT.json",
    "MIGRATION_PREPARATION_REPORT.json",
    "PREPARED_ACTIVE_STATUS.json",
    "PREPARED_ROOT_SELECTOR.json",
    "PREPARED_RUN_QUEUE.json",
    "ROLLBACK_DESCRIPTORS.json",
    "SESSION_PRE_HANDOFF_SNAPSHOT.bin",
    "SPEC_FREEZE_DECISION.json",
    "SESSION_PROJECTION.json",
}


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _extract_repo_fixture(destination: Path) -> tuple[Path, Path]:
    archive_raw = FIXTURE_ARCHIVE.read_bytes()
    assert _sha256(archive_raw) == FIXTURE_ARCHIVE_SHA256
    with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
        infos = archive.infolist()
        info_by_name = {info.filename: info for info in infos}
        assert len(info_by_name) == len(infos)

        manifest_name = "revision/ARTIFACT_MANIFEST.json"
        manifest_raw = archive.read(manifest_name)
        assert _sha256(manifest_raw) == ARTIFACT_MANIFEST_SHA256
        manifest = json.loads(manifest_raw)
        expected: dict[str, tuple[str, int, int]] = {
            "ACTIVE_STATUS.json": (V1_ACTIVE_STATUS_SHA256, 1_551, 0o644),
            manifest_name: (ARTIFACT_MANIFEST_SHA256, len(manifest_raw), 0o444),
        }
        for row in manifest["files"]:
            expected[f"revision/{row['path']}"] = (
                row["sha256"],
                row["size"],
                int(row["mode"], 8),
            )

        assert len(expected) == len(manifest["files"]) + 2
        assert set(info_by_name) == set(expected)
        payloads: dict[str, bytes] = {}
        for name, (
            expected_sha256,
            expected_size,
            expected_mode,
        ) in expected.items():
            info = info_by_name[name]
            archive_mode = info.external_attr >> 16
            assert info.create_system == 3
            assert info.compress_type == zipfile.ZIP_STORED
            assert stat.S_IFMT(archive_mode) == stat.S_IFREG
            assert stat.S_IMODE(archive_mode) == expected_mode
            payload = archive.read(info)
            assert len(payload) == expected_size
            assert _sha256(payload) == expected_sha256
            payloads[name] = payload

    destination.mkdir(mode=0o700)
    for name, payload in payloads.items():
        target = destination / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(payload)
        target.chmod(expected[name][2])

    extracted = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert extracted == set(expected)
    for name, (expected_sha256, expected_size, expected_mode) in expected.items():
        target = destination / name
        payload = target.read_bytes()
        assert len(payload) == expected_size
        assert _sha256(payload) == expected_sha256
        assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    return destination / "revision", destination / "ACTIVE_STATUS.json"


def _load_spec_freeze_fixture() -> dict[str, Any]:
    raw = SPEC_FREEZE_FIXTURE.read_bytes()
    assert len(raw) == 1_585
    assert f"sha256:{hashlib.sha256(raw).hexdigest()}" == SPEC_FREEZE_SHA256
    return json.loads(raw)


@dataclass(frozen=True)
class MigrationFixture:
    execution_root: Path
    revision: Path
    beads_export: Path
    session_state: Path
    spec_freeze_decision: Path
    event_log: Path
    output_dir: Path

    @property
    def live_paths(self) -> tuple[Path, ...]:
        paths = (
            self.execution_root / "ACTIVE_STATUS.json",
            self.beads_export,
            self.session_state,
            self.spec_freeze_decision,
        )
        if self.event_log.exists():
            return (*paths, self.event_log)
        return paths


def _make_fixture(
    tmp_path: Path,
    *,
    valid_spec_freeze: bool = True,
    session_authority: dict[str, Any] | None = None,
    event_log_present: bool = True,
) -> MigrationFixture:
    frozen_revision, active_status = _extract_repo_fixture(
        tmp_path / "repo-fixture"
    )
    execution_root = tmp_path / "execution"
    revision = execution_root / "versions/v2-two-track" / REVISION_ID
    shutil.copytree(frozen_revision, revision)
    shutil.copyfile(active_status, execution_root / "ACTIVE_STATUS.json")

    spec_freeze = _load_spec_freeze_fixture()
    if not valid_spec_freeze:
        spec_freeze["decision"] = "DO_NOT_FREEZE"
    spec_freeze_decision = tmp_path / "RC5_SPEC_FREEZE_DECISION.json"
    beads_migration = _load_json(revision / "BEADS_MIGRATION.json")
    spec_freeze_decision.write_bytes(_canonical_bytes(spec_freeze))
    legacy_parent = {
        "close_reason": None,
        "id": "bb-auh",
        "status": "open",
    }
    beads_export = tmp_path / "beads-export.json"
    beads_export.write_bytes(
        _canonical_bytes(
            {
                "issues": [
                    legacy_parent,
                    *beads_migration["legacy_snapshot"],
                ]
            }
        )
    )

    session_state = tmp_path / "session-state.json"
    session_document = {
        "queue": [
            {
                "issue_id": "bb-auh.59",
                "state": "paused_legacy",
            }
        ],
        "schema_version": "bb.session.phase5.v1",
        "target_lease": None,
        "todos": [
            {
                "issue_id": "bb-auh.59",
                "state": "in_progress",
            }
        ],
    }
    if session_authority is not None:
        session_document.update(session_authority)
    session_state.write_bytes(_canonical_bytes(session_document))
    event_log = execution_root / "EVENT_CHAIN.json"
    if event_log_present:
        event_log.write_bytes(_canonical_bytes([]))
    return MigrationFixture(
        execution_root=execution_root,
        revision=revision,
        beads_export=beads_export,
        session_state=session_state,
        spec_freeze_decision=spec_freeze_decision,
        event_log=event_log,
        output_dir=tmp_path / "prepared-bundle",
    )


def _prepare(fixture: MigrationFixture) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PREPARE_SCRIPT),
            "--execution-root",
            str(fixture.execution_root),
            "--revision",
            str(fixture.revision),
            "--beads-export",
            str(fixture.beads_export),
            "--session-state",
            str(fixture.session_state),
            "--spec-freeze-decision",
            str(fixture.spec_freeze_decision),
            "--migration-id",
            "phase5-v2-test-migration",
            "--output-dir",
            str(fixture.output_dir),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _prepare_namespace(fixture: MigrationFixture) -> argparse.Namespace:
    return argparse.Namespace(
        execution_root=fixture.execution_root,
        revision=fixture.revision,
        beads_export=fixture.beads_export,
        session_state=fixture.session_state,
        spec_freeze_decision=fixture.spec_freeze_decision,
        migration_id="phase5-v2-test-migration",
        output_dir=fixture.output_dir,
    )


def _validate(
    fixture: MigrationFixture, report: Path
) -> subprocess.CompletedProcess[str]:
    report.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--execution-root",
            str(fixture.execution_root),
            "--revision",
            str(fixture.revision),
            "--beads-export",
            str(fixture.beads_export),
            "--session-state",
            str(fixture.session_state),
            "--spec-freeze-decision",
            str(fixture.spec_freeze_decision),
            "--bundle",
            str(fixture.output_dir),
            "--output-root",
            str(report.parent),
            "--report",
            str(report),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _live_bytes(fixture: MigrationFixture) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in fixture.live_paths}


def _rewrite_bundle_json(
    fixture: MigrationFixture, name: str, mutate: Any
) -> None:
    path = fixture.output_dir / name
    value = json.loads(path.read_bytes())
    mutate(value)
    fixture.output_dir.chmod(0o755)
    path.chmod(0o644)
    path.write_bytes(_canonical_bytes(value))
    path.chmod(0o444)
    fixture.output_dir.chmod(0o555)


def test_prepare_and_validate_are_local_only_and_worker_deterministic(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    before = _live_bytes(fixture)

    prepared = _prepare(fixture)

    assert prepared.returncode == 0, prepared.stderr
    assert _live_bytes(fixture) == before
    assert {path.name for path in fixture.output_dir.iterdir()} == BUNDLE_FILES

    report = _load_json(fixture.output_dir / "MIGRATION_PREPARATION_REPORT.json")
    assert report["artifact_manifest_sha256"] == ARTIFACT_MANIFEST_SHA256
    assert report["migration_transaction_sha256"] == MIGRATION_TRANSACTION_SHA256
    assert report["spec_freeze_decision_sha256"] == SPEC_FREEZE_SHA256
    assert report["authority_decision_sha256"] == SPEC_FREEZE_SHA256
    assert len(report["before_images"]) == 3
    assert len(report["after_images"]) == 3
    assert report["commit_results"] == []
    assert report["consumer_barrier_acquired"] is False
    assert report["consumer_barrier_released"] is False
    assert report["released_lease"] is False
    assert report["prepared_only"] is True

    worker_report = _load_json(
        fixture.output_dir / "FRESH_WORKER_PREPARATION_REPORT.json"
    )
    assert worker_report["frozen_contract_receipt"]["worker_count"] == 2
    assert len(worker_report["workers"]) == 2
    assert len({worker["pid"] for worker in worker_report["workers"]}) == 2
    receipt = worker_report["frozen_contract_receipt"]
    assert worker_report["frozen_contract_passed"] is False
    assert receipt["result"] == "non_conformance_preparation_replay"
    assert receipt["contract_sha256"].startswith("sha256:")
    assert all(
        set(worker)
        == {
            "ambient_inputs_used",
            "derived_action",
            "execution_frontier",
            "input_hashes",
            "pid",
            "target_execution_allowed",
        }
        for worker in worker_report["workers"]
    )
    assert all(worker["ambient_inputs_used"] for worker in worker_report["workers"])
    assert all(worker["target_execution_allowed"] is False for worker in worker_report["workers"])
    assert all(worker["execution_frontier"] == ["AT0"] for worker in worker_report["workers"])

    validation_report = tmp_path / "validation-output" / "validation-report.json"
    validated = _validate(fixture, validation_report)

    assert validated.returncode == 0, validated.stderr
    assert _load_json(validation_report)["result"] == "pass"
    assert _live_bytes(fixture) == before


def test_prepare_and_validate_preserve_absent_event_log_genesis(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path, event_log_present=False)
    before = _live_bytes(fixture)

    prepared = _prepare(fixture)

    assert prepared.returncode == 0, prepared.stderr
    assert _live_bytes(fixture) == before
    assert not fixture.event_log.exists()
    assert not (
        fixture.output_dir / "BEFORE_IMAGE_v2_event_log.bin"
    ).exists()
    assert {
        path.name for path in fixture.output_dir.iterdir()
    } == BUNDLE_FILES - {"BEFORE_IMAGE_v2_event_log.bin"}

    before_images = _load_json(fixture.output_dir / "BEFORE_IMAGES.json")
    event_before = before_images["images"][0]
    assert event_before["store_id"] == "v2_event_log"
    assert event_before["presence"] == "absent"
    assert event_before["bytes_sha256"] is None
    assert event_before["before_image_ref"] is None
    assert event_before["snapshot"] is None
    assert event_before["size"] is None
    assert event_before["parent_device"] >= 0
    assert event_before["parent_inode"] >= 0

    rollback = _load_json(fixture.output_dir / "ROLLBACK_DESCRIPTORS.json")
    event_rollback = rollback["operations"][0]
    assert event_rollback["before_presence"] == "absent"
    assert event_rollback["before_image_ref"] is None
    assert event_rollback["operation_type"] == "append_compensation_event"
    assert event_rollback["required_payload_bindings"] == [
        "before_presence",
        "before_head_sha256",
        "genesis_created",
        "committed_event_sha256s",
        "restored_root_active_selector_sha256",
        "restored_beads_schema_sha256",
        "restored_beads_canonical_rows_sha256",
    ]
    assert event_rollback["required_receipt_bindings"] == [
        "compensation_event_sha256",
        "compensation_head_sha256",
    ]

    validation_report = tmp_path / "validation-output" / "validation-report.json"
    validated = _validate(fixture, validation_report)

    assert validated.returncode == 0, validated.stderr
    validation = _load_json(validation_report)
    assert validation["result"] == "pass"
    assert validation["live_source_presence"]["v2_event_log"] == "absent"
    assert validation["live_source_hashes"]["v2_event_log"] is None
    assert _live_bytes(fixture) == before


@pytest.mark.parametrize("final_name", ["report.json", "worker-output.json"])
def test_receipt_publication_keeps_partial_bytes_off_the_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_name: str,
) -> None:
    output_root = tmp_path / "receipt-output"
    output_root.mkdir()
    final_path = output_root / final_name
    real_write = replay_module.os.write
    first_write = True

    def write_prefix_then_fail(descriptor: int, payload: Any) -> int:
        nonlocal first_write
        if first_write:
            first_write = False
            prefix_size = max(1, len(payload) // 2)
            return real_write(descriptor, payload[:prefix_size])
        raise OSError("injected partial-write failure")

    monkeypatch.setattr(replay_module.os, "write", write_prefix_then_fail)

    with pytest.raises(OSError, match="injected partial-write failure"):
        replay_module._write_new_canonical(
            output_root,
            final_path,
            {"result": "complete"},
            protected=(),
        )

    assert not final_path.exists()
    assert list(output_root.iterdir()) == []


@pytest.mark.parametrize("final_name", ["report.json", "worker-output.json"])
def test_receipt_publication_does_not_replace_a_competing_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_name: str,
) -> None:
    output_root = tmp_path / "receipt-output"
    output_root.mkdir()
    final_path = output_root / final_name
    original = b"competing durable receipt\n"
    competing_inode: int | None = None

    def publish_competing_final(*args: Any, **kwargs: Any) -> None:
        nonlocal competing_inode
        final_path.write_bytes(original)
        competing_inode = final_path.stat().st_ino
        raise FileExistsError("injected competing final")

    monkeypatch.setattr(replay_module.os, "link", publish_competing_final)

    with pytest.raises(FileExistsError, match="injected competing final"):
        replay_module._write_new_canonical(
            output_root,
            final_path,
            {"result": "new"},
            protected=(),
        )

    assert final_path.read_bytes() == original
    assert final_path.stat().st_ino == competing_inode
    assert [path.name for path in output_root.iterdir()] == [final_name]


def test_retained_before_images_preserve_private_source_readability(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    fixture.beads_export.chmod(0o600)
    fixture.session_state.chmod(0o600)

    prepared = _prepare(fixture)

    assert prepared.returncode == 0, prepared.stderr
    before_document = _load_json(fixture.output_dir / "BEFORE_IMAGES.json")
    report = _load_json(
        fixture.output_dir / "MIGRATION_PREPARATION_REPORT.json"
    )
    rollback_document = _load_json(
        fixture.output_dir / "ROLLBACK_DESCRIPTORS.json"
    )
    before_by_store = {
        image["store_id"]: image for image in before_document["images"]
    }
    report_by_store = {
        image["store_id"]: image for image in report["before_images"]
    }
    rollback_by_store = {
        operation["store_id"]: operation
        for operation in rollback_document["operations"]
    }
    image = before_by_store["beads_projection"]
    operation = rollback_by_store["beads_projection"]
    assert image["snapshot"]["mode"] == 0o600
    assert image["source_mode"] == 0o600
    assert image["retained_mode"] == 0o400
    assert image["before_image_ref"]["source_mode"] == 0o600
    assert image["before_image_ref"]["retained_mode"] == 0o400
    assert report_by_store["beads_projection"] == image
    assert operation["source_mode"] == 0o600
    assert operation["retained_mode"] == 0o400
    assert operation["before_image_ref"]["source_mode"] == 0o600
    assert operation["before_image_ref"]["retained_mode"] == 0o400
    assert operation["restore_mode"] == 0o600
    retained_path = fixture.output_dir / image["before_image_ref"]["path"]
    assert stat.S_IMODE(retained_path.stat().st_mode) == 0o400
    assert stat.S_IMODE(
        (fixture.output_dir / "SESSION_PRE_HANDOFF_SNAPSHOT.bin").stat().st_mode
    ) == 0o400
    validation_report = tmp_path / "mode-validation" / "report.json"
    validated = _validate(fixture, validation_report)
    assert validated.returncode == 0, validated.stderr


def test_prepare_rejects_live_source_mode_drift_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _make_fixture(tmp_path)
    before = _live_bytes(fixture)
    original_recheck = prepare_module._recheck_captured_sources

    def chmod_before_recheck(
        captures: dict[str, prepare_module.CapturedSource],
    ) -> None:
        fixture.session_state.chmod(0o600)
        original_recheck(captures)

    monkeypatch.setattr(
        prepare_module,
        "_recheck_captured_sources",
        chmod_before_recheck,
    )

    with pytest.raises(ValueError, match="live source drifted before publication"):
        prepare_module.prepare(_prepare_namespace(fixture))

    assert not fixture.output_dir.exists()
    assert _live_bytes(fixture) == before
    assert not list(tmp_path.glob(".prepared-bundle.stage-*"))


@pytest.mark.parametrize("competitor_kind", ["directory", "symlink"])
def test_prepare_publication_does_not_replace_a_competing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    competitor_kind: str,
) -> None:
    fixture = _make_fixture(tmp_path)
    before = _live_bytes(fixture)
    competing_target = tmp_path / "competing-target"
    if competitor_kind == "symlink":
        competing_target.mkdir()
    original_recheck = prepare_module._recheck_captured_sources
    competing_inode: int | None = None

    def publish_competitor_after_recheck(
        captures: dict[str, prepare_module.CapturedSource],
    ) -> None:
        nonlocal competing_inode
        original_recheck(captures)
        if competitor_kind == "directory":
            fixture.output_dir.mkdir()
        else:
            fixture.output_dir.symlink_to(
                competing_target,
                target_is_directory=True,
            )
        competing_inode = fixture.output_dir.lstat().st_ino

    monkeypatch.setattr(
        prepare_module,
        "_recheck_captured_sources",
        publish_competitor_after_recheck,
    )

    with pytest.raises(FileExistsError):
        prepare_module.prepare(_prepare_namespace(fixture))

    assert fixture.output_dir.lstat().st_ino == competing_inode
    if competitor_kind == "directory":
        assert fixture.output_dir.is_dir()
        assert list(fixture.output_dir.iterdir()) == []
    else:
        assert fixture.output_dir.is_symlink()
        assert fixture.output_dir.readlink() == competing_target
        assert list(competing_target.iterdir()) == []
    assert _live_bytes(fixture) == before
    assert not list(tmp_path.glob(".prepared-bundle.stage-*"))


def test_validator_rejects_a_tampered_worker_result(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    prepared = _prepare(fixture)
    assert prepared.returncode == 0, prepared.stderr
    before = _live_bytes(fixture)

    worker_report_path = (
        fixture.output_dir / "FRESH_WORKER_PREPARATION_REPORT.json"
    )
    worker_report = _load_json(worker_report_path)
    worker_report["workers"][1]["derived_action"] = "tampered"
    fixture.output_dir.chmod(0o755)
    worker_report_path.chmod(0o644)
    worker_report_path.write_bytes(_canonical_bytes(worker_report))
    worker_report_path.chmod(0o444)
    fixture.output_dir.chmod(0o555)

    validated = _validate(
        fixture, tmp_path / "tamper-output" / "tamper-validation.json"
    )

    assert validated.returncode != 0
    assert _live_bytes(fixture) == before


def test_prepare_rejects_a_tampered_spec_freeze_decision(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, valid_spec_freeze=False)
    before = _live_bytes(fixture)

    prepared = _prepare(fixture)

    assert prepared.returncode != 0
    assert _live_bytes(fixture) == before
    assert not fixture.output_dir.exists()


def test_prepare_rejects_a_changed_root_selector(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    selector_path = fixture.execution_root / "ACTIVE_STATUS.json"
    changed = _load_json(selector_path)
    changed["campaign_state"] = "CHANGED_AFTER_RC5"
    selector_path.write_bytes(_canonical_bytes(changed))
    before = _live_bytes(fixture)

    prepared = _prepare(fixture)

    assert prepared.returncode != 0
    assert _live_bytes(fixture) == before
    assert not fixture.output_dir.exists()


@pytest.mark.parametrize(
    "session_authority",
    [
        {
            "target_lease": {
                "lease_id": "forbidden-target-lease",
                "owner": "target-runner",
            }
        },
        {"target_execution_allowed": True},
        {"admitted": True, "active_packet": "AT0"},
    ],
)
def test_prepare_rejects_live_target_authority(
    tmp_path: Path, session_authority: dict[str, Any]
) -> None:
    fixture = _make_fixture(tmp_path, session_authority=session_authority)
    before = _live_bytes(fixture)

    prepared = _prepare(fixture)

    assert prepared.returncode != 0
    assert _live_bytes(fixture) == before
    assert not fixture.output_dir.exists()


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "MIGRATION_PREPARATION_REPORT.json",
            lambda value: value.update({"target_action": "submit"}),
        ),
        (
            "BEFORE_IMAGES.json",
            lambda value: value["images"][0].update({"native_revision_bound": True}),
        ),
        (
            "BEFORE_IMAGES.json",
            lambda value: value["images"][0]["before_image_ref"].update(
                {"undeclared": True}
            ),
        ),
        (
            "ROLLBACK_DESCRIPTORS.json",
            lambda value: value["operations"][0].update({"undeclared": True}),
        ),
        (
            "EVENT_APPEND_METADATA.json",
            lambda value: value.update({"after_head_sha256": "sha256:" + "0" * 64}),
        ),
        (
            "MIGRATION_PREPARATION_REPORT.json",
            lambda value: value["consumer_barrier_feasibility"].update(
                {"feasible": True}
            ),
        ),
    ],
)
def test_validator_rejects_closed_schema_or_authority_tampering(
    tmp_path: Path, name: str, mutate: Any
) -> None:
    fixture = _make_fixture(tmp_path)
    assert _prepare(fixture).returncode == 0
    before = _live_bytes(fixture)
    _rewrite_bundle_json(fixture, name, mutate)

    validated = _validate(
        fixture, tmp_path / "schema-output" / "validation.json"
    )

    assert validated.returncode != 0
    assert _live_bytes(fixture) == before


def test_validator_rejects_tampered_retained_before_image_bytes(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    assert _prepare(fixture).returncode == 0
    path = fixture.output_dir / "SESSION_PRE_HANDOFF_SNAPSHOT.bin"
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b" ")
    path.chmod(0o444)
    fixture.output_dir.chmod(0o555)

    validated = _validate(
        fixture, tmp_path / "before-image-output" / "validation.json"
    )

    assert validated.returncode != 0


def test_validator_rejects_retained_before_image_mode_drift(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    fixture.session_state.chmod(0o600)
    assert _prepare(fixture).returncode == 0
    path = fixture.output_dir / "SESSION_PRE_HANDOFF_SNAPSHOT.bin"
    path.chmod(0o444)

    validated = _validate(
        fixture, tmp_path / "before-image-mode-output" / "validation.json"
    )

    assert validated.returncode != 0


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_validator_rejects_linked_bundle_artifacts(
    tmp_path: Path, alias_kind: str
) -> None:
    fixture = _make_fixture(tmp_path)
    assert _prepare(fixture).returncode == 0
    path = fixture.output_dir / "PREPARED_RUN_QUEUE.json"
    external = tmp_path / "external-run-queue.json"
    external.write_bytes(path.read_bytes())
    external.chmod(0o444)
    fixture.output_dir.chmod(0o755)
    path.unlink()
    if alias_kind == "symlink":
        path.symlink_to(external)
    else:
        path.hardlink_to(external)
    fixture.output_dir.chmod(0o555)

    validated = _validate(
        fixture, tmp_path / f"{alias_kind}-output" / "validation.json"
    )

    assert validated.returncode != 0


def test_validator_rejects_live_source_drift_from_retained_snapshot(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    assert _prepare(fixture).returncode == 0
    session = _load_json(fixture.session_state)
    session["queue"] = []
    fixture.session_state.write_bytes(_canonical_bytes(session))

    validated = _validate(
        fixture, tmp_path / "source-drift-output" / "validation.json"
    )

    assert validated.returncode != 0


@pytest.mark.parametrize(
    "protected_report",
    [
        "active_selector",
        "frozen_manifest",
        "beads_export",
        "session_state",
        "bundle_report",
    ],
)
def test_validator_cannot_overwrite_protected_or_existing_inputs(
    tmp_path: Path, protected_report: str
) -> None:
    fixture = _make_fixture(tmp_path)
    assert _prepare(fixture).returncode == 0
    paths = {
        "active_selector": fixture.execution_root / "ACTIVE_STATUS.json",
        "frozen_manifest": fixture.revision / "ARTIFACT_MANIFEST.json",
        "beads_export": fixture.beads_export,
        "session_state": fixture.session_state,
        "bundle_report": fixture.output_dir / "MIGRATION_PREPARATION_REPORT.json",
    }
    before = _live_bytes(fixture)

    validated = _validate(fixture, paths[protected_report])

    assert validated.returncode != 0
    assert _live_bytes(fixture) == before


def test_replay_cannot_write_inside_bundle_or_claim_frozen_pass(
    tmp_path: Path,
) -> None:
    fixture = _make_fixture(tmp_path)
    assert _prepare(fixture).returncode == 0
    report = _load_json(
        fixture.output_dir / "FRESH_WORKER_PREPARATION_REPORT.json"
    )
    assert report["frozen_contract_passed"] is False
    assert (
        report["frozen_contract_receipt"]["result"]
        == "non_conformance_preparation_replay"
    )

    replayed = subprocess.run(
        [
            sys.executable,
            str(REPLAY_SCRIPT),
            "--revision",
            str(fixture.revision),
            "--bundle",
            str(fixture.output_dir),
            "--output-root",
            str(fixture.output_dir),
            "--report",
            str(fixture.output_dir / "MIGRATION_PREPARATION_REPORT.json"),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert replayed.returncode != 0
