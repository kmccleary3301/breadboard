from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.e4_parity import compile_lane_lock


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _assert_canonical_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    assert raw == _canonical_bytes(parsed)
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    return parsed


@pytest.fixture
def synthetic_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    entries = [
        {
            "description": "fixture capture adapter",
            "id": "fixture_capture",
            "metadata": {"impl": "fixture:capture", "kind": "capture_adapter"},
            "status": "active",
        },
        {
            "description": "fixture translator",
            "id": "fixture_translate",
            "metadata": {"impl": "fixture:translate", "kind": "translator"},
            "status": "active",
        },
        {
            "description": "fixture comparator",
            "id": "fixture_compare",
            "metadata": {"impl": "fixture:compare", "kind": "comparator"},
            "status": "active",
        },
    ]
    registry_path = tmp_path / "contracts/kernel/registries/e4_adapters.v1.json"
    _write_json(
        registry_path,
        {
            "schema_version": "bb.registry.v1",
            "registry_id": "e4_adapters",
            "entries": entries,
        },
    )

    config_id = "fixture.config"
    freeze_row = {"family": "fixture", "version": "1.0", "source": "frozen-source"}
    freeze_path = tmp_path / "freeze.yaml"
    _write_yaml(freeze_path, {"e4_configs": {config_id: freeze_row}})

    manifest = {
        "schema_version": "bb.e4.lane_manifest.v1",
        "lane_id": "fixture_lane",
        "config_id": config_id,
        "target": {
            "family": "fixture",
            "version": "1.0",
            "source_freeze_ref": "freeze.yaml",
        },
        "kind": "target_support",
        "capture": {
            "strategy": "adapter",
            "adapter": "fixture_capture",
            "inputs": [],
        },
        "normalize": {
            "mode": "translate",
            "translator": "fixture_translate",
            "projection_constants": {"author_owned": "must-not-enter-sidecar"},
        },
        "replay": {"mode": "stored", "comparator_class": "semantic"},
        "compare": {"comparator": "fixture_compare"},
        "claim": {"scope": {"behaviors": ["fixture behavior"]}, "exclusions": []},
        "artifacts_root": "artifacts/fixture_lane",
    }
    manifest_path = tmp_path / "fixture_lane.manifest.yaml"
    _write_yaml(manifest_path, manifest)
    monkeypatch.setattr(compile_lane_lock, "ROOT", tmp_path)
    return {
        "root": tmp_path,
        "entries": entries,
        "freeze_row": freeze_row,
        "manifest": manifest,
        "manifest_path": manifest_path,
    }


def test_migrate_extracts_legacy_packet_constants_and_emits_deterministic_canonical_bytes(
    synthetic_repo: dict[str, Any],
) -> None:
    root = synthetic_repo["root"]
    manifest_path = synthetic_repo["manifest_path"]
    legacy_path = root / "legacy-lane.yaml"
    payload_templates = {
        "report": {"message": "café", "result": None},
        "summary": {"items": []},
    }
    substitutions = {
        "report": [
            {"path": ["result"], "source": "input_refs", "key": "capture"}
        ]
    }
    _write_yaml(
        legacy_path,
        {
            "schema_version": "bb.e4.lane_def.v2",
            "lane_id": "fixture_lane",
            "normalize": {
                "config": {
                    "roles": {
                        "report": "artifacts/report.json",
                        "summary": "artifacts/summary.json",
                    },
                    "packet_constants": {
                        "payload_templates": payload_templates,
                        "substitutions": substitutions,
                        "unrelated_runtime_value": "excluded",
                    },
                }
            },
        },
    )
    lock_path = root / "out/lane.lock.json"
    sidecar_path = root / "out/lane.packet_constants.v1.json"
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

    assert compile_lane_lock.main(argv) == 0
    first_lock = lock_path.read_bytes()
    first_sidecar = sidecar_path.read_bytes()
    assert compile_lane_lock.main(argv) == 0
    assert lock_path.read_bytes() == first_lock
    assert sidecar_path.read_bytes() == first_sidecar

    sidecar = _assert_canonical_file(sidecar_path)
    assert sidecar["payload_templates"] == payload_templates
    assert sidecar["substitutions"] == substitutions
    assert "unrelated_runtime_value" not in sidecar

    lock = _assert_canonical_file(lock_path)
    assert lock["manifest_sha256"] == _digest(manifest_path.read_bytes())
    assert lock["packet_constants_ref"]["sha256"] == _digest(sidecar_path.read_bytes())
    assert lock["artifact_roles"] == {
        "report": {"path": "artifacts/report.json", "sha256": None, "bytes": None},
        "summary": {"path": "artifacts/summary.json", "sha256": None, "bytes": None},
    }
    legacy_input = next(
        row for row in lock["resolved_inputs"] if row["path"] == "legacy-lane.yaml"
    )
    assert legacy_input["sha256"] == _digest(legacy_path.read_bytes())
    assert legacy_input["bytes"] == len(legacy_path.read_bytes())


def test_compile_uses_only_declared_inputs_and_registry_entries_without_consulting_legacy(
    synthetic_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synthetic_repo["root"]
    manifest_path = synthetic_repo["manifest_path"]
    first_input = root / "inputs/root.json"
    second_input = root / "inputs/nested.yaml"
    _write_json(
        first_input,
        {
            "payload_templates": {"alpha": {"ordinal": 1}},
            "substitutions": {"alpha": []},
        },
    )
    _write_yaml(
        second_input,
        {
            "packet_constants": {
                "payload_templates": {"beta": {"ordinal": 2}},
                "substitutions": {
                    "beta": [
                        {"path": ["source"], "source": "input_refs", "key": "alpha"}
                    ]
                },
            }
        },
    )
    manifest = synthetic_repo["manifest"]
    manifest["capture"]["inputs"] = ["inputs/root.json", "inputs/nested.yaml"]
    _write_yaml(manifest_path, manifest)

    tempting_legacy = root / "fixture_lane.yaml"
    _write_yaml(
        tempting_legacy,
        {
            "normalize": {
                "config": {
                    "packet_constants": {
                        "payload_templates": {"forbidden": {"read": True}},
                        "substitutions": {},
                    }
                }
            }
        },
    )
    original_open = Path.open

    def reject_legacy_open(path: Path, *args: object, **kwargs: object):
        if path.resolve() == tempting_legacy.resolve():
            raise AssertionError("steady-state compile consulted the legacy descriptor")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_legacy_open)
    lock_path = root / "compiled.lock.json"
    sidecar_path = root / "compiled.packet_constants.v1.json"

    assert (
        compile_lane_lock.main(
            [
                "compile",
                str(manifest_path),
                "--lock",
                str(lock_path),
                "--sidecar",
                str(sidecar_path),
            ]
        )
        == 0
    )

    sidecar = _assert_canonical_file(sidecar_path)
    assert sidecar["payload_templates"] == {
        "alpha": {"ordinal": 1},
        "beta": {"ordinal": 2},
    }
    assert sidecar["substitutions"] == {
        "alpha": [],
        "beta": [{"path": ["source"], "source": "input_refs", "key": "alpha"}],
    }
    assert "author_owned" not in sidecar
    assert "forbidden" not in sidecar["payload_templates"]

    lock = _assert_canonical_file(lock_path)
    assert lock["artifact_roles"] == {}
    assert lock["manifest_sha256"] == _digest(manifest_path.read_bytes())
    assert lock["packet_constants_ref"]["sha256"] == _digest(sidecar_path.read_bytes())
    expected_freeze_preimage = json.dumps(
        {"row_id": "fixture.config", "row": synthetic_repo["freeze_row"]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert lock["target_freeze"] == {
        "config_id": "fixture.config",
        "freeze_manifest_row_sha256": _digest(expected_freeze_preimage),
    }

    resolved = {row["path"]: row for row in lock["resolved_inputs"]}
    for path in (first_input, second_input):
        relative = path.relative_to(root).as_posix()
        assert resolved[relative]["sha256"] == _digest(path.read_bytes())
        assert resolved[relative]["bytes"] == len(path.read_bytes())

    entry_by_id = {entry["id"]: entry for entry in synthetic_repo["entries"]}
    assert {pin["entry_id"]: pin for pin in lock["registry_pins"]} == {
        entry_id: {
            "registry": "e4_adapters",
            "entry_id": entry_id,
            "entry_sha256": _digest(_canonical_bytes(entry_by_id[entry_id])[:-1]),
        }
        for entry_id in ("fixture_capture", "fixture_translate", "fixture_compare")
    }


@pytest.mark.parametrize("drift_target", ["lock", "sidecar"])
def test_check_returns_five_when_either_generated_file_drifts(
    synthetic_repo: dict[str, Any], drift_target: str
) -> None:
    root = synthetic_repo["root"]
    manifest_path = synthetic_repo["manifest_path"]
    source_path = root / "inputs/constants.json"
    _write_json(
        source_path,
        {"payload_templates": {"report": {"ok": True}}, "substitutions": {}},
    )
    manifest = synthetic_repo["manifest"]
    manifest["capture"]["inputs"] = ["inputs/constants.json"]
    _write_yaml(manifest_path, manifest)
    lock_path = root / "check.lock.json"
    sidecar_path = root / "check.packet_constants.v1.json"
    argv = [
        "compile",
        str(manifest_path),
        "--lock",
        str(lock_path),
        "--sidecar",
        str(sidecar_path),
    ]

    assert compile_lane_lock.main(argv) == 0
    lock_before = lock_path.read_bytes()
    sidecar_before = sidecar_path.read_bytes()
    assert compile_lane_lock.main([*argv, "--check"]) == 0
    assert lock_path.read_bytes() == lock_before
    assert sidecar_path.read_bytes() == sidecar_before

    drift_path = lock_path if drift_target == "lock" else sidecar_path
    drift_path.write_bytes(drift_path.read_bytes() + b" ")
    drifted_lock = lock_path.read_bytes()
    drifted_sidecar = sidecar_path.read_bytes()

    assert compile_lane_lock.main([*argv, "--check"]) == 5
    assert lock_path.read_bytes() == drifted_lock
    assert sidecar_path.read_bytes() == drifted_sidecar
