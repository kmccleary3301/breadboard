from __future__ import annotations

import json
import re
import sysconfig
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator
from referencing import Registry, Resource

if TYPE_CHECKING:
    from .model import HarnessDefinition

_CANONICAL = ("bb.harness_definition.v1", 1)
_LEGACY = ("bb.agent_config_surface.v2", 2)
_SCHEMA_PATHS = {
    _CANONICAL: Path("contracts/public/schemas/bb.harness_definition.v1.schema.json"),
    _LEGACY: Path("contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json"),
}
_KNOWN_VERSIONS = {
    _CANONICAL[0]: _CANONICAL[1],
    _LEGACY[0]: _LEGACY[1],
}

@dataclass(frozen=True, order=True)
class ValidationFinding:
    pointer: str
    code: str
    message: str


class HarnessDefinitionValidationError(ValueError):
    def __init__(self, findings: Sequence[ValidationFinding]) -> None:
        self.findings = tuple(sorted(set(findings)))
        detail = "; ".join(
            f"{item.pointer} [{item.code}]: {item.message}" for item in self.findings
        )
        super().__init__(detail or "Harness definition validation failed")


def _schema_root() -> Path:
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / _SCHEMA_PATHS[_CANONICAL]).is_file():
        return source_root
    return Path(sysconfig.get_path("data"))


def _load_schema(relative_path: Path) -> dict[str, Any]:
    path = _schema_root() / relative_path
    with path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    if not isinstance(schema, dict):
        raise RuntimeError(f"Schema root must be an object: {path}")
    return schema


def _is_mapping(_checker: object, instance: object) -> bool:
    return isinstance(instance, Mapping)


_MappingDraft202012Validator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("object", _is_mapping),
)


@lru_cache(maxsize=1)
def _schema_validators() -> dict[tuple[str, int], Validator]:
    schemas = {pair: _load_schema(path) for pair, path in _SCHEMA_PATHS.items()}
    resources = []
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str):
            raise RuntimeError("Harness definition schemas must have canonical $id values")
        resources.append((schema_id, Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    return {
        pair: _MappingDraft202012Validator(schema, registry=registry)
        for pair, schema in schemas.items()
    }


def _pointer(path: Sequence[object]) -> str:
    if not path:
        return "/"
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def _required_property(error: ValidationError) -> str | None:
    if not isinstance(error.validator_value, list):
        return None
    missing = [
        name
        for name in error.validator_value
        if isinstance(name, str)
        and isinstance(error.instance, Mapping)
        and name not in error.instance
    ]
    for name in missing:
        if error.message == f"{name!r} is a required property":
            return name
    return missing[0] if len(missing) == 1 else None


def _additional_properties(error: ValidationError) -> list[str]:
    if not isinstance(error.instance, Mapping) or not isinstance(error.schema, Mapping):
        return []
    properties = error.schema.get("properties", {})
    patterns = error.schema.get("patternProperties", {})
    known = properties if isinstance(properties, Mapping) else {}
    regexes = (
        [re.compile(pattern) for pattern in patterns if isinstance(pattern, str)]
        if isinstance(patterns, Mapping)
        else []
    )
    return sorted(
        key
        for key in error.instance
        if isinstance(key, str)
        and key not in known
        and not any(regex.search(key) for regex in regexes)
    )


def _schema_error_findings(error: ValidationError) -> list[ValidationFinding]:
    path, code = tuple(error.absolute_path), str(error.validator)
    if error.validator == "required":
        if missing := _required_property(error):
            return [
                ValidationFinding(
                    _pointer((*path, missing)), code, f"{missing!r} is a required property"
                )
            ]
    if error.validator == "additionalProperties":
        if unexpected := _additional_properties(error):
            return [
                ValidationFinding(
                    _pointer((*path, name)),
                    code,
                    f"Additional property {name!r} is not allowed",
                )
                for name in unexpected
            ]
    return [ValidationFinding(_pointer(path), code, error.message)]


def _source_pair_findings(document: Mapping[str, object]) -> list[ValidationFinding]:
    findings = [
        ValidationFinding(f"/{name}", "required", f"{name!r} is a required property")
        for name in ("schema_version", "version")
        if name not in document
    ]
    if findings:
        return findings
    schema_version, version = document["schema_version"], document["version"]
    expected = (
        _KNOWN_VERSIONS.get(schema_version) if isinstance(schema_version, str) else None
    )
    if expected is None:
        supported = ", ".join(repr(value) for value in sorted(_KNOWN_VERSIONS))
        findings.append(
            ValidationFinding(
                "/schema_version",
                "unsupported_schema_version",
                f"Unsupported schema_version {schema_version!r}; expected one of {supported}",
            )
        )
    if expected is not None and (type(version) is not int or version != expected):
        findings.append(
            ValidationFinding(
                "/version",
                "unsupported_version",
                f"Version {version!r} does not match schema_version "
                f"{schema_version!r}; expected {expected}",
            )
        )
    elif expected is None and not (
        type(version) is int and version in _KNOWN_VERSIONS.values()
    ):
        findings.append(
            ValidationFinding(
                "/version",
                "unsupported_version",
                f"Unsupported version {version!r}; expected integer 1 or 2",
            )
        )
    return findings


def validate_harness_definition(
    document: Mapping[str, object],
) -> tuple[ValidationFinding, ...]:
    if not isinstance(document, Mapping):
        return (ValidationFinding("/", "type", "Harness definition must be a mapping"),)
    if findings := _source_pair_findings(document):
        return tuple(sorted(set(findings)))
    pair = (document["schema_version"], document["version"])
    validator = _schema_validators()[pair]  # type: ignore[index]
    findings = [
        finding
        for error in validator.iter_errors(document)
        for finding in _schema_error_findings(error)
    ]
    return tuple(sorted(set(findings)))


def parse_harness_definition(document: Mapping[str, object]) -> HarnessDefinition:
    if findings := validate_harness_definition(document):
        raise HarnessDefinitionValidationError(findings)
    from .model import HarnessDefinition

    return HarnessDefinition._from_validated(document)


def load_harness_definition(path: str | Path) -> HarnessDefinition:
    with Path(path).open(encoding="utf-8") as definition_file:
        document = yaml.safe_load(definition_file)
    if not isinstance(document, Mapping):
        raise HarnessDefinitionValidationError(
            (ValidationFinding("/", "type", "Harness definition must be a mapping"),)
        )
    return parse_harness_definition(document)
