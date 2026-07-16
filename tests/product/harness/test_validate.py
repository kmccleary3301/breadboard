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
def _preference(value: object) -> dict[str, object]:
    return {"tools": {"dialects": {"preference": {"by_model": {"main": value}}}}}
def test_public_schema_reuses_each_v2_property_grammar() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "https://breadboard.dev/contracts/public/schemas/bb.harness_definition.v1.schema.json"
    assert schema["required"] == [
        "schema_version", "version", "workspace", "providers", "modes", "loop"
    ]
    assert schema["properties"]["schema_version"] == {"const": "bb.harness_definition.v1"}
    assert schema["properties"]["version"] == {"const": 1}
    for name, property_schema in schema["properties"].items():
        if name not in {"schema_version", "version"}:
            assert property_schema == {"$ref": f"{V2_SCHEMA_ID}#/properties/{name}"}
    surface = json.dumps(schema, sort_keys=True).casefold()
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
    (_preference({}), ValidationFinding(
        "/tools/dialects/preference/by_model/main/order", "required",
        "'order' is a required property")),
    (_preference({"order": [], "a/b~c": True}), ValidationFinding(
        "/tools/dialects/preference/by_model/main/a~1b~0c", "additionalProperties",
        "Additional property 'a/b~c' is not allowed")),
    (_preference({"order": 7}), ValidationFinding(
        "/tools/dialects/preference/by_model/main/order", "type",
        'Value must have type "array"')),
])
def test_schema_findings_have_project_owned_exact_messages(
    updates: dict[str, object], finding: ValidationFinding
) -> None:
    document = _definition(**updates)
    assert validate_harness_definition(document) == (finding,)
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
def test_json_domain_preflight_reports_cycle_back_edge() -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    expected = ValidationFinding(
        "/dossier/bad/0", "json_cycle", "JSON values must not contain cycles")
    assert validate_harness_definition(_definition(dossier={"bad": cycle})) == (expected,)
def test_integer_boundary_is_host_stable(request: pytest.FixtureRequest) -> None:
    original = sys.get_int_max_str_digits()
    request.addfinalizer(lambda: sys.set_int_max_str_digits(original))
    sys.set_int_max_str_digits(640)
    maximum = 10**640 - 1
    for integer in (maximum, -maximum):
        definition = HarnessDefinition.from_mapping(_definition(dossier={"bad": integer}))
        assert json.loads(definition.canonical_json())["dossier"]["bad"] == integer
    expected = ValidationFinding(
        "/dossier/bad", "integer_range",
        "JSON integers must contain at most 640 decimal digits")
    for integer in (maximum + 1, -(maximum + 1)):
        assert validate_harness_definition(_definition(dossier={"bad": integer})) == (expected,)
def test_json_depth_boundary_is_stable() -> None:
    accepted, rejected = (
        json.loads("[" * depth + "null" + "]" * depth) for depth in (98, 99))
    definition = HarnessDefinition.from_mapping(_definition(dossier={"bad": accepted}))
    assert json.loads(definition.canonical_json())["dossier"]["bad"] == accepted
    expected = ValidationFinding(
        "/dossier/bad" + "/0" * 99, "json_depth",
        "JSON paths must not exceed 100 segments")
    assert validate_harness_definition(_definition(dossier={"bad": rejected})) == (expected,)
    very_deep: object = None
    for _ in range(1200):
        very_deep = [very_deep]
    with pytest.raises(HarnessDefinitionValidationError) as raised:
        HarnessDefinition.from_mapping(_definition(dossier={"bad": very_deep}))
    assert raised.value.findings[0].code == "json_depth"
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
def test_discriminator_messages_are_canonical_for_equivalent_mappings() -> None:
    first = _definition(schema_version={"alpha": 1, "zeta": 2})
    second = _definition(schema_version={"zeta": 2, "alpha": 1})
    expected = ValidationFinding(
        "/schema_version", "unsupported_schema_version",
        "Unsupported schema_version; expected one of 'bb.agent_config_surface.v2', 'bb.harness_definition.v1'")
    assert validate_harness_definition(first) == validate_harness_definition(second) == (expected,)
def test_missing_discriminators_fail_before_schema_validation() -> None:
    document = _definition(unknown=True)
    del document["schema_version"]
    del document["version"]
    assert _pairs(document) == [
        ("/schema_version", "required"), ("/version", "required")
    ]
def test_findings_are_immutable_and_orderable() -> None:
    later, earlier = ValidationFinding("/z", "type", "later"), ValidationFinding("/a", "required", "earlier")
    assert sorted((later, earlier)) == [earlier, later]
    with pytest.raises(FrozenInstanceError):
        earlier.pointer = "/changed"  # type: ignore[misc]
