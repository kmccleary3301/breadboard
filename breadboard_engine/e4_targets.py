from __future__ import annotations

from collections.abc import Mapping
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


_DISTRIBUTION_NAME = "breadboard-harness-cli"
_LOADER_PATH = "breadboard_engine/e4_targets.py"
_RESOURCE_DIRECTORY = "config/e4_targets"
_INDEX_FILE = "index.json"
_INDEX_SCHEMA = "bb.e4.target_index.v1"
_TARGET_SCHEMA = "bb.e4.target.v1"


class E4TargetError(ValueError):
    """Raised when an E4 target package is missing, unsafe, or corrupt."""


@dataclass(frozen=True)
class E4TargetPackage:
    target_id: str
    descriptor: Mapping[str, Any]
    _assets: Mapping[str, bytes]

    def read_asset_bytes(self, relative_path: str) -> bytes:
        try:
            return self._assets[relative_path]
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
    index = _load_index(root)
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
    descriptor_resource = _join_parts(root, descriptor_parts)
    descriptor_bytes = _read_bytes(descriptor_resource, descriptor_path)
    _verify_sha256(descriptor_bytes, descriptor_sha256, descriptor_path)
    descriptor = _decode_json_object(descriptor_bytes, descriptor_path)

    if descriptor.get("schema_version") != _TARGET_SCHEMA:
        raise E4TargetError(
            f"{descriptor_path} schema_version must be {_TARGET_SCHEMA!r}"
        )
    if descriptor.get("target_id") != target_id:
        raise E4TargetError(
            f"{descriptor_path} target_id does not match index key {target_id!r}"
        )

    descriptor_parent = _join_parts(root, descriptor_parts[:-1])
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
        _validate_relative_path(asset_path)
        if asset_path in declared_paths:
            raise E4TargetError(
                f"{descriptor_path} declares duplicate asset {asset_path!r}"
            )
        declared_paths.add(asset_path)
        expected_digest = _required_sha256(asset, "sha256", context)
        asset_bytes = _read_bytes(
            _join_safe(descriptor_parent, asset_path),
            f"{descriptor_path}:{asset_path}",
        )
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

    return E4TargetPackage(
        target_id=target_id,
        descriptor=_freeze_json(descriptor),
        _assets=MappingProxyType(verified_assets),
    )


def _load_index(root: Traversable | Path) -> dict[str, Any]:
    index = _decode_json_object(
        _read_bytes(root.joinpath(_INDEX_FILE), _INDEX_FILE), _INDEX_FILE
    )
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


def _required_sha256(value: dict[str, Any], key: str, context: str) -> str:
    digest = _required_string(value, key, context)
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise E4TargetError(f"{context}.{key} must be a lowercase SHA-256 digest")
    return digest


def _verify_sha256(content: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise E4TargetError(
            f"target resource {label!r} SHA-256 mismatch: expected {expected}, got {actual}"
        )
