from __future__ import annotations
import copy
import json
import sys
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from types import MappingProxyType
import pytest
from breadboard.product.harness.model import HarnessDefinition
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError, ValidationFinding, load_harness_definition,
    parse_harness_definition, validate_harness_definition)
ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "contracts/public/schemas/bb.harness_definition.v1.schema.json"
V2_SCHEMA_ID = "https://breadboard.dev/contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json"
def _definition(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "bb.harness_definition.v1",
        "version": 1,
        "workspace": {"root": "."},
        "providers": {"default_model": "main", "models": [{"id": "main", "adapter": "openai"}]},
        "modes": [{"name": "build"}],
        "loop": {"sequence": [{"mode": "build"}]},
    }
    document.update(updates)
    return document
def _pairs(document: object) -> list[tuple[str, str]]:
    return [(item.pointer, item.code) for item in validate_harness_definition(document)]  # type: ignore[arg-type]
def _schema_strings(value: object) -> list[str]:
    if isinstance(value, str): return [value]
    if isinstance(value, dict): return [*value, *(item for child in value.values() for item in _schema_strings(child))]
    if isinstance(value, list): return [item for child in value for item in _schema_strings(child)]
    return []
def test_public_schema_reuses_each_v2_property_grammar() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "https://breadboard.dev/contracts/public/schemas/bb.harness_definition.v1.schema.json"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "schema_version", "version", "workspace", "providers", "modes", "loop"
    ]
    assert schema["properties"]["schema_version"] == {"const": "bb.harness_definition.v1"}
    assert schema["properties"]["version"] == {"const": 1}
    for name, property_schema in schema["properties"].items():
        if name not in {"schema_version", "version"}:
            assert property_schema == {"$ref": f"{V2_SCHEMA_ID}#/properties/{name}"}
    surface = "\n".join(_schema_strings(schema)).casefold()
    assert not any(token in surface for token in ("implementations/", "import_path", "agentic_coder_prototype", "breadboard.product"))
@pytest.mark.parametrize(
    "source", [("bb.harness_definition.v1", 1), ("bb.agent_config_surface.v2", 2)]
)
def test_sources_and_mapping_instances_round_trip_without_mutation(
    source: tuple[str, int],
) -> None:
    document = _definition(schema_version=source[0], version=source[1])
    original = copy.deepcopy(document)
    assert validate_harness_definition(MappingProxyType(document)) == ()
    assert parse_harness_definition(document).as_dict() == original
    assert document == original
def test_multiple_errors_are_expanded_deduplicated_and_sorted() -> None:
    document = _definition(
        zeta=True, alpha=True, workspace={}, providers={"models": [{"id": "main"}]},
        modes=[{}], loop={"sequence": []})
    findings = validate_harness_definition(document)
    assert [(item.pointer, item.code) for item in findings] == [
        ("/alpha", "additionalProperties"), ("/loop/sequence", "minItems"),
        ("/modes/0/name", "required"), ("/providers/default_model", "required"),
        ("/providers/models/0/adapter", "required"), ("/workspace/root", "required"),
        ("/zeta", "additionalProperties")]
    assert findings == tuple(sorted(set(findings)))
def test_unknown_fields_have_exact_escaped_pointers() -> None:
    document = _definition()
    document["a/b~c"] = True
    assert _pairs(document) == [("/a~1b~0c", "additionalProperties")]
@pytest.mark.parametrize(("updates", "finding"), [
    ({"workspace": {}}, ValidationFinding(
        "/workspace/root", "required", "'root' is a required property")),
    ({"workspace": {"root": ".", "unknown": True}}, ValidationFinding(
        "/workspace/unknown", "additionalProperties",
        "Additional property 'unknown' is not allowed")),
    ({"workspace": {"root": 7}}, ValidationFinding(
        "/workspace/root", "type", 'Value must have type "string"')),
    ({"loop": {"sequence": []}}, ValidationFinding(
        "/loop/sequence", "minItems", "Array must contain at least 1 item(s)")),
])
def test_schema_findings_have_project_owned_exact_messages(
    updates: dict[str, object], finding: ValidationFinding
) -> None:
    document = _definition(**updates)
    before = copy.deepcopy(document)
    assert validate_harness_definition(document) == (finding,)
    assert document == before
def test_v2_open_dynamic_maps_remain_open_through_canonical_refs() -> None:
    document = _definition(
        dossier={"arbitrary": {"nested": [1, True, None]}},
        tools={"aliases": {"author-name": "runtime-name"},
               "dialects": {"selection": {"by_model": {"dynamic-model": ["dialect-a"]}}}})
    assert validate_harness_definition(document) == ()
@pytest.mark.parametrize(("value", "finding"), [
    ({}, ValidationFinding(
        "/tools/dialects/preference/by_model/main/order", "required",
        "'order' is a required property")),
    ({"order": [], "a/b~c": True}, ValidationFinding(
        "/tools/dialects/preference/by_model/main/a~1b~0c", "additionalProperties",
        "Additional property 'a/b~c' is not allowed")),
    ({"order": 7}, ValidationFinding(
        "/tools/dialects/preference/by_model/main/order", "type",
        'Value must have type "array"')),
])
def test_compatible_one_of_branch_exposes_leaf_findings(
    value: object, finding: ValidationFinding
) -> None:
    tools = {"dialects": {"preference": {"by_model": {"main": value}}}}
    assert validate_harness_definition(_definition(tools=tools)) == (finding,)
@pytest.mark.parametrize(("value", "code"), [
    (b"x", "json_type"), (bytearray(b"x"), "json_type"), ((1,), "json_type"),
    (range(1), "json_type"), ({1}, "json_type"), (date(2026, 1, 1), "json_type"),
    (object(), "json_type"), (float("nan"), "finite"), (float("inf"), "finite"), (float("-inf"), "finite"),
    pytest.param(10**5000, "integer_range", id="oversized-integer"), ({1: "value"}, "json_key")])
def test_json_domain_preflight_rejects_adversarial_values(
    value: object, code: str
) -> None:
    message = {"json_type": "Value is not a JSON-domain value",
               "finite": "JSON numbers must be finite",
               "integer_range": "JSON integers must contain at most 640 decimal digits",
               "json_key": "Object keys must be strings"}[code]
    assert validate_harness_definition(_definition(dossier={"bad": value})) == (
        ValidationFinding("/dossier/bad", code, message),)
def test_json_domain_preflight_reports_cycle_back_edge() -> None: cycle: list[object] = []; cycle.append(cycle); assert validate_harness_definition(_definition(dossier={"bad": cycle})) == (ValidationFinding("/dossier/bad/0", "json_cycle", "JSON values must not contain cycles"),)
def test_supported_json_boundaries_are_stable(request: pytest.FixtureRequest) -> None:
    accepted, rejected = (json.loads("[" * depth + "null" + "]" * depth) for depth in (98, 99))
    original = sys.get_int_max_str_digits(); request.addfinalizer(lambda: sys.set_int_max_str_digits(original))
    sys.set_int_max_str_digits(640)
    for integer in (10**640 - 1, -(10**640 - 1)): assert json.loads(HarnessDefinition.from_mapping(_definition(dossier={"bad": integer})).canonical_json())["dossier"]["bad"] == integer
    for integer in (10**640, -(10**640)): assert validate_harness_definition(_definition(dossier={"bad": integer})) == (ValidationFinding("/dossier/bad", "integer_range", "JSON integers must contain at most 640 decimal digits"),)
    assert json.loads(HarnessDefinition.from_mapping(_definition(dossier={"bad": accepted})).canonical_json())["dossier"]["bad"] == accepted
    assert validate_harness_definition(_definition(dossier={"bad": rejected})) == (ValidationFinding("/dossier/bad" + "/0" * 99, "json_depth", "JSON paths must not exceed 100 segments"),)
@pytest.mark.parametrize(("schema_version", "version", "expected"), [
    ("bb.unknown.v9", 9, [("/schema_version", "unsupported_schema_version"),
                          ("/version", "unsupported_version")]),
    ("bb.harness_definition.v1", 2, [("/version", "unsupported_version")]),
    ("bb.agent_config_surface.v2", 1, [("/version", "unsupported_version")]),
    pytest.param("bb.harness_definition.v1", 10**5000, [("/version", "integer_range")], id="oversized-version"),
    ("bb.harness_definition.v1", "1", [("/version", "unsupported_version")])])
def test_unsupported_and_mismatched_source_pairs_fail_closed(
    schema_version: object, version: object, expected: list[tuple[str, str]]
) -> None:
    assert _pairs(_definition(schema_version=schema_version, version=version)) == expected
def test_missing_discriminators_fail_before_schema_validation() -> None:
    document = _definition(unknown=True)
    del document["schema_version"]
    del document["version"]
    assert _pairs(document) == [
        ("/schema_version", "required"), ("/version", "required")
    ]
def test_loading_yaml_requires_a_mapping_root(tmp_path: Path) -> None:
    path = tmp_path / "not-a-mapping.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(HarnessDefinitionValidationError) as raised:
        load_harness_definition(path)
    assert raised.value.findings == (
        ValidationFinding("/", "type", "Harness definition must be a mapping"),
    )
def test_loading_valid_yaml_returns_canonical_model(tmp_path: Path) -> None:
    path = tmp_path / "harness.yaml"
    path.write_text(
        """schema_version: bb.harness_definition.v1
version: 1
workspace: {root: .}
providers: {default_model: main, models: [{id: main, adapter: openai}]}
modes: [{name: build}]
loop: {sequence: [{mode: build}]}
""",
        encoding="utf-8",
    )
    loaded = load_harness_definition(path)
    assert isinstance(loaded, HarnessDefinition)
    assert loaded["schema_version"] == "bb.harness_definition.v1"
def test_findings_are_immutable_and_orderable() -> None:
    later, earlier = ValidationFinding("/z", "type", "later"), ValidationFinding("/a", "required", "earlier")
    assert sorted((later, earlier)) == [earlier, later]
    with pytest.raises(FrozenInstanceError): earlier.pointer = "/changed"  # type: ignore[misc]
