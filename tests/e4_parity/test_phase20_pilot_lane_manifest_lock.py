from __future__ import annotations

import copy
import hashlib
import json
import stat
import struct
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from scripts.authoring import validate_lane
from scripts.e4_parity import compile_lane_lock
from scripts.e4_parity.lane_definitions import load_lane_def, load_manifest_lane_def
from scripts.e4_parity.tree_digest import digest_directory


ROOT = Path(__file__).resolve().parents[2]
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
LANE_DIR = ROOT / "config" / "e4_lanes"
MANIFEST_PATH = LANE_DIR / f"{LANE_ID}.manifest.yaml"
LEGACY_PATH = LANE_DIR / f"{LANE_ID}.yaml"
LOCK_PATH = LANE_DIR / f"{LANE_ID}.lock.json"
SIDECAR_PATH = LANE_DIR / f"{LANE_ID}.packet_constants.v1.json"
MANIFEST_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.lane_manifest.v1.schema.json"
REGISTRY_PATH = ROOT / "contracts" / "kernel" / "registries" / "e4_adapters.v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_bytes(value: object, *, newline: bool = True) -> bytes:
    suffix = "\n" if newline else ""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + suffix).encode(
        "utf-8"
    )


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    if path.is_dir():
        return digest_directory(path).digest
    return _sha256_bytes(path.read_bytes())

def _resolve_reference(reference: str) -> Path:
    candidate = ROOT / reference
    if candidate.exists():
        return candidate
    return next(
        (ancestor / reference for ancestor in ROOT.parents if (ancestor / reference).exists()),
        candidate,
    )


def _set_nested(payload: dict[str, Any], pointer: str, value: object) -> None:
    parts = pointer.strip("/").split("/")
    target: dict[str, Any] = payload
    for part in parts[:-1]:
        nested = target[part]
        assert isinstance(nested, dict)
        target = nested
    target[parts[-1]] = value


@pytest.mark.parametrize(
    ("pointer", "invalid_value"),
    [
        pytest.param("/normalize/record_builders", {}, id="record-builders-must-be-array"),
        pytest.param("/normalize/projection_constants", [], id="projection-constants-must-be-object"),
        pytest.param("/normalize/required_records", [1], id="required-records-must-contain-strings"),
        pytest.param("/normalize/required_roles", "capture_ref", id="required-roles-must-be-array"),
        pytest.param("/normalize/record_roles", {"work_item": 1}, id="record-roles-must-map-to-strings"),
        pytest.param(
            "/normalize/record_envelopes",
            {"work_item": "bb.work_item.v1"},
            id="record-envelopes-must-map-to-objects",
        ),
        pytest.param("/normalize/role_aliases", {"comparison": ["comparator_ref"]}, id="role-aliases-must-map-to-strings"),
        pytest.param("/normalize/auto_bind_role_refs", "true", id="auto-bind-role-refs-must-be-boolean"),
        pytest.param(
            "/normalize/scope_observation_labels",
            ["target_probe", 7],
            id="scope-observation-labels-must-contain-strings",
        ),
    ],
)
def test_am8_author_intent_extensions_accept_the_pilot_and_reject_wrong_shapes(
    pointer: str,
    invalid_value: object,
) -> None:
    """AM8 normalization intent is accepted only at its declared collection/value shapes."""
    schema = _load_json(MANIFEST_SCHEMA_PATH)
    validator = Draft202012Validator(schema)
    manifest = _load_yaml(MANIFEST_PATH)

    assert list(validator.iter_errors(manifest)) == []

    invalid = copy.deepcopy(manifest)
    _set_nested(invalid, pointer, invalid_value)
    errors = list(validator.iter_errors(invalid))
    expected_path = pointer.strip("/").split("/")
    assert any(list(error.absolute_path)[: len(expected_path)] == expected_path for error in errors), errors


def test_pilot_manifest_is_canonical_author_owned_intent_within_the_pilot_budget() -> None:
    """The pilot remains digest-free, block-style, line-bounded authoring input of at most 300 canonical lines."""
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    canonical_lines = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]

    assert "sha256:" not in text
    assert len(canonical_lines) <= 300
    assert all(len(line) <= 120 for line in text.splitlines())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        loaded = validate_lane.load_lane_manifest(MANIFEST_PATH)
    assert loaded == _load_yaml(MANIFEST_PATH)


def test_pilot_manifest_runtime_uses_the_derived_tree_and_parity_load_matches_legacy() -> None:
    """Runtime consumes the derived extraction, while explicit parity mode alone substitutes the legacy tree."""
    derived_ref = compile_lane_lock.SOURCE_FREEZE_EXTRACTION_REF
    legacy_ref = "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest"

    runtime_lane = load_manifest_lane_def(MANIFEST_PATH)
    assert derived_ref in runtime_lane["capture"]["inputs"]
    assert legacy_ref not in runtime_lane["capture"]["inputs"]
    assert load_manifest_lane_def(MANIFEST_PATH, parity_legacy=True) == load_lane_def(LEGACY_PATH)


def test_pilot_lock_pins_every_dependency_and_keeps_the_three_layers_directional() -> None:
    """The lock pins manifest, sidecar, inputs, registry entries, and freeze row without generated or volatile state."""
    manifest = _load_yaml(MANIFEST_PATH)
    lock = _load_json(LOCK_PATH)
    sidecar = _load_json(SIDECAR_PATH)

    assert LOCK_PATH.read_bytes() == _canonical_bytes(lock)
    assert SIDECAR_PATH.read_bytes() == _canonical_bytes(sidecar)
    assert set(lock) == {
        "artifact_roles",
        "lane_id",
        "lock_format",
        "manifest_ref",
        "manifest_sha256",
        "packet_constants_ref",
        "registry_pins",
        "resolved_inputs",
        "schema_version",
        "target_freeze",
    }
    assert set(sidecar) == {"payload_templates", "substitutions"}
    assert lock["lane_id"] == manifest["lane_id"]
    assert lock["manifest_ref"] == MANIFEST_PATH.relative_to(ROOT).as_posix()
    assert lock["manifest_sha256"] == _sha256_path(MANIFEST_PATH)
    assert lock["packet_constants_ref"] == {
        "path": SIDECAR_PATH.relative_to(ROOT).as_posix(),
        "sha256": _sha256_path(SIDECAR_PATH),
    }

    resolved_by_path = {row["path"]: row for row in lock["resolved_inputs"]}
    expected_paths = set(manifest["capture"]["inputs"])
    expected_paths.update(
        {
            LEGACY_PATH.relative_to(ROOT).as_posix(),
            manifest["target"]["source_freeze_ref"],
            compile_lane_lock.SOURCE_FREEZE_EXTRACTION_REF,
        }
    )
    assert set(resolved_by_path) == expected_paths
    archive_ref = compile_lane_lock.SOURCE_FREEZE_ARCHIVE_REF
    extraction_ref = compile_lane_lock.SOURCE_FREEZE_EXTRACTION_REF
    assert archive_ref in manifest["capture"]["inputs"]
    assert extraction_ref not in manifest["capture"]["inputs"]
    assert resolved_by_path[archive_ref]["role"] == "source_freeze_archive"
    assert resolved_by_path[extraction_ref]["role"] == "source_freeze_extraction"
    for reference, row in resolved_by_path.items():
        source = _resolve_reference(reference)
        assert row["sha256"] == _sha256_path(source), reference
        assert row["bytes"] == (digest_directory(source).bytes if source.is_dir() else len(source.read_bytes()))

    registry = _load_json(REGISTRY_PATH)
    entries_by_id = {entry["id"]: entry for entry in registry["entries"]}
    referenced_entries = {
        manifest["capture"]["adapter"],
        manifest["normalize"]["translator"],
        manifest["compare"]["comparator"],
    }
    assert {pin["entry_id"] for pin in lock["registry_pins"]} == referenced_entries
    for pin in lock["registry_pins"]:
        assert pin == {
            "registry": "e4_adapters",
            "entry_id": pin["entry_id"],
            "entry_sha256": _sha256_bytes(_canonical_bytes(entries_by_id[pin["entry_id"]], newline=False)),
        }

    freeze = _load_yaml(ROOT / manifest["target"]["source_freeze_ref"])
    freeze_row = freeze["e4_configs"][manifest["config_id"]]
    freeze_preimage = _canonical_bytes(
        {"row_id": manifest["config_id"], "row": freeze_row}, newline=False
    )
    assert lock["target_freeze"] == {
        "config_id": manifest["config_id"],
        "freeze_manifest_row_sha256": _sha256_bytes(freeze_preimage),
    }

    forbidden_volatile_keys = {
        "generated_at_utc",
        "updated_at_utc",
        "duration",
        "duration_ms",
        "run_id",
        "stage_results",
        "environment",
    }

    def collect_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(collect_keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(collect_keys(item) for item in value))
        return set()

    assert collect_keys(lock).isdisjoint(forbidden_volatile_keys)
    assert not ({"payload_templates", "substitutions"} & collect_keys(lock))
    assert not ({"manifest_sha256", "resolved_inputs", "registry_pins", "target_freeze"} & collect_keys(manifest))


def test_pilot_migration_is_byte_deterministic_and_committed_outputs_pass_check(tmp_path: Path) -> None:
    """Repeated migration emits identical bytes, and check mode accepts the committed generated pair without writes."""
    lock_path = tmp_path / f"{LANE_ID}.lock.json"
    sidecar_path = tmp_path / f"{LANE_ID}.packet_constants.v1.json"
    argv = [
        "migrate",
        str(MANIFEST_PATH),
        "--legacy",
        str(LEGACY_PATH),
        "--lock",
        str(lock_path),
        "--sidecar",
        str(sidecar_path),
    ]

    assert compile_lane_lock.main(argv) == 0
    first = (lock_path.read_bytes(), sidecar_path.read_bytes())
    assert compile_lane_lock.main(argv) == 0
    assert (lock_path.read_bytes(), sidecar_path.read_bytes()) == first

    committed_before = (LOCK_PATH.read_bytes(), SIDECAR_PATH.read_bytes())
    assert (
        compile_lane_lock.main(
            ["migrate", str(MANIFEST_PATH), "--legacy", str(LEGACY_PATH), "--check"]
        )
        == 0
    )
    assert (LOCK_PATH.read_bytes(), SIDECAR_PATH.read_bytes()) == committed_before


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _synthetic_archive_migration(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], Path, Path, Path]:
    archive_ref = compile_lane_lock.SOURCE_FREEZE_ARCHIVE_REF
    extraction_ref = compile_lane_lock.SOURCE_FREEZE_EXTRACTION_REF
    archive_path = root / archive_ref
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.json", b'{"name":"fixture"}\n')
        archive.writestr("src/index.ts", b"export const fixture = true;\n")

    registry_entries = [
        {
            "description": f"fixture {kind}",
            "id": entry_id,
            "metadata": {"impl": f"fixture:{kind}", "kind": kind},
            "status": "active",
        }
        for entry_id, kind in (
            ("fixture_capture", "capture_adapter"),
            ("fixture_translate", "translator"),
            ("fixture_compare", "comparator"),
        )
    ]
    _write_json(
        root / "contracts/kernel/registries/e4_adapters.v1.json",
        {
            "schema_version": "bb.registry.v1",
            "registry_id": "e4_adapters",
            "entries": registry_entries,
        },
    )
    _write_yaml(
        root / "freeze.yaml",
        {"e4_configs": {"fixture.config": {"family": "fixture", "version": "1.0"}}},
    )
    manifest = {
        "schema_version": "bb.e4.lane_manifest.v1",
        "lane_id": "fixture_lane",
        "config_id": "fixture.config",
        "target": {
            "family": "fixture",
            "version": "1.0",
            "source_freeze_ref": "freeze.yaml",
        },
        "kind": "target_support",
        "capture": {
            "strategy": "adapter",
            "adapter": "fixture_capture",
            "inputs": [archive_ref],
        },
        "normalize": {
            "mode": "translate",
            "translator": "fixture_translate",
            "projection_constants": {},
        },
        "replay": {"mode": "stored", "comparator_class": "semantic"},
        "compare": {"comparator": "fixture_compare"},
        "claim": {"scope": {"behaviors": ["fixture behavior"]}, "exclusions": []},
        "artifacts_root": "artifacts/fixture_lane",
    }
    manifest_path = root / "fixture_lane.manifest.yaml"
    legacy_path = root / "fixture_lane.yaml"
    lock_path = root / "fixture_lane.lock.json"
    sidecar_path = root / "fixture_lane.packet_constants.v1.json"
    _write_yaml(manifest_path, manifest)
    _write_yaml(
        legacy_path,
        {
            "normalize": {
                "config": {
                    "roles": {},
                    "packet_constants": {"payload_templates": {}, "substitutions": {}},
                }
            }
        },
    )
    monkeypatch.setattr(compile_lane_lock, "ROOT", root)
    argv = [
        "migrate",
        str(manifest_path),
        "--legacy",
        str(legacy_path),
        "--lock",
        str(lock_path),
        "--sidecar",
        str(sidecar_path),
    ]
    return argv, root / extraction_ref, lock_path, sidecar_path


def test_clean_state_migration_and_check_materialize_the_archive_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both write and check modes recreate a missing safe extraction and keep generated outputs valid."""
    argv, extraction_path, lock_path, sidecar_path = _synthetic_archive_migration(
        tmp_path,
        monkeypatch,
    )

    assert not extraction_path.exists()
    assert compile_lane_lock.main(argv) == 0
    assert (extraction_path / "package.json").read_bytes() == b'{"name":"fixture"}\n'
    assert (extraction_path / "src/index.ts").read_bytes() == b"export const fixture = true;\n"
    generated = (lock_path.read_bytes(), sidecar_path.read_bytes())
    resolved_inputs = {
        row["path"]: row for row in json.loads(generated[0])["resolved_inputs"]
    }
    assert (
        resolved_inputs[compile_lane_lock.SOURCE_FREEZE_ARCHIVE_REF]["role"]
        == "source_freeze_archive"
    )
    assert (
        resolved_inputs[compile_lane_lock.SOURCE_FREEZE_EXTRACTION_REF]["role"]
        == "source_freeze_extraction"
    )

    for path in sorted(extraction_path.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    extraction_path.rmdir()
    assert compile_lane_lock.main([*argv, "--check"]) == 0
    assert (extraction_path / "package.json").read_bytes() == b'{"name":"fixture"}\n'
    assert (lock_path.read_bytes(), sidecar_path.read_bytes()) == generated


@pytest.mark.parametrize(
    ("member_name", "mode"),
    [
        pytest.param("/absolute.txt", None, id="absolute-path"),
        pytest.param("../outside.txt", None, id="parent-traversal"),
        pytest.param("dir/../../outside.txt", None, id="nested-parent-traversal"),
        pytest.param("link", stat.S_IFLNK | 0o777, id="symlink"),
        pytest.param("fifo", stat.S_IFIFO | 0o644, id="special-file"),
    ],
)
def test_safe_archive_extraction_rejects_escaping_and_non_regular_members(
    tmp_path: Path,
    member_name: str,
    mode: int | None,
) -> None:
    """Archive extraction rejects paths outside its root and Unix metadata that is not a regular file/directory."""
    archive_path = tmp_path / "hostile.zip"
    info = zipfile.ZipInfo(member_name)
    if mode is not None:
        info.create_system = 3
        info.external_attr = mode << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, b"../outside-target" if stat.S_ISLNK(mode or 0) else b"payload")
    extraction_path = tmp_path / "extracted"

    with pytest.raises(compile_lane_lock.ReferenceError):
        compile_lane_lock.extract_source_archive(archive_path, extraction_path)

    assert not (tmp_path / "outside.txt").exists()
    assert not extraction_path.exists() or not any(extraction_path.rglob("*"))


def test_safe_archive_extraction_rejects_an_invalid_utf8_member_name(tmp_path: Path) -> None:
    """Malformed UTF-8 in a ZIP member name is surfaced as a safe extraction error, never a decoder crash."""
    archive_path = tmp_path / "invalid-name.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("bad.txt", b"payload")
    raw = bytearray(archive_path.read_bytes())
    local = raw.index(b"PK\x03\x04")
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, local + 6, struct.unpack_from("<H", raw, local + 6)[0] | 0x800)
    struct.pack_into("<H", raw, central + 8, struct.unpack_from("<H", raw, central + 8)[0] | 0x800)
    raw[local + 30] = 0xFF
    raw[central + 46] = 0xFF
    archive_path.write_bytes(raw)
    extraction_path = tmp_path / "extracted"

    with pytest.raises(compile_lane_lock.ReferenceError):
        compile_lane_lock.extract_source_archive(archive_path, extraction_path)

    assert not extraction_path.exists() or not any(extraction_path.rglob("*"))
