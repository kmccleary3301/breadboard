from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

import pytest

from scripts.e4_parity.adapters import oh_my_pi_compiler_capture as compiler
from scripts.e4_parity.adapters.oh_my_pi_projection_packet import (
    build_projection_packet,
    canonical_json_bytes,
)
from scripts.e4_parity.lane_definitions import load_manifest_lane_def


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
LANE_DIR = ROOT / "config/e4_lanes"
INVENTORY_PATH = ROOT / "docs/conformance/e4_lane_inventory.json"
SUPPORT_CLAIMS_DIR = ROOT / "docs/conformance/support_claims"

P3_SCRATCH_LANES = (
    "oh_my_pi_p3_1_effective_config_graph_compiler",
    "oh_my_pi_p3_2_context_resource_pack_compiler",
    "oh_my_pi_p3_5_resource_blob_compiler",
    "oh_my_pi_p3_8_projection_broker_adapter",
)
SCRATCH_LANES = (
    *P3_SCRATCH_LANES,
    "oh_my_pi_p6_0_l5_memory_compaction",
    "oh_my_pi_p6_0_l6_tui_projection",
)
P31_PROJECTION_HASHES = {
    "capability_registry": "sha256:20a474699dc6d6886d79036d320ead3085812701359dec49d4b343f0cabbce92",
    "effective_config_graph": "sha256:3816a6751ea56e8526f0acc0cceb50a93d651090d8ed553457e1ac7e37d2c898",
    "effective_tool_surface": "sha256:1ad75cf55a3e26cd73580e5aa152c37cdf35373c0dcc86b0712a33fdbcdf4359",
}
P31_GRAPH_HASH = "sha256:3a5699fc84dde3cd78dd7116400650f17ded5a4b4d0af1f415afd196d1a5b490"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _lane(lane_id: str) -> dict[str, Any]:
    lane = (
        load_manifest_lane_def(LANE_DIR / f"{lane_id}.manifest.yaml")
        if lane_id == "oh_my_pi_p6_6_task_job_subagent"
        else _load_json(LANE_DIR / f"{lane_id}.yaml")
    )
    assert isinstance(lane, dict)
    return lane


def _inventory_lane(lane_id: str) -> dict[str, Any]:
    inventory = _load_json(INVENTORY_PATH)
    return next(row for row in inventory["lanes"] if row["lane_id"] == lane_id)


def _evidence_manifest(inventory_lane: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    claim_id = str(inventory_lane["claim_id"])
    path = SUPPORT_CLAIMS_DIR / f"{claim_id.replace('_support_claim', '_evidence_manifest')}.json"
    payload = _load_json(path)
    assert isinstance(payload, dict)
    return path, payload


def _resolve(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if path_text.startswith("docs_tmp/") or path_text.startswith(f"{ROOT.name}/"):
        return WORKSPACE / path
    return ROOT / path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _role_paths(lane: Mapping[str, Any]) -> dict[str, str]:
    return dict(lane["normalize"]["config"]["roles"])


def _files(paths: set[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        if path.is_dir():
            files.update(child for child in path.rglob("*") if child.is_file())
        else:
            assert path.is_file(), path
            files.add(path)
    return sorted(files)


def _accepted_snapshot(
    lane: Mapping[str, Any],
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> dict[Path, bytes]:
    paths = {_resolve(str(path)) for path in lane["capture"]["inputs"]}
    paths.update(_resolve(path) for path in _role_paths(lane).values())
    paths.update(_resolve(str(row["path"])) for row in manifest["artifacts"])
    paths.add(manifest_path)
    return {path: path.read_bytes() for path in _files(paths)}


def _assert_accepted_unchanged(snapshot: Mapping[Path, bytes]) -> None:
    assert {path: path.read_bytes() for path in snapshot} == snapshot


def test_compiler_ledger_reads_workspace_evidence() -> None:
    assert compiler.WORKSPACE == WORKSPACE
    assert compiler.LEDGER_PATH == (
        WORKSPACE / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    )


@pytest.mark.parametrize("lane_id", SCRATCH_LANES)
def test_scratch_capture_reproduces_governed_accepted_role_bytes_without_accepted_writes(
    lane_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _lane(lane_id)
    inventory_lane = _inventory_lane(lane_id)
    manifest_path, manifest = _evidence_manifest(inventory_lane)
    accepted_before = _accepted_snapshot(lane, manifest_path, manifest)
    scratch = tmp_path / "scratch"
    writes: list[Path] = []
    original_write_bytes = Path.write_bytes
    original_write_text = Path.write_text

    def recording_write_bytes(path: Path, data: bytes) -> int:
        writes.append(path.resolve())
        return original_write_bytes(path, data)

    def recording_write_text(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
        writes.append(path.resolve())
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", recording_write_bytes)
    monkeypatch.setattr(Path, "write_text", recording_write_text)

    report = compiler.capture(
        lane,
        inventory_lane,
        promote_accepted=False,
        out_dir=scratch,
    )

    assert report["ok"] is True
    if lane_id == "oh_my_pi_p3_1_effective_config_graph_compiler":
        assert {
            report["paths"]["parity_results"],
            report["paths"]["secret_scan_report"],
            report["paths"]["validator_output"],
        } == {
            f"docs/conformance/e4_target_support/{lane_id}/parity_results.json",
            f"docs/conformance/e4_target_support/{lane_id}/secret_scan_report.json",
            f"docs/conformance/e4_target_support/{lane_id}/prevalidation_report.json",
        }
    assert writes
    scratch_root = scratch.resolve()
    for written in writes:
        written.relative_to(scratch_root)

    configured_roles = _role_paths(lane)
    packet_constants = lane["normalize"]["config"].get("packet_constants")
    required_roles = packet_constants.get("required_roles") if isinstance(packet_constants, Mapping) else None
    emitted_roles = required_roles if isinstance(required_roles, list) else list(configured_roles)
    assert set(emitted_roles) <= set(configured_roles)
    manifest_by_role = {str(row["role"]): row for row in manifest["artifacts"]}
    for role in emitted_roles:
        logical_path = configured_roles[role]
        accepted = _resolve(logical_path)
        emitted = scratch / logical_path
        assert emitted.read_bytes() == accepted.read_bytes(), role
        assert _sha256(emitted) == _sha256(accepted), role
        manifest_row = manifest_by_role.get(role)
        if manifest_row is not None:
            assert manifest_row["path"] == logical_path
            assert _sha256(accepted) == manifest_row["sha256"]

    _assert_accepted_unchanged(accepted_before)

def test_p31_lane_names_and_byte_locks_all_three_projection_outputs() -> None:
    lane = _lane("oh_my_pi_p3_1_effective_config_graph_compiler")
    builders = lane["normalize"]["config"]["record_builders"]
    roles = _role_paths(lane)

    assert [row["records"] for row in builders] == [
        ["capability_registry"],
        ["effective_config_graph"],
        ["effective_tool_surface"],
    ]
    assert [row["projection"] for row in builders] == [
        "p3_1_capability_registry",
        "p3_1_effective_config_graph",
        "p3_1_effective_tool_surface",
    ]

    for role, expected_hash in P31_PROJECTION_HASHES.items():
        assert _sha256(_resolve(roles[role])) == expected_hash

    graph = _load_json(_resolve(roles["effective_config_graph"]))
    assert graph["graph_hash"] == P31_GRAPH_HASH


def test_record_builder_descriptors_select_only_configured_projections_in_list_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / "source.json"
    source.write_text('{"source": true}\n', encoding="utf-8")
    source_ref = source.relative_to(checkout).as_posix()
    monkeypatch.setattr(compiler, "ROOT", checkout)
    configured = ("p3_8_projection_event", "p3_2_context_resource_pack")
    calls: list[str] = []

    def unexpected_projection(_context: Mapping[str, Any]) -> dict[str, Any]:
        raise AssertionError("an unconfigured projection callback was invoked")

    for projection_id in tuple(compiler.PROJECTIONS):
        monkeypatch.setitem(compiler.PROJECTIONS, projection_id, unexpected_projection)

    builders = []
    for index, projection_id in enumerate(configured):
        record_key = f"record_{index}"

        def projection(
            _context: Mapping[str, Any],
            *,
            projection_id: str = projection_id,
            record_key: str = record_key,
        ) -> dict[str, Any]:
            calls.append(projection_id)
            return {"records": [{"record_key": record_key, "value": {"projection": projection_id}}]}

        monkeypatch.setitem(compiler.PROJECTIONS, projection_id, projection)
        builders.append(
            {
                "id": projection_id,
                "projection": projection_id,
                "schema_version": f"bb.test_projection_{index}.v1",
                "source": source_ref,
                "records": [record_key],
            }
        )

    lane = {
        "lane_id": "lane-id-must-not-control-dispatch",
        "config_id": "compiler_projection_selection_v1",
        "target_family": "oh_my_pi",
        "target_version": "test",
        "capture": {"inputs": [source_ref]},
        "normalize": {"config": {"record_builders": builders, "roles": {}}},
        "run": {"run_id": "run", "provider_model": "none", "sandbox_mode": "read-only"},
    }
    inventory_lane = _inventory_lane("oh_my_pi_p3_2_context_resource_pack_compiler")
    monkeypatch.setattr(compiler, "_finalize_projected_record", lambda _schema, value: dict(value))

    selected_builders, records, _derived_facts, _projection_inputs = compiler._execute_record_builders(lane, inventory_lane)

    assert selected_builders == tuple(builders)
    assert calls == list(configured)
    assert [record["projection"] for record in records.values()] == list(configured)

def test_projection_inputs_reject_absolute_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"source": true}\n', encoding="utf-8")
    lane = {"capture": {"inputs": [str(source)]}, "normalize": {"config": {}}}

    with pytest.raises(ValueError, match="capture input must be relative to checkout"):
        compiler._load_projection_inputs(lane)


@pytest.mark.parametrize(
    ("invalid_kind", "message"),
    [
        ("unknown", "unknown projection: projection_missing_from_registry"),
        ("duplicate", "duplicate record builder id: p3_2_context_resource_pack"),
        ("mismatched", "projection must match id"),
    ],
)
def test_invalid_projection_descriptors_fail_before_creating_scratch_output(
    invalid_kind: str,
    message: str,
    tmp_path: Path,
) -> None:
    lane = _lane("oh_my_pi_p3_2_context_resource_pack_compiler")
    builders = lane["normalize"]["config"]["record_builders"]
    if invalid_kind == "unknown":
        builders[0]["id"] = "projection_missing_from_registry"
        builders[0]["projection"] = "projection_missing_from_registry"
    elif invalid_kind == "duplicate":
        builders.append(copy.deepcopy(builders[0]))
    else:
        builders[0]["projection"] = "p3_8_projection_event"
    scratch = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match=message):
        compiler.capture(
            lane,
            _inventory_lane("oh_my_pi_p3_2_context_resource_pack_compiler"),
            promote_accepted=False,
            out_dir=scratch,
        )

    assert not scratch.exists()


def test_declared_record_outputs_cross_the_schema_specific_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinary_calls: list[tuple[str, Mapping[str, Any]]] = []
    graph_calls: list[Mapping[str, Any]] = []

    def get_spec(schema_version: str) -> SimpleNamespace:
        return SimpleNamespace(schema_version=schema_version, hash_field=None)

    def finalize_record(spec: SimpleNamespace, value: Mapping[str, Any]) -> dict[str, Any]:
        ordinary_calls.append((spec.schema_version, value))
        return {"finalized_schema": spec.schema_version, **value}

    def finalize_graph(value: Mapping[str, Any]) -> dict[str, Any]:
        graph_calls.append(value)
        return {"finalized_schema": "bb.effective_config_graph.v1", **value}

    monkeypatch.setattr(compiler, "get_spec", get_spec)
    monkeypatch.setattr(compiler, "finalize_record", finalize_record)
    monkeypatch.setattr(compiler, "finalize_effective_config_graph", finalize_graph)

    expected_ordinary: list[tuple[str, Mapping[str, Any]]] = []
    expected_graph: list[Mapping[str, Any]] = []
    for lane_id in P3_SCRATCH_LANES:
        for builder in _lane(lane_id)["normalize"]["config"]["record_builders"]:
            rows = []
            for record_key in builder["records"]:
                value = {"record_key_under_test": record_key}
                rows.append(
                    {
                        "record_key": record_key,
                        "schema_version": builder["schema_version"],
                        "value": value,
                    }
                )
                if builder["schema_version"] == "bb.effective_config_graph.v1":
                    expected_graph.append(value)
                else:
                    expected_ordinary.append((builder["schema_version"], value))

            normalized, _facts = compiler._normalize_projected_records(builder, {"records": rows})
            assert list(normalized) == builder["records"]
            assert all(value["finalized_schema"] == builder["schema_version"] for value in normalized.values())

    assert ordinary_calls == expected_ordinary
    assert graph_calls == expected_graph


def _projection_context(lane_id: str, projection_id: str) -> tuple[Callable[[Mapping[str, Any]], dict[str, Any]], dict[str, Any]]:
    lane = _lane(lane_id)
    inventory_lane = _inventory_lane(lane_id)
    builder = next(row for row in compiler._record_builders(lane) if row["id"] == projection_id)
    inputs = compiler._load_projection_inputs(lane)
    return compiler.PROJECTIONS[projection_id], compiler._projection_context(lane, inventory_lane, builder, inputs)


def _mutate_p31(context: dict[str, Any]) -> None:
    context["l1_probe"]["builtinToolNames"].append("mutation_probe_tool")
    context["l2_probe"]["builtin_tool_count"] = len(context["l1_probe"]["builtinToolNames"])


def _mutate_p32(context: dict[str, Any]) -> None:
    context["lane_id"] = f"{context['lane_id']}_mutated"


def _mutate_l5(context: dict[str, Any]) -> None:
    context["source"]["value"]["compaction_runtime"]["snapcompactResult"]["summary"] += " mutated"
    context["source"]["sha256"] = "sha256:" + "1" * 64


def _mutate_l6(context: dict[str, Any]) -> None:
    stdout_path = context["constants"]["events"][0]["stdout_path"]
    context["inputs"][stdout_path]["sha256"] = "sha256:" + "2" * 64



@pytest.mark.parametrize(
    ("lane_id", "projection_id", "mutate"),
    [
        (
            "oh_my_pi_p3_1_effective_config_graph_compiler",
            "p3_1_effective_tool_surface",
            _mutate_p31,
        ),
        (
            "oh_my_pi_p3_2_context_resource_pack_compiler",
            "p3_2_context_resource_pack",
            _mutate_p32,
        ),
        (
            "oh_my_pi_p6_0_l5_memory_compaction",
            "p6_l5_memory_compaction_plan",
            _mutate_l5,
        ),
        (
            "oh_my_pi_p6_0_l6_tui_projection",
            "p6_l6_tui_projection",
            _mutate_l6,
        ),
    ],
)
def test_pure_projections_are_repeatable_and_sensitive_to_declared_semantic_context(
    lane_id: str,
    projection_id: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    projection, context = _projection_context(lane_id, projection_id)
    original_context = copy.deepcopy(context)

    first = projection(context)
    second = projection(context)

    assert first == second
    assert context == original_context

    changed_context = copy.deepcopy(context)
    mutate(changed_context)
    assert projection(changed_context) != first


def test_generic_projection_packet_assembles_declared_record_envelope_and_substitutions(
    tmp_path: Path,
) -> None:
    lane = {
        "lane_id": "data_driven_lane",
        "config_id": "data_driven_lane_v1",
        "normalize": {
            "config": {
                "roles": {
                    "record": "records/record.json",
                    "bundle": "records/bundle.json",
                    "report": "reports/report.json",
                },
                "packet_constants": {
                    "required_records": ["alpha"],
                    "record_roles": {"record": "alpha"},
                    "record_envelopes": {
                        "bundle": {
                            "records_field": "items",
                            "records": ["alpha"],
                            "fields": {"schema_version": "bundle.v1"},
                        }
                    },
                    "payload_templates": {
                        "report": {"digest": None, "source": None, "count": None}
                    },
                    "substitutions": {
                        "report": [
                            {"path": ["digest"], "source": "role_hashes", "key": "record"},
                            {"path": ["source"], "source": "input_refs", "key": "capture"},
                            {"path": ["count"], "source": "derived_values", "key": "record_count"},
                        ]
                    },
                    "required_roles": ["record", "bundle", "report"],
                },
            }
        },
    }
    physical = {
        "record": tmp_path / "records/record.json",
        "bundle": tmp_path / "records/bundle.json",
        "report": tmp_path / "reports/report.json",
    }
    alpha = {"schema_version": "alpha.v1", "value": 7}

    packet = build_projection_packet(
        lane,
        {"claim_id": "claim"},
        {"alpha": alpha},
        {"record_count": 1},
        physical_roles=physical,
        role_hashes={"record": "sha256:" + "a" * 64},
        input_refs={"capture": "capture.json#sha256:" + "b" * 64},
    )

    assert packet["record"] == canonical_json_bytes(alpha)
    assert _load_json(physical["bundle"]) == {"schema_version": "bundle.v1", "items": [alpha]}
    # Rendered role hashes supersede caller-seeded role_hashes so downstream
    # roles always bind to the bytes the packet actually emitted.
    rendered_record_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(alpha)).hexdigest()
    assert _load_json(physical["report"]) == {
        "digest": rendered_record_hash,
        "source": "capture.json#sha256:" + "b" * 64,
        "count": 1,
    }
    assert {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.json")} == {
        "records/record.json",
        "records/bundle.json",
        "reports/report.json",
    }


def _packet_lane(roles: Mapping[str, str], constants: Mapping[str, Any], **top_level: Any) -> dict[str, Any]:
    lane = {
        "lane_id": "data_driven_lane",
        "config_id": "data_driven_lane_v1",
        "normalize": {
            "config": {
                "roles": dict(roles),
                "packet_constants": {
                    "required_records": [],
                    "record_roles": {},
                    "record_envelopes": {},
                    **constants,
                },
            }
        },
    }
    lane.update(top_level)
    return lane


def test_source_payloads_replace_only_declared_template_roles() -> None:
    lane = _packet_lane(
        {"report": "reports/report.json"},
        {
            "payload_templates": {"report": {"status": "template"}},
            "required_roles": ["report"],
        },
    )

    packet = build_projection_packet(
        lane,
        {"claim_id": "claim"},
        {},
        {},
        source_payloads={
            "report": {"status": "accepted"},
            "unknown_role": {"status": "ignored"},
        },
    )

    assert json.loads(packet["report"]) == {"status": "accepted"}
    assert "unknown_role" not in packet


def test_role_aliases_emit_byte_identical_payloads() -> None:
    lane = _packet_lane(
        {
            "comparator": "reports/comparator.json",
            "alias": "reports/alias.json",
        },
        {
            "payload_templates": {"comparator": {"ok": True}},
            "role_aliases": {"alias": "comparator"},
            "required_roles": ["comparator", "alias"],
        },
    )

    packet = build_projection_packet(lane, {"claim_id": "claim"}, {}, {})

    assert packet["alias"] == packet["comparator"]


def test_auto_bind_role_refs_binds_refs_hash_maps_and_artifact_rows_topologically() -> None:
    stale = "sha256:" + "0" * 64
    lane = _packet_lane(
        {
            "base": "data/base.json",
            "mid": "data/mid.json",
            "top": "data/top.json",
        },
        {
            "auto_bind_role_refs": True,
            "payload_templates": {
                "base": {"kind": "base"},
                "mid": {
                    "artifacts": [
                        {"path": "data/base.json", "sha256": stale, "bytes": 1, "exists": False}
                    ],
                    "input_hashes": {"data/base.json": stale},
                    "ref": f"data/base.json#{stale}",
                },
                "top": {
                    "hashes": {"mid": stale},
                    "mid_ref": f"data/mid.json#{stale}",
                    "refs": {"mid": "data/mid.json"},
                },
            },
            # Reverse listing order proves rendering follows reference topology.
            "required_roles": ["top", "mid", "base"],
        },
    )

    packet = build_projection_packet(
        lane,
        {"claim_id": "claim"},
        {},
        {},
        source_bytes={"base": 1},
        role_hashes={"base": stale},
        input_refs={"base": f"data/base.json#{stale}"},
        source_payloads={
            "mid": {
                "artifacts": [
                    {"path": "data/base.json", "sha256": stale, "bytes": 1, "exists": False}
                ],
                "input_hashes": {"data/base.json": stale},
                "ref": f"data/base.json#{stale}",
            }
        },
    )

    base_hash = "sha256:" + hashlib.sha256(packet["base"]).hexdigest()
    mid_hash = "sha256:" + hashlib.sha256(packet["mid"]).hexdigest()
    mid = json.loads(packet["mid"])
    assert mid["ref"] == f"data/base.json#{base_hash}"
    assert mid["input_hashes"] == {"data/base.json": base_hash}
    assert mid["artifacts"] == [
        {"path": "data/base.json", "sha256": base_hash, "bytes": len(packet["base"]), "exists": True}
    ]
    assert json.loads(packet["top"]) == {
        "hashes": {"mid": mid_hash},
        "mid_ref": f"data/mid.json#{mid_hash}",
        "refs": {"mid": "data/mid.json"},
    }


def test_lane_values_derive_freeze_ledger_catalog_and_scope_facts() -> None:
    config_id = "data_driven_lane_v1"
    freeze_row = {"config_path": "agent_configs/misc/data_driven_lane_v1.yaml", "points": 20}
    ledger_row = {"feature_id": "feat_test", "behavior_family": "work_items"}
    row_digest = lambda row_id, row: "sha256:" + hashlib.sha256(
        json.dumps({"row_id": row_id, "row": row}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    lane = _packet_lane(
        {
            "freeze_manifest": "config/freeze.yaml",
            "atomic_feature_ledger": "docs_tmp/ledger.json",
            "artifact_catalog": "docs/catalog.json",
            "support_claim_ref": "claims/support_claim.json",
            "report": "reports/report.json",
        },
        {
            "scope_observation_labels": ["raw_capture", "target_probe"],
            "payload_templates": {
                "support_claim_ref": {"schema_version": "bb.e4.support_claim.v2"},
                "report": {
                    "catalog_hash": None,
                    "catalog_revision": None,
                    "checks": {"raw_run_ids_match_declared_scope": None},
                    "freeze_ref": None,
                    "freeze_row_hash": None,
                    "generated_from": None,
                    "ledger_row_ref": None,
                    "observed_provider_models": None,
                    "observed_run_ids": None,
                    "provider_model": None,
                    "run_id": None,
                },
            },
            "substitutions": {
                "report": [
                    {"path": ["catalog_hash"], "source": "lane_values", "key": "catalog_hash"},
                    {"path": ["catalog_revision"], "source": "lane_values", "key": "catalog_revision"},
                    {"path": ["checks", "raw_run_ids_match_declared_scope"], "source": "lane_values", "key": "true"},
                    {"path": ["freeze_ref"], "source": "lane_values", "key": "freeze_ref"},
                    {"path": ["freeze_row_hash"], "source": "lane_values", "key": "freeze_row_hash"},
                    {"path": ["generated_from"], "source": "lane_values", "key": "support_claim_schema_version"},
                    {"path": ["ledger_row_ref"], "source": "lane_values", "key": "ledger_row_ref"},
                    {"path": ["observed_provider_models"], "source": "lane_values", "key": "observed_provider_models"},
                    {"path": ["observed_run_ids"], "source": "lane_values", "key": "observed_run_ids"},
                    {"path": ["provider_model"], "source": "lane_values", "key": "provider_model"},
                    {"path": ["run_id"], "source": "lane_values", "key": "run_id"},
                ]
            },
            "required_roles": ["support_claim_ref", "report"],
        },
        run={"run_id": "run_1", "provider_model": "provider/x", "sandbox_mode": "read-only"},
    )

    packet = build_projection_packet(
        lane,
        {"claim_id": "claim", "ledger_feature_ids": ["feat_test"]},
        {},
        {},
        source_values={
            "freeze_manifest": {"e4_configs": {config_id: freeze_row}},
            "atomic_feature_ledger": {"rows": [ledger_row]},
            "artifact_catalog": {
                "integrity": {"stable_entries_hash": "sha256:" + "c" * 64},
                "revision": 6,
            },
        },
    )

    freeze_hash = row_digest(config_id, freeze_row)
    ledger_hash = row_digest("feat_test", ledger_row)
    assert json.loads(packet["report"]) == {
        "catalog_hash": "sha256:" + "c" * 64,
        "catalog_revision": 6,
        "checks": {"raw_run_ids_match_declared_scope": True},
        "freeze_ref": f"config/freeze.yaml#{config_id}#{freeze_hash}",
        "freeze_row_hash": freeze_hash,
        "generated_from": "bb.e4.support_claim.v2",
        "ledger_row_ref": f"docs_tmp/ledger.json#feat_test#{ledger_hash}",
        "observed_provider_models": {"raw_capture": "provider/x", "target_probe": "provider/x"},
        "observed_run_ids": {"raw_capture": "run_1", "target_probe": "run_1"},
        "provider_model": "provider/x",
        "run_id": "run_1",
    }
