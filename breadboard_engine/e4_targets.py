from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from importlib.resources.abc import Traversable
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from breadboard.product.harness import validate as _harness_validation_module


_DISTRIBUTION_NAME = "breadboard-harness-cli"
_LOADER_PATH = "breadboard_engine/e4_targets.py"
_RESOURCE_DIRECTORY = "config/e4_targets"
_INDEX_FILE = "index.json"
_INDEX_SCHEMA = "bb.e4.target_index.v1"
_TARGET_SCHEMA = "bb.e4.target.v1"
_TARGET_V2_SCHEMA = "bb.e4.target.v2"


class E4TargetError(ValueError):
    """Raised when an E4 target package is missing, unsafe, or corrupt."""


@dataclass(frozen=True)
class E4TargetPackage:
    target_id: str
    descriptor: Mapping[str, Any]
    descriptor_path: str
    descriptor_bytes: bytes
    descriptor_sha256: str
    index_bytes: bytes
    assets: Mapping[str, bytes]

    def read_asset_bytes(self, relative_path: str) -> bytes:
        try:
            return self.assets[relative_path]
        except KeyError:
            raise E4TargetError(
                f"target {self.target_id!r} does not declare asset {relative_path!r}"
            ) from None

    def read_asset_text(self, relative_path: str) -> str:
        try:
            return self.read_asset_bytes(relative_path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise E4TargetError(
                f"target {self.target_id!r} asset {relative_path!r} is not UTF-8"
            ) from exc


def list_e4_target_ids() -> tuple[str, ...]:
    index = _load_index(_resource_root())
    return tuple(sorted(index["targets"]))


def load_e4_target(target_id: str) -> E4TargetPackage:
    return _load_e4_target_from_root(_resource_root(), target_id)


def _resource_root() -> Traversable:
    loader_location = _location_key(__file__)
    for distribution in metadata.distributions(name=_DISTRIBUTION_NAME):
        candidate = distribution.locate_file(_LOADER_PATH)
        if _location_key(candidate) == loader_location:
            return distribution.locate_file(_RESOURCE_DIRECTORY)
        editable_root = _editable_source_root(distribution)
        if editable_root is not None and (
            _location_key(editable_root / _LOADER_PATH) == loader_location
        ):
            return editable_root / _RESOURCE_DIRECTORY
    raise E4TargetError(
        f"could not locate {_RESOURCE_DIRECTORY!r} in the distribution "
        f"that owns {loader_location!r}"
    )


def _editable_source_root(distribution: metadata.Distribution) -> Path | None:
    try:
        raw = distribution.read_text("direct_url.json")
    except (OSError, UnicodeError):
        return None
    if raw is None:
        return None
    try:
        direct_url = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(direct_url, dict):
        return None
    directory_info = direct_url.get("dir_info")
    url = direct_url.get("url")
    if (
        not isinstance(directory_info, dict)
        or directory_info.get("editable") is not True
        or not isinstance(url, str)
    ):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    source_root = Path(url2pathname(parsed.path))
    return source_root if source_root.is_absolute() else None


def _location_key(location: object) -> str:
    return os.path.abspath(str(location))


def _load_e4_target_from_root(
    root: Traversable | Path, target_id: str
) -> E4TargetPackage:
    return read_e4_target(
        target_id,
        read_resource=lambda path: _read_bytes(_join_safe(root, path), path),
    )


def read_e4_target(
    target_id: str, *, read_resource: Callable[[str], bytes]
) -> E4TargetPackage:
    """Verify one target from immutable indexed bytes, without locating a distribution."""

    def read_member(path: str) -> bytes:
        content = read_resource(path)
        if not isinstance(content, bytes):
            raise E4TargetError(f"target resource {path!r} must be immutable bytes")
        return content

    index_bytes = read_member(_INDEX_FILE)
    index = _decode_index(index_bytes)
    targets = index["targets"]
    entry = targets.get(target_id)
    if entry is None:
        available = ", ".join(sorted(targets))
        raise E4TargetError(f"unknown E4 target {target_id!r}; available: {available}")
    if not isinstance(entry, dict):
        raise E4TargetError(f"index entry for {target_id!r} must be an object")

    descriptor_path = _required_string(
        entry, "descriptor", f"index entry {target_id!r}"
    )
    descriptor_sha256 = _required_sha256(entry, "sha256", f"index entry {target_id!r}")
    descriptor_parts = _validate_relative_path(descriptor_path)
    descriptor_bytes = read_member(descriptor_path)
    _verify_sha256(descriptor_bytes, descriptor_sha256, descriptor_path)
    descriptor = _decode_json_object(descriptor_bytes, descriptor_path)

    descriptor_schema = descriptor.get("schema_version")
    if descriptor_schema not in (_TARGET_SCHEMA, _TARGET_V2_SCHEMA):
        raise E4TargetError(
            f"{descriptor_path} schema_version must be one of "
            f"{_TARGET_SCHEMA!r} or {_TARGET_V2_SCHEMA!r}"
        )
    if descriptor.get("target_id") != target_id:
        raise E4TargetError(
            f"{descriptor_path} target_id does not match index key {target_id!r}"
        )
    if descriptor_schema == _TARGET_V2_SCHEMA:
        _raise_validation_findings(
            _harness_validation_module.validate_e4_target_document(descriptor),
            descriptor_path,
        )

    descriptor_parent_parts = descriptor_parts[:-1]
    assets = descriptor.get("assets")
    if not isinstance(assets, list) or not assets:
        raise E4TargetError(f"{descriptor_path} assets must be a non-empty array")

    declared_paths: set[str] = set()
    verified_assets: dict[str, bytes] = {}
    for position, asset in enumerate(assets):
        context = f"{descriptor_path} assets[{position}]"
        if not isinstance(asset, dict):
            raise E4TargetError(f"{context} must be an object")
        asset_path = _required_string(asset, "path", context)
        asset_parts = _validate_relative_path(asset_path)
        if asset_path in declared_paths:
            raise E4TargetError(
                f"{descriptor_path} declares duplicate asset {asset_path!r}"
            )
        declared_paths.add(asset_path)
        expected_digest = _required_sha256(
            asset, "sha256", context,
            prefix="sha256:" if descriptor_schema == _TARGET_V2_SCHEMA else "",
        )
        asset_bytes = read_member("/".join((*descriptor_parent_parts, *asset_parts)))
        _verify_sha256(asset_bytes, expected_digest, f"{descriptor_path}:{asset_path}")
        expected_bytes = asset.get("bytes")
        if type(expected_bytes) is not int or expected_bytes < 0:
            raise E4TargetError(f"{context}.bytes must be a non-negative integer")
        if len(asset_bytes) != expected_bytes:
            raise E4TargetError(
                f"{descriptor_path}:{asset_path} size mismatch: "
                f"expected {expected_bytes}, got {len(asset_bytes)}"
            )
        verified_assets[asset_path] = asset_bytes

    execution = descriptor.get("execution")
    if not isinstance(execution, dict) or not execution:
        raise E4TargetError(f"{descriptor_path} execution must be a non-empty object")
    for key, value in execution.items():
        if not key.endswith("_asset"):
            continue
        if not isinstance(value, str) or value not in declared_paths:
            raise E4TargetError(
                f"{descriptor_path} execution.{key} must name a declared asset"
            )
    if descriptor_schema == _TARGET_V2_SCHEMA:
        _validate_v2_configuration(
            descriptor,
            descriptor_path,
            declared_paths,
            verified_assets,
        )

    return E4TargetPackage(
        target_id=target_id,
        descriptor=_freeze_json(descriptor),
        descriptor_path=descriptor_path,
        descriptor_bytes=descriptor_bytes,
        descriptor_sha256=descriptor_sha256,
        index_bytes=index_bytes,
        assets=MappingProxyType(verified_assets),
    )


def _raise_validation_findings(
    findings: tuple[_harness_validation_module.ValidationFinding, ...], label: str
) -> None:
    if not findings:
        return
    finding = findings[0]
    pointer = finding.pointer
    code = finding.code
    message = finding.message
    if code == "additionalProperties":
        raise E4TargetError(f"{label} contains undeclared field at {pointer}")
    if code == "required":
        raise E4TargetError(f"{label} is missing required field at {pointer}")
    raise E4TargetError(f"{label}{pointer}: {message}")


def _decode_yaml_object(content: bytes, label: str) -> dict[str, Any]:
    from breadboard_engine.compilation.contracts import ConfigCompileError
    from breadboard_engine.compilation.server_compiler import strict_parse_payload

    try:
        return strict_parse_payload(content, logical_path=label)
    except ConfigCompileError as exc:
        raise E4TargetError(f"target resource {label!r} is not valid YAML: {exc.code.value}") from exc


def _validate_v2_value_schema_relations(
    schema: Mapping[str, Any],
    context: str,
) -> None:
    properties = schema.get("properties")
    required = schema.get("required")
    if isinstance(properties, Mapping):
        if isinstance(required, list) and not set(required) <= set(properties):
            raise E4TargetError(
                f"{context}.required references an undeclared property"
            )
        for name, property_schema in properties.items():
            _validate_v2_value_schema_relations(
                property_schema,
                f"{context}.properties[{name!r}]",
            )
    items = schema.get("items")
    if isinstance(items, Mapping):
        _validate_v2_value_schema_relations(items, f"{context}.items")




def _validate_v2_input_relations(config: Mapping[str, Any], label: str) -> set[str]:
    inputs = config["inputs"]
    fields = inputs["fields"]
    names: list[str] = []
    for position, field in enumerate(fields):
        name = field["name"]
        if name in names:
            raise E4TargetError(f"{label}.inputs.fields has duplicate name {name!r}")
        names.append(name)
        _validate_v2_value_schema_relations(
            field["value_schema"],
            f"{label}.inputs.fields[{position}].value_schema",
        )
    order = inputs["order"]
    if set(order) != set(names):
        raise E4TargetError(f"{label}.inputs.order must cover every declared field")
    return set(names)


def _validate_v2_materialization_relations(
    config: Mapping[str, Any],
    label: str,
    declared_paths: set[str],
) -> None:
    materialization = config["materialization"]
    paths: list[str] = []
    for position, asset in enumerate(materialization["assets"]):
        asset_context = f"{label}.materialization.assets[{position}]"
        path = asset["path"]
        _validate_relative_path(path)
        if path in paths:
            raise E4TargetError(
                f"{label}.materialization.assets has duplicate path {path!r}"
            )
        paths.append(path)
        if path not in declared_paths:
            raise E4TargetError(
                f"{asset_context}.path must name a declared package asset"
            )
    if set(materialization["order"]) != set(paths):
        raise E4TargetError(
            f"{label}.materialization.order must cover every materialized asset"
        )


def _validate_v2_configuration(
    descriptor: Mapping[str, Any],
    descriptor_path: str,
    declared_paths: set[str],
    verified_assets: Mapping[str, bytes],
) -> None:
    config_asset = descriptor["execution"]["config_asset"]
    config_label = f"{descriptor_path}:{config_asset}"
    config = _decode_yaml_object(verified_assets[config_asset], config_label)
    _raise_validation_findings(
        _harness_validation_module.validate_e4_target_document(config),
        config_label,
    )
    if config["target_id"] != descriptor["target_id"]:
        raise E4TargetError(
            f"{config_label}.target_id does not match descriptor target_id"
        )
    prompt = config["prompt"]
    execution = descriptor["execution"]
    if (
        prompt.get("asset") != execution.get("system_prompt_asset")
        or prompt.get("source") != execution.get("system_prompt_source")
    ):
        raise E4TargetError(
            f"{config_label}.prompt must match the declared system prompt asset or source"
        )
    input_names = _validate_v2_input_relations(config, config_label)
    if not set(prompt["dynamic_fields"]) <= input_names:
        raise E4TargetError(
            f"{config_label}.prompt.dynamic_fields contains an undeclared input"
        )
    tools = config["tools"]
    if tools["surface_asset"] != descriptor["execution"]["tool_surface_asset"]:
        raise E4TargetError(
            f"{config_label}.tools.surface_asset must match the declared tool "
            "surface asset"
        )
    _validate_v2_materialization_relations(config, config_label, declared_paths)

def _load_index(root: Traversable | Path) -> dict[str, Any]:
    return _decode_index(_read_bytes(root.joinpath(_INDEX_FILE), _INDEX_FILE))



def _decode_index(content: bytes) -> dict[str, Any]:
    index = _decode_json_object(content, _INDEX_FILE)
    if index.get("schema_version") != _INDEX_SCHEMA:
        raise E4TargetError(f"{_INDEX_FILE} schema_version must be {_INDEX_SCHEMA!r}")
    targets = index.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise E4TargetError(f"{_INDEX_FILE} targets must be a non-empty object")
    if any(not isinstance(target_id, str) or not target_id for target_id in targets):
        raise E4TargetError(f"{_INDEX_FILE} target IDs must be non-empty strings")
    return index


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _join_safe(root: Traversable | Path, relative_path: str) -> Traversable | Path:
    return _join_parts(root, _validate_relative_path(relative_path))


def _join_parts(root: Traversable | Path, parts: tuple[str, ...]) -> Traversable | Path:
    resource = root
    for part in parts:
        resource = resource.joinpath(part)
    return resource


def _validate_relative_path(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path:
        raise E4TargetError("target resource path must be a non-empty string")
    if (
        relative_path.startswith("/")
        or "\\" in relative_path
        or ":" in relative_path
    ):
        raise E4TargetError(f"unsafe target resource path {relative_path!r}")
    parts = tuple(relative_path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise E4TargetError(f"unsafe target resource path {relative_path!r}")
    return parts


def _read_bytes(resource: Traversable | Path, label: str) -> bytes:
    try:
        if not resource.is_file():
            raise E4TargetError(f"target resource {label!r} is missing or not a file")
        return resource.read_bytes()
    except E4TargetError:
        raise
    except OSError as exc:
        raise E4TargetError(f"could not read target resource {label!r}: {exc}") from exc


def _decode_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E4TargetError(f"target resource {label!r} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise E4TargetError(f"target resource {label!r} must contain a JSON object")
    return value


def _required_string(value: dict[str, Any], key: str, context: str) -> str:
    field = value.get(key)
    if not isinstance(field, str) or not field:
        raise E4TargetError(f"{context}.{key} must be a non-empty string")
    return field


def _required_sha256(
    value: dict[str, Any], key: str, context: str, *, prefix: str = ""
) -> str:
    digest = _required_string(value, key, context)
    if not digest.startswith(prefix):
        raise E4TargetError(f"{context}.{key} must use the {prefix!r} prefix")
    value_digest = digest[len(prefix):]
    if len(value_digest) != 64 or any(
        character not in "0123456789abcdef" for character in value_digest
    ):
        raise E4TargetError(f"{context}.{key} must be a lowercase SHA-256 digest")
    return value_digest


def _verify_sha256(content: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise E4TargetError(
            f"target resource {label!r} SHA-256 mismatch: expected {expected}, got {actual}"
        )
