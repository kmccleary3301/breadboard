from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

import yaml
from jsonschema.exceptions import SchemaError

from breadboard.product.harness.compile import (
    HarnessCompilation,
    HarnessReferenceMissingError,
)
from breadboard.product.harness.resolution import (
    HarnessContainmentError,
    HarnessResourceInvalidError,
    resolve_contained_target,
    compile_harness_source,
)
from breadboard.product.harness.templates import (
    DAILY_DRIVER_TEMPLATE_NAME,
    daily_driver_template_path,
)
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError,
)

DEFAULT_PROFILE_ID = Path(DAILY_DRIVER_TEMPLATE_NAME).stem
DEFAULT_PROFILE_DEFINITION_REF = (
    Path("agent_configs/templates") / DAILY_DRIVER_TEMPLATE_NAME
).as_posix()


class DefaultProfileResolutionError(RuntimeError):
    error_code = "default_profile_invalid"
    exit_code = 2
    hint = "Reinstall BreadBoard from a complete trusted distribution, then retry."


class DefaultProfileUnavailableError(DefaultProfileResolutionError):
    error_code = "default_profile_unavailable"
    exit_code = 3


class DefaultProfileInvalidError(DefaultProfileResolutionError):
    pass


@dataclass(frozen=True, slots=True)
class DefaultProfileResolution:
    """Immutable internal and portable authority for the packaged profile."""

    source_path: Path
    compilation: HarnessCompilation
    identity: Mapping[str, object]

    def public_identity(self) -> dict[str, object]:
        return _thaw_mapping(self.identity)


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[object, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {str(key): _freeze_value(item) for key, item in value.items()}
    )


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _thaw_mapping(value)
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


def _thaw_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return {str(key): _thaw_value(item) for key, item in value.items()}


def _default_profile_source() -> Path:
    profile_path = Path(daily_driver_template_path())
    expected = Path(DEFAULT_PROFILE_DEFINITION_REF)
    if (
        len(profile_path.parts) < len(expected.parts)
        or profile_path.parts[-len(expected.parts) :] != expected.parts
    ):
        raise HarnessContainmentError(
            "bundled daily-driver profile path is not canonical"
        )
    package_root = profile_path.parents[len(expected.parts) - 1]
    return resolve_contained_target(
        profile_path,
        package_root,
        "bundled daily-driver profile",
        strict=True,
    )


def _default_profile_identity(
    compilation: HarnessCompilation,
) -> dict[str, object]:
    lock = compilation.lock
    resources = []
    for layer in lock["source_layers"]:
        if layer["scope"] != "resource":
            continue
        _, separator, declared = str(layer["source_ref"]).partition("::")
        declared_path = Path(declared)
        if (
            not separator
            or not declared
            or declared_path.is_absolute()
            or ".." in declared_path.parts
        ):
            raise DefaultProfileInvalidError(
                "bundled daily-driver profile identity is corrupt"
            )
        resource_ref = Path(
            "agent_configs/templates",
            declared_path,
        ).as_posix()
        resources.append(
            {
                "ref": resource_ref,
                "sha256": str(layer["layer_hash"]),
            }
        )
    explanation = compilation.explanation.as_dict()
    definition = compilation.resolved_author_dict()
    return {
        "profile_id": DEFAULT_PROFILE_ID,
        "definition_ref": DEFAULT_PROFILE_DEFINITION_REF,
        "schema_version": str(definition["schema_version"]),
        "source_sha256": str(explanation["config_sha256"]),
        "effective_lock_schema_version": str(lock["schema_version"]),
        "effective_lock_hash": str(lock["graph_hash"]),
        "resources": sorted(resources, key=lambda row: row["ref"]),
    }


@lru_cache(maxsize=1)
def resolve_default_profile() -> DefaultProfileResolution:
    """Resolve one immutable package-local daily-driver authority."""

    try:
        profile_path = _default_profile_source()
        compilation = compile_harness_source(
            profile_path,
            profile_path.parent,
            contained=True,
        )
        identity = _default_profile_identity(compilation)
        return DefaultProfileResolution(
            source_path=profile_path,
            compilation=compilation,
            identity=_freeze_mapping(identity),
        )
    except (
        HarnessContainmentError,
        HarnessResourceInvalidError,
        IsADirectoryError,
    ) as error:
        raise DefaultProfileInvalidError(
            "bundled daily-driver profile is corrupt"
        ) from error
    except HarnessReferenceMissingError as error:
        raise DefaultProfileUnavailableError(
            "bundled daily-driver profile resources are unavailable"
        ) from error
    except OSError as error:
        raise DefaultProfileUnavailableError(
            "bundled daily-driver profile resources are unavailable"
        ) from error
    except (
        HarnessDefinitionValidationError,
        SchemaError,
        KeyError,
        RuntimeError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        raise DefaultProfileInvalidError(
            "bundled daily-driver profile is corrupt"
        ) from error


def default_profile_identity() -> dict[str, object]:
    """Return a fresh public identity of the packaged default profile."""

    return resolve_default_profile().public_identity()
