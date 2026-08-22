from __future__ import annotations

import copy
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

from breadboard.rl.phase5.migration_projections import (
    build_root_selector,
    derive_active_status,
    derive_beads_projection,
    derive_run_queue,
    derive_session_projection,
    validate_beads_projection,
    validate_zero_authority,
)


MIGRATION_ID = "phase5-v2-test-migration"
SPEC_FREEZE_SHA256 = "sha256:" + "a" * 64
PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
REVISION_ID = "v2.0.0-rc5-20260717"
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
FIXTURE_ARCHIVE = (
    Path(__file__).resolve().parent / "fixtures" / "phase5_v2_rc5_inputs.zip"
)
FIXTURE_ARCHIVE_SHA256 = (
    "sha256:9e69cf1fb66fd5094d036e8027e3a83b38b70d272f42922c6bd290d131d0ff07"
)
V1_ACTIVE_STATUS_SHA256 = (
    "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
)
FROZEN_REVISION: Path | None = None


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _extract_repo_fixture(destination: Path) -> Path:
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
    return destination / "revision"


@pytest.fixture(scope="module", autouse=True)
def _portable_frozen_revision(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    global FROZEN_REVISION
    FROZEN_REVISION = _extract_repo_fixture(
        tmp_path_factory.mktemp("phase5-v2-rc5-fixture") / "inputs"
    )


def _load_frozen(name: str) -> dict[str, Any]:
    assert FROZEN_REVISION is not None
    return json.loads((FROZEN_REVISION / name).read_text(encoding="utf-8"))


def _frozen_queue() -> dict[str, Any]:
    return _load_frozen("RUN_QUEUE.json")


def _draft_status() -> dict[str, Any]:
    return _load_frozen("DRAFT_STATUS.json")


def _beads_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    migration = _load_frozen("BEADS_MIGRATION.json")
    live_parent = {
        "_type": "issue",
        "close_reason": None,
        "dependencies": [],
        "id": "bb-auh",
        "issue_type": "epic",
        "status": "open",
        "title": "Legacy Phase 5 parent",
    }
    return migration, [*copy.deepcopy(migration["legacy_snapshot"]), live_parent]


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256((encoded + "\n").encode()).hexdigest()


def _assert_zero_authority(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"target_lease", "active_attempt", "completion_decision"}:
                assert item is None
            elif key in {"current_verified_points", "evidence_ref_count", "review_ref_count"}:
                assert item == 0
            elif key in {"awarded_items", "approval_refs", "review_refs"}:
                assert item == []
            elif key in {"admitted", "target_execution_allowed", "internal_completion"}:
                assert item is False
            elif key == "authorized":
                assert item is False
            _assert_zero_authority(item)
    elif isinstance(value, list):
        for item in value:
            _assert_zero_authority(item)


def test_post_cutover_projection_exposes_only_at0_without_target_authority() -> None:
    run_queue = derive_run_queue(
        _frozen_queue(),
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )
    active_status = derive_active_status(
        _draft_status(),
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )

    assert run_queue["eligible"] == [
        {
            "kind": "local",
            "packet_key": "AT0",
            "reason": "SPEC_FREEZE and migration cutover complete",
        }
    ]
    assert all(item["packet_key"] != "AT0" for item in run_queue["blocked"])
    assert run_queue["generation"] == 1
    assert run_queue["state"] == "READY_FOR_LOCAL_MIGRATION_WORK"
    assert run_queue["target_lease"] is None
    assert run_queue["waiting_human"] == []
    assert run_queue["migration_id"] == MIGRATION_ID
    assert run_queue["spec_freeze_sha256"] == SPEC_FREEZE_SHA256
    assert active_status["migration_id"] == MIGRATION_ID
    assert active_status["spec_freeze_sha256"] == SPEC_FREEZE_SHA256
    assert active_status["target_lease"] is None
    assert active_status["tracks"]["assurance"]["current_verified_points"] == 0
    assert active_status["tracks"]["assurance"]["awarded_items"] == []
    assert active_status["promotion"]["authorized"] is False
    assert active_status["internal_completion"] is False
    validate_zero_authority(run_queue, active_status)
    _assert_zero_authority([run_queue, active_status])


def test_beads_projection_orders_67_children_before_parent_supersession() -> None:
    beads_migration, live_rows = _beads_inputs()
    beads_before = copy.deepcopy(beads_migration)
    live_before = copy.deepcopy(live_rows)

    projection = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )
    repeated = derive_beads_projection(
        copy.deepcopy(beads_migration),
        copy.deepcopy(live_rows),
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )

    assert beads_migration == beads_before
    assert live_rows == live_before
    assert projection == repeated
    assert projection["migration_id"] == MIGRATION_ID
    assert projection["spec_freeze_sha256"] == SPEC_FREEZE_SHA256
    assert set(projection) == {
        "legacy_parent_resolution",
        "legacy_resolutions",
        "migration_id",
        "program_id",
        "schema_version",
        "source_snapshot_sha256",
        "spec_freeze_sha256",
        "successor_epic",
        "successor_issues",
    }
    child_resolutions = projection["legacy_resolutions"]
    assert len(child_resolutions) == 67
    assert [item["legacy_issue_id"] for item in child_resolutions] == [
        f"bb-auh.{number}" for number in range(1, 68)
    ]
    live_parent = live_rows[-1]
    assert projection["legacy_parent_resolution"] == {
        "after_child_resolution_count": len(child_resolutions),
        "before_record_sha256": _canonical_sha256(live_parent),
        "before_status": "open",
        "close_reason": "SUPERSEDED BY V2 — NOT COMPLETED",
        "disposition": "superseded_by_v2_not_completed",
        "issue_id": "bb-auh",
        "projected_status": "closed",
    }
    resolutions = {
        item["legacy_issue_id"]: item
        for item in child_resolutions
    }
    assert len(resolutions) == 67
    closed_id = next(
        row["id"]
        for row in live_rows
        if row["id"] in resolutions and row["status"] == "closed"
    )
    assert resolutions[closed_id]["before_status"] == "closed"
    assert resolutions[closed_id]["projected_status"] == "closed"
    assert resolutions[closed_id]["disposition"] == "preserve_closed"
    unclosed_ids = [
        row["id"]
        for row in live_rows
        if row["id"] in resolutions and row["status"] in {"open", "in_progress"}
    ]
    assert unclosed_ids
    assert {resolutions[issue_id]["before_status"] for issue_id in unclosed_ids} >= {
        "open",
        "in_progress",
    }
    for issue_id in unclosed_ids:
        assert resolutions[issue_id]["projected_status"] == "closed"
        assert resolutions[issue_id]["disposition"] == (
            "SUPERSEDED BY V2 — NOT COMPLETED"
        )
    validate_beads_projection(projection)
    validate_zero_authority(projection)


def test_beads_projection_preserves_an_already_closed_parent_as_a_no_op() -> None:
    beads_migration, live_rows = _beads_inputs()
    live_parent = live_rows[-1]
    live_parent["status"] = "closed"
    live_parent["close_reason"] = "existing exact parent close reason"

    projection = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )

    assert projection["legacy_parent_resolution"] == {
        "after_child_resolution_count": 67,
        "before_record_sha256": _canonical_sha256(live_parent),
        "before_status": "closed",
        "close_reason": "existing exact parent close reason",
        "disposition": "preserve_closed",
        "issue_id": "bb-auh",
        "projected_status": "closed",
    }
    validate_beads_projection(projection)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda projection: projection.pop("legacy_parent_resolution"),
        lambda projection: projection["legacy_resolutions"].insert(
            0, projection.pop("legacy_parent_resolution")
        ),
        lambda projection: projection["legacy_resolutions"].append(
            copy.deepcopy(projection["legacy_parent_resolution"])
        ),
    ],
    ids=[
        "omitted-parent-operation",
        "parent-operation-before-children",
        "extra-parent-operation",
    ],
)
def test_beads_projection_schema_rejects_missing_reordered_or_extra_parent_operation(
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    beads_migration, live_rows = _beads_inputs()
    projection = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )
    mutate(projection)

    with pytest.raises(ValueError):
        validate_beads_projection(projection)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("after_child_resolution_count", 66),
        ("before_record_sha256", "sha256:not-a-digest"),
        ("before_status", "superseded"),
        ("close_reason", "alternate reason"),
        ("disposition", "alternate_disposition"),
        ("issue_id", "bb-auh.1"),
        ("projected_status", "superseded"),
    ],
)
def test_beads_projection_schema_rejects_alternate_parent_operation(
    field: str, value: Any
) -> None:
    beads_migration, live_rows = _beads_inputs()
    projection = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )
    projection["legacy_parent_resolution"][field] = value

    with pytest.raises(ValueError):
        validate_beads_projection(projection)


def test_beads_projection_preserves_comment_history_outside_migration_fields() -> None:
    beads_migration, live_rows = _beads_inputs()
    row = next(item for item in live_rows if item.get("id") == "bb-auh.34")
    row["comments"] = [
        {
            "author": "Kyle McCleary",
            "id": "comment-1",
            "issue_id": "bb-auh.34",
            "text": "Observation-only history retained by the native Beads store.",
        }
    ]
    row["comment_count"] = 1
    before = copy.deepcopy(live_rows)

    projection = derive_beads_projection(
        beads_migration,
        live_rows,
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )

    assert projection["legacy_resolutions"]
    assert live_rows == before


def test_beads_projection_still_rejects_migration_field_drift() -> None:
    beads_migration, live_rows = _beads_inputs()
    row = next(item for item in live_rows if item.get("id") == "bb-auh.34")
    row["title"] = "mutated title"

    with pytest.raises(ValueError, match="frozen migration fields"):
        derive_beads_projection(
            beads_migration,
            live_rows,
            migration_id=MIGRATION_ID,
            spec_freeze_sha256=SPEC_FREEZE_SHA256,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rules: rules.pop(4),
        lambda rules: rules.__setitem__(
            slice(3, 5), [rules[4], rules[3]]
        ),
        lambda rules: rules.insert(5, rules[4]),
    ],
    ids=[
        "omitted-parent-rule",
        "reordered-parent-rule",
        "extra-parent-rule",
    ],
)
def test_beads_projection_rejects_changed_parent_cutover_operation(
    mutate: Callable[[list[str]], Any],
) -> None:
    beads_migration, live_rows = _beads_inputs()
    mutate(beads_migration["cutover_rules"])

    with pytest.raises(ValueError, match="cutover_rules"):
        derive_beads_projection(
            beads_migration,
            live_rows,
            migration_id=MIGRATION_ID,
            spec_freeze_sha256=SPEC_FREEZE_SHA256,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda migration, rows: migration["mappings"].pop(),
        lambda migration, rows: migration["mappings"].append(
            copy.deepcopy(migration["mappings"][0])
        ),
        lambda migration, rows: rows.pop(0),
        lambda migration, rows: rows.pop(),
        lambda migration, rows: migration["mappings"][0].update(
            {"legacy_issue_id": "not-a-legacy-child"}
        ),
        lambda migration, rows: migration["mappings"][0].update(
            {"successor_packet_keys": []}
        ),
    ],
    ids=[
        "missing-mapping",
        "duplicate-mapping",
        "missing-live-row",
        "missing-parent-row",
        "malformed-legacy-id",
        "missing-successor",
    ],
)
def test_beads_projection_rejects_missing_or_malformed_mappings(
    mutate: Callable[[dict[str, Any], list[dict[str, Any]]], None],
) -> None:
    beads_migration, live_rows = _beads_inputs()
    mutate(beads_migration, live_rows)

    with pytest.raises(ValueError):
        derive_beads_projection(
            beads_migration,
            live_rows,
            migration_id=MIGRATION_ID,
            spec_freeze_sha256=SPEC_FREEZE_SHA256,
        )


def _ref(path: str, digit: str, size: int) -> dict[str, Any]:
    return {"path": path, "sha256": "sha256:" + digit * 64, "size": size}


def test_root_selector_and_session_projection_are_deterministic() -> None:
    revision_prefix = f"versions/v2-two-track/{REVISION_ID}"
    migration_prefix = f"migrations/{MIGRATION_ID}"
    refs = {
        "artifact_manifest_ref": _ref(
            f"{revision_prefix}/ARTIFACT_MANIFEST.json", "1", 101
        ),
        "active_status_ref": _ref(
            f"{migration_prefix}/PREPARED_ACTIVE_STATUS.json", "2", 102
        ),
        "evidence_index_ref": _ref(
            f"{revision_prefix}/EVIDENCE_INDEX.json", "3", 103
        ),
        "authority_policy_ref": _ref(
            f"{revision_prefix}/AUTHORITY_POLICY.json", "4", 104
        ),
        "run_queue_ref": _ref(
            f"{migration_prefix}/PREPARED_RUN_QUEUE.json", "5", 105
        ),
    }
    refs["artifact_manifest_ref"]["sha256"] = ARTIFACT_MANIFEST_SHA256
    arguments = {
        "revision_id": REVISION_ID,
        "program_id": PROGRAM_ID,
        "generation": 1,
        "event_cursor": 2,
        "migration_id": MIGRATION_ID,
        **refs,
    }

    first = build_root_selector(**arguments)
    second = build_root_selector(**copy.deepcopy(arguments))

    assert first == second
    assert first["artifacts"] == {
        "artifact_manifest": refs["artifact_manifest_ref"],
        "active_status": refs["active_status_ref"],
        "evidence_index": refs["evidence_index_ref"],
        "authority_policy": refs["authority_policy_ref"],
        "run_queue": refs["run_queue_ref"],
    }
    assert first["migration_id"] == MIGRATION_ID
    validate_zero_authority(first)

    active_status = derive_active_status(
        _draft_status(),
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )
    run_queue = derive_run_queue(
        _frozen_queue(),
        migration_id=MIGRATION_ID,
        spec_freeze_sha256=SPEC_FREEZE_SHA256,
    )
    session_state = {
        "queue": [{"issue_id": "bb-auh.10", "state": "legacy_open"}],
        "schema_version": "bb.session.phase5.v1",
        "target_lease": None,
        "todos": [{"issue_id": "bb-auh.10", "state": "in_progress"}],
    }
    session_first = derive_session_projection(
        session_state,
        active_status,
        run_queue,
        migration_id=MIGRATION_ID,
    )
    session_second = derive_session_projection(
        copy.deepcopy(session_state),
        copy.deepcopy(active_status),
        copy.deepcopy(run_queue),
        migration_id=MIGRATION_ID,
    )
    assert session_first == session_second
    assert session_first["migration_id"] == MIGRATION_ID
    assert set(session_first) == {
        "active_packet",
        "migration_id",
        "program_id",
        "queue_sha256",
        "revision_id",
        "schema_version",
        "state",
        "status_sha256",
        "target_lease",
        "todos",
    }
    assert session_first["target_lease"] is None
    assert session_first["todos"][0]["packet_key"] == "AT0"
    validate_zero_authority(session_first)
    _assert_zero_authority(session_first)


@pytest.mark.parametrize(
    ("reference_key", "bad_path"),
    [
        ("artifact_manifest_ref", "/tmp/ARTIFACT_MANIFEST.json"),
        ("artifact_manifest_ref", "../ARTIFACT_MANIFEST.json"),
        (
            "artifact_manifest_ref",
            "versions/v2-two-track/wrong-revision/ARTIFACT_MANIFEST.json",
        ),
        (
            "active_status_ref",
            "migrations/wrong-migration/PREPARED_ACTIVE_STATUS.json",
        ),
        ("active_status_ref", "PREPARED_ACTIVE_STATUS.json"),
        ("run_queue_ref", "PREPARED_RUN_QUEUE.json"),
    ],
)
def test_root_selector_rejects_noncanonical_or_unbound_reference_paths(
    reference_key: str, bad_path: str
) -> None:
    revision_prefix = f"versions/v2-two-track/{REVISION_ID}"
    migration_prefix = f"migrations/{MIGRATION_ID}"
    refs = {
        "artifact_manifest_ref": _ref(
            f"{revision_prefix}/ARTIFACT_MANIFEST.json", "1", 101
        ),
        "active_status_ref": _ref(
            f"{migration_prefix}/PREPARED_ACTIVE_STATUS.json", "2", 102
        ),
        "evidence_index_ref": _ref(
            f"{revision_prefix}/EVIDENCE_INDEX.json", "3", 103
        ),
        "authority_policy_ref": _ref(
            f"{revision_prefix}/AUTHORITY_POLICY.json", "4", 104
        ),
        "run_queue_ref": _ref(
            f"{migration_prefix}/PREPARED_RUN_QUEUE.json", "5", 105
        ),
    }
    refs["artifact_manifest_ref"]["sha256"] = ARTIFACT_MANIFEST_SHA256
    refs[reference_key]["path"] = bad_path

    with pytest.raises(ValueError):
        build_root_selector(
            revision_id=REVISION_ID,
            program_id=PROGRAM_ID,
            generation=1,
            event_cursor=2,
            migration_id=MIGRATION_ID,
            **refs,
        )




def test_zero_authority_rejects_score_lease_admission_completion_and_promotion() -> None:
    forbidden_documents = [
        {"tracks": {"assurance": {"current_verified_points": 1}}},
        {"tracks": {"assurance": {"awarded_items": ["A1"]}}},
        {"target_lease": {"lease_id": "target-lease"}},
        {"admitted": True},
        {"target_execution_allowed": True},
        {"internal_completion": True},
        {"promotion": {"authorized": True}},
    ]
    for document in forbidden_documents:
        with pytest.raises(ValueError):
            validate_zero_authority(document)


def test_derivation_rejects_preexisting_score_lease_or_admission() -> None:
    queue = _frozen_queue()
    queue["target_lease"] = {"lease_id": "existing-target-lease"}
    with pytest.raises(ValueError):
        derive_run_queue(
            queue,
            migration_id=MIGRATION_ID,
            spec_freeze_sha256=SPEC_FREEZE_SHA256,
        )

    status = _draft_status()
    status["tracks"]["assurance"]["current_verified_points"] = 1
    with pytest.raises(ValueError):
        derive_active_status(
            status,
            migration_id=MIGRATION_ID,
            spec_freeze_sha256=SPEC_FREEZE_SHA256,
        )

    status = _draft_status()
    status["shared_transport"]["admitted_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValueError):
        derive_active_status(
            status,
            migration_id=MIGRATION_ID,
            spec_freeze_sha256=SPEC_FREEZE_SHA256,
        )
