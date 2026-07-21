from __future__ import annotations
import copy
import importlib
import shutil
from types import ModuleType
import pytest
from scripts.quality.build_surface_inventory import _binding_manifest, build_inventory
import scripts.quality.run_axis_smoke as axis_runner
from scripts.quality.run_axis_smoke import run_axis_smoke
from scripts.quality.validate_public_contracts import (
    ContractValidationError, PUBLIC_DIR, SURFACES,
    canonical_bytes,
    load_json,
    frozen_operation_ids,
    load_frozen_surface,
    validate_axis_manifest,
    validate_catalog,
    validate_record_surface,
    sync_record_schemas,
    validate_public_contracts,
)
def catalog() -> dict:
    return load_json(PUBLIC_DIR / "operations.v1.json")
def test_catalog_is_the_frozen_non_active_six_surface_candidate() -> None:
    value = catalog()
    validate_catalog(value)
    assert set(row["operation_id"] for row in value["operations"]) == frozen_operation_ids(load_frozen_surface())
    assert len(value["operations"]) == 45
    assert value["status"] == "candidate"
    for row in value["operations"]:
        assert set(row["bindings"]) == set(SURFACES)
        assert row["status"] == "candidate"
        assert {binding["status"] for binding in row["bindings"].values()} == {"candidate"}
def test_duplicate_operation_id_is_rejected() -> None:
    value = catalog()
    value["operations"][1]["operation_id"] = value["operations"][0]["operation_id"]
    with pytest.raises(ContractValidationError, match="duplicate operation IDs"):
        validate_catalog(value)
def test_missing_surface_binding_is_rejected() -> None:
    value = catalog()
    del value["operations"][0]["bindings"]["tui"]
    with pytest.raises(ContractValidationError, match="tui.*required property|missing surface bindings"):
        validate_catalog(value)
@pytest.mark.parametrize("surface", SURFACES)
def test_duplicate_surface_binding_identity_is_rejected(surface: str) -> None:
    value = catalog()
    duplicate = copy.deepcopy(value["operations"][0]["bindings"][surface])
    if surface == "openapi":
        duplicate["operation_id"] = value["operations"][1]["operation_id"]
    value["operations"][1]["bindings"][surface] = duplicate
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_catalog(value)
def test_openapi_operation_id_must_match_canonical_operation_id() -> None:
    value = catalog()
    value["operations"][0]["bindings"]["openapi"]["operation_id"] = "system.health"
    with pytest.raises(ContractValidationError, match="canonical operation ID"):
        validate_catalog(value)
def test_frozen_bbh_example_is_binding_invariant() -> None:
    value = catalog()
    next(row for row in value["operations"] if row["operation_id"] == "session.start")["bindings"]["bbh"]["command"] = "bbh session nope"
    with pytest.raises(ContractValidationError, match="differs from frozen example"):
        validate_catalog(value)
def test_docs_slug_cannot_escape_documentation_root() -> None:
    value = catalog()
    value["operations"][0]["bindings"]["docs"]["slug"] = "operations/../../outside"
    with pytest.raises(ContractValidationError, match="does not match"):
        validate_catalog(value)
def test_active_or_unknown_status_is_rejected() -> None:
    value = catalog()
    value["operations"][0]["status"] = "active"
    with pytest.raises(ContractValidationError, match="candidate"):
        validate_catalog(value)
@pytest.mark.parametrize("stability", [None, "candidate"])
def test_missing_or_invalid_stability_is_rejected(stability) -> None:
    value = catalog()
    if stability is None:
        del value["operations"][0]["stability"]
    else:
        value["operations"][0]["stability"] = stability
    with pytest.raises(ContractValidationError, match="stability|experimental"):
        validate_catalog(value)
def test_duplicated_inline_record_models_are_rejected() -> None:
    value = catalog()
    model = {"type": "object", "properties": {"id": {"type": "string"}}}
    value["operations"][0]["input_schema"] = copy.deepcopy(model)
    value["operations"][1]["input_schema"] = copy.deepcopy(model)
    with pytest.raises(ContractValidationError, match="schema ID|inline record models|not of type 'string'"):
        validate_catalog(value)
@pytest.mark.parametrize("mode,match", [("duplicate_owner", "multiple roles"), ("role_substitution", "frozen public record roles mismatch"), ("schema_swap", "semantic mapping"), ("missing_harness_report", "semantic mapping"), ("missing_schema", "absent from candidate/kernel roots")])
def test_record_surface_semantic_mapping_is_strict(mode, match) -> None:
    value = load_json(PUBLIC_DIR / "record_surface.v1.json")
    if mode == "duplicate_owner": value["roles"][1]["schema_ids"] = value["roles"][0]["schema_ids"]
    elif mode == "role_substitution": value["roles"][0]["role_id"] = "substituted_role"
    elif mode == "schema_swap": value["roles"][0]["schema_ids"], value["roles"][1]["schema_ids"] = value["roles"][1]["schema_ids"], value["roles"][0]["schema_ids"]
    elif mode == "missing_harness_report": next(row for row in value["roles"] if row["role_id"] == "validation_report")["schema_ids"] = ["bb.lane_validation_report.v1"]
    else: value["roles"][0]["schema_ids"] = ["bb.syntactically_valid_missing.v1"]
    with pytest.raises(ContractValidationError, match=match):
        validate_record_surface(value)
def staged_root(tmp_path):
    root = tmp_path / "staged"
    shutil.copytree(PUBLIC_DIR, root / "contracts" / "public")
    shutil.copytree(PUBLIC_DIR.parent / "kernel" / "schemas", root / "contracts" / "kernel" / "schemas")
    return root
def test_staged_contracts_reject_their_malformed_schema(tmp_path) -> None:
    root = staged_root(tmp_path)
    schema_path = root / "contracts" / "public" / "schemas" / "bb.public_operation_catalog.v1.schema.json"
    schema = load_json(schema_path)
    schema["type"] = 7
    schema_path.write_bytes(canonical_bytes(schema))
    with pytest.raises(ContractValidationError, match="invalid Draft 2020-12 schema"):
        validate_public_contracts(root / "contracts" / "public")
@pytest.mark.parametrize("mode", ["wrong_id", "duplicate_id"])
def test_staged_schema_identity_index_is_strict(tmp_path, mode) -> None:
    root = staged_root(tmp_path)
    path = root / "contracts/public/schemas/bb.public_operation_catalog.v1.schema.json"
    if mode == "wrong_id":
        schema = load_json(path)
        schema["$id"] = "https://example.invalid/bb.public_operation_catalog.v1.schema.json"
        path.write_bytes(canonical_bytes(schema))
    else:
        shutil.copy(path, root / "contracts/kernel/schemas" / path.name)
    with pytest.raises(ContractValidationError, match=r"\$id must equal|duplicate schema \$id"):
        validate_public_contracts(root / "contracts/public")
@pytest.mark.parametrize("mode", ["provenance", "schema_version", "conditional", "fixture_omission", "source_version", "top_level_extra"])
def test_staged_record_schema_source_rejects_false_identity(tmp_path, mode) -> None:
    public_dir = staged_root(tmp_path) / "contracts/public"
    path = public_dir / "record_schemas.v1.json"
    source = load_json(path)
    if mode == "provenance": source["decision_inputs"]["executable_replay_sha256"] = "sha256:" + "0" * 64
    elif mode == "schema_version": source["schemas"]["bb.page.v1"]["properties"]["schema_version"]["const"] = "1.0"
    elif mode == "conditional": del source["schemas"]["bb.comparison_report.v1"]["allOf"]
    elif mode == "fixture_omission": del source["fixtures"]["bb.page.v1"]
    elif mode == "source_version": source["version"] = 2
    else: source["unexpected"] = True
    path.write_bytes(canonical_bytes(source))
    with pytest.raises(ContractValidationError, match="frozen provenance|schema identity|contradiction fixture passed|fixture schema keys|source envelope"):
        validate_public_contracts(public_dir)
def test_generated_record_schema_drift_fails_and_rewrites_deterministically(tmp_path) -> None:
    root = staged_root(tmp_path)
    public_dir = root / "contracts/public"
    path = public_dir / "schemas/bb.page.v1.schema.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ContractValidationError, match="generated record schema is stale"):
        validate_public_contracts(public_dir)
    sync_record_schemas(public_dir, write=True)
    validate_public_contracts(public_dir)
def test_staged_inventory_uses_its_inventory_schema(tmp_path) -> None:
    root = staged_root(tmp_path)
    schema_path = root / "contracts" / "public" / "schemas" / "bb.public_surface_inventory.v1.schema.json"
    schema = load_json(schema_path)
    schema["properties"]["operation_count"]["const"] = 44
    schema_path.write_bytes(canonical_bytes(schema))
    with pytest.raises(ContractValidationError, match="invalid generated inventory"):
        build_inventory(root)
def test_inventory_is_a_checked_in_fixed_point_with_honest_gaps() -> None:
    first = build_inventory()
    second = build_inventory()
    assert canonical_bytes(first) == canonical_bytes(second)
    assert canonical_bytes(first) == (PUBLIC_DIR / "surface_inventory.v1.json").read_bytes()
    assert first["parity_claimed"] is False
    assert first["candidate_status"] == "candidate"
    assert all(summary["gaps"] > 0 for summary in first["summary"].values())
    assert all(summary["detected"] + summary["gaps"] == 45 for summary in first["summary"].values())
def test_generated_binding_manifest_ignores_source_text(tmp_path) -> None:
    manifest = tmp_path / "generated" / "public_surface_manifest.v1.json"
    (tmp_path / "client.test.ts").write_text("// system.health BreadBoardClient health", encoding="utf-8")
    assert _binding_manifest(manifest, ("operation_id", "client", "method")) == set()
    manifest.parent.mkdir()
    manifest.write_bytes(canonical_bytes({"operations": [{"operation_id": "system.health", "client": "BreadBoardClient", "method": "health"}]}))
    assert _binding_manifest(manifest, ("operation_id", "client", "method")) == {("system.health", "BreadBoardClient", "health")}
def test_axis_check_timeout_kills_grandchild_holding_stdout(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "grandchild.pid"
    monkeypatch.setattr(axis_runner, "CHECK_TIMEOUT_SECONDS", 0.25)
    code = f"import pathlib,subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))"
    result = axis_runner._run_check(["python", "-c", code], tmp_path)
    assert result["status"] == "failed" and result["exit_code"] is None and "timed out" in result["failure"]
    with pytest.raises(ProcessLookupError):
        axis_runner.os.kill(int(pid_path.read_text()), 0)
def test_axis_check_timeout_survives_early_pipe_eof(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "closed-pipe.pid"
    monkeypatch.setattr(axis_runner, "CHECK_TIMEOUT_SECONDS", 0.25)
    code = f"import os,pathlib,time; pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); os.close(1); os.close(2); time.sleep(30)"
    result = axis_runner._run_check(["python", "-c", code], tmp_path)
    assert result["status"] == "failed" and result["exit_code"] is None and "timed out" in result["failure"]
    with pytest.raises(ProcessLookupError): axis_runner.os.kill(int(pid_path.read_text()), 0)
def test_axis_check_output_is_bounded_with_real_child(tmp_path) -> None:
    code = f"import os; os.write(1, b'x' * {axis_runner.MAX_OUTPUT_BYTES * 4})"
    result = axis_runner._run_check(["python", "-c", code], tmp_path)
    assert result["status"] == "passed" and result["output_truncated"] is True
    assert len(result["output"].split("\n[output truncated", 1)[0].encode()) == axis_runner.MAX_OUTPUT_BYTES
def test_axis_manifest_and_runner_emit_only_the_frozen_names_without_scores() -> None:
    manifest_path = PUBLIC_DIR / "axis_smoke.v1.json"
    manifest = load_json(manifest_path)
    validate_axis_manifest(manifest)
    expected = [
        "maintainability", "atomicity_composability", "generalizability",
        "development_velocity", "versatility", "developer_experience",
        "interpretability", "intuitive_configuration", "forward_compatibility",
        "blast_radius",
    ]
    result = run_axis_smoke(manifest_path, execute=False)
    assert [row["axis_id"] for row in result["axis_results"]] == expected
    assert all(row["score"] is None for row in result["axis_results"])
    assert result["score_promoted"] is False
    assert result["passed"] is False
def test_axis_manifest_cannot_embed_a_promoted_score() -> None:
    manifest = load_json(PUBLIC_DIR / "axis_smoke.v1.json")
    manifest["axes"][0]["score"] = 10
    with pytest.raises(ContractValidationError, match="[Aa]dditional properties"):
        validate_axis_manifest(manifest)
def test_product_boundary_is_declarative_and_exports_no_implementation() -> None:
    product = importlib.import_module("breadboard.product")
    assert product.__all__ == []
    public_bindings = {
        name: value
        for name, value in vars(product).items()
        if not name.startswith("_")
    }
    assert all(isinstance(value, ModuleType) for value in public_bindings.values())