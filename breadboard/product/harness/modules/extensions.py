from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, TypeAlias
from ..validate import parse_harness_definition

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = (
    JsonScalar | Mapping[str, "JsonValue"] | list["JsonValue"] | tuple["JsonValue", ...]
)
Validator: TypeAlias = Callable[[Mapping[str, Any]], None]
_ROOTS = frozenset(
    "completion concurrency dossier enhanced_tools features guardrails long_running loop modes multi_agent permissions prompts provider_tools providers replay schema_version tools turn_strategy version workspace".split()
)
_PROTECTED = frozenset(("schema_version", "version", "dossier"))
_MAX_JSON_INTEGER = 10**640 - 1
_OWNER_KEY = object()


class CompositionError(ValueError):
    """A contribution cannot be composed without ambiguity or invalid output."""


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


def _copy_json(
    value: Any, freeze: bool, active: set[int] | None = None, depth: int = 0
) -> Any:
    if depth > 100:
        raise CompositionError("JSON paths must not exceed 100 segments")
    active = set() if active is None else active
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise CompositionError("cyclic JSON value")
        if any(type(key) is not str for key in value):
            raise CompositionError("JSON object keys must be strings")
        active.add(identity)
        try:
            result = {
                key: _copy_json(item, freeze, active, depth + 1)
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
        return MappingProxyType(result) if freeze else result
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise CompositionError("cyclic JSON value")
        active.add(identity)
        try:
            result = [_copy_json(item, freeze, active, depth + 1) for item in value]
        finally:
            active.remove(identity)
        return tuple(result) if freeze else result
    kind = type(value)
    if (
        value is None
        or kind in (bool, str)
        or kind is int
        and abs(value) <= _MAX_JSON_INTEGER
        or kind is float
        and math.isfinite(value)
    ):
        return value
    raise CompositionError(f"unsupported JSON value: {kind.__name__}")


@dataclass(frozen=True, slots=True)
class Operation:
    kind: Literal["remove", "replace", "add"]
    path: tuple[str, ...]
    value: JsonValue | _Missing = field(default=_MISSING, repr=False)

    def __post_init__(self) -> None:
        if self.kind not in ("remove", "replace", "add"):
            raise CompositionError(f"unknown operation kind: {self.kind!r}")
        if isinstance(self.path, (str, bytes)):
            raise CompositionError("operation path must be a sequence")
        try:
            path = tuple(self.path)
        except TypeError:
            raise CompositionError("operation path must be a sequence") from None
        if not path or any(type(part) is not str or not part for part in path):
            raise CompositionError("operation path must contain non-empty strings")
        object.__setattr__(self, "path", path)
        if self.kind == "remove":
            if self.value is not _MISSING:
                raise CompositionError("remove operation cannot have a value")
        elif self.value is _MISSING:
            raise CompositionError(f"{self.kind} operation requires a value")
        else:
            object.__setattr__(self, "value", _copy_json(self.value, True))


@dataclass(frozen=True, slots=True)
class ModuleContribution:
    module_id: str
    precedence: int
    operations: tuple[Operation, ...]
    validator: Validator = field(default=lambda _: None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.module_id) is not str or not self.module_id:
            raise CompositionError("module_id must be a non-empty string")
        if type(self.precedence) is not int:
            raise CompositionError("module precedence must be an integer")
        try:
            operations = tuple(self.operations)
        except TypeError:
            raise CompositionError("module operations must be a sequence") from None
        if any(not isinstance(item, Operation) for item in operations):
            raise CompositionError("module operations must be Operation values")
        keys = [(item.kind, item.path) for item in operations]
        if len(keys) != len(set(keys)):
            raise CompositionError("module operations must not repeat a kind and path")
        if not callable(self.validator):
            raise CompositionError("module validator must be callable")
        object.__setattr__(self, "operations", operations)


class _OwnedContribution(ModuleContribution):
    __slots__ = ()

    def __init__(self, *args: Any, _key: object | None = None, **kwargs: Any) -> None:
        if _key is not _OWNER_KEY:
            raise CompositionError("owned contributions require internal capability")
        super().__init__(*args, **kwargs)


def _owned(
    module_id: str,
    precedence: int,
    operations: Sequence[Operation],
    roots: frozenset[str],
    validator: Validator,
) -> ModuleContribution:
    snapshot = tuple(operations)
    if any(
        not isinstance(item, Operation) or item.path[0] not in roots
        for item in snapshot
    ):
        raise CompositionError(f"{module_id} module operation targets an unowned root")
    return _OwnedContribution(
        f"{module_id}:{precedence}",
        precedence,
        snapshot,
        validator,
        _key=_OWNER_KEY,
    )


def _path(path: tuple[str, ...]) -> str:
    return "/" + "/".join(item.replace("~", "~0").replace("/", "~1") for item in path)


def _apply(document: dict[str, Any], operation: Operation) -> None:
    parent = document
    for index, item in enumerate(operation.path[:-1]):
        if item not in parent:
            raise CompositionError(
                f"operation parent does not exist: {_path(operation.path[:index + 1])}"
            )
        child = parent[item]
        if not isinstance(child, dict):
            raise CompositionError(
                f"operation crosses non-object: {_path(operation.path[:index + 1])}"
            )
        parent = child
    target, present = operation.path[-1], operation.path[-1] in parent
    if operation.kind == "add" and present:
        raise CompositionError(f"add target already exists: {_path(operation.path)}")
    if operation.kind != "add" and not present:
        raise CompositionError(
            f"{operation.kind} target does not exist: {_path(operation.path)}"
        )
    if operation.kind == "remove":
        del parent[target]
    else:
        parent[target] = _copy_json(operation.value, False)


def compose_modules(
    base: Mapping[str, Any], modules: Sequence[ModuleContribution]
) -> dict[str, Any]:
    if not isinstance(base, Mapping):
        raise CompositionError("base harness must be a mapping")
    document = _copy_json(base, False)
    if (
        document.get("schema_version") != "bb.harness_definition.v1"
        or type(document.get("version")) is not int
        or document["version"] != 1
    ):
        raise CompositionError(
            "base harness must use bb.harness_definition.v1 version 1"
        )
    extra = sorted(set(document) - _ROOTS)
    if extra:
        raise CompositionError(f"base contains non-canonical V1 root: {extra[0]!r}")
    try:
        snapshot = tuple(modules)
    except TypeError:
        raise CompositionError("modules must be a sequence") from None
    if any(not isinstance(item, _OwnedContribution) for item in snapshot):
        raise CompositionError("modules must be owned module contributions")
    precedences = [item.precedence for item in snapshot]
    if len(precedences) != len(set(precedences)):
        raise CompositionError("duplicate module precedence")
    ordered = sorted(snapshot, key=lambda item: (item.precedence, item.module_id))
    for module in ordered:
        for operation in module.operations:
            root = operation.path[0]
            if root not in _ROOTS:
                raise CompositionError(f"operation root is not canonical V1: {root!r}")
            if root in _PROTECTED:
                raise CompositionError(
                    f"operation cannot change protected root: {root!r}"
                )
            _apply(document, operation)
    final = _copy_json(document, True)
    for module in ordered:
        try:
            module.validator(final)
        except Exception as error:
            raise CompositionError(
                f"validator for module {module.module_id!r} failed: {error}"
            ) from None
    try:
        return parse_harness_definition(document).as_dict()
    except Exception as error:
        raise CompositionError(f"composed harness is invalid: {error}") from None


class LocalExtensionRegistry:

    def __init__(self) -> None:
        self._builders: dict[str, Callable[[Mapping[str, Any]], Any]] = {}

    def register(
        self, extension_id: str, builder: Callable[[Mapping[str, Any]], Any]
    ) -> None:
        if type(extension_id) is not str or not extension_id:
            raise CompositionError("extension id must be a non-empty string")
        if not callable(builder):
            raise CompositionError("extension builder must be callable")
        if extension_id in self._builders:
            raise CompositionError(f"duplicate extension id: {extension_id!r}")
        self._builders[extension_id] = builder

    def resolve(
        self, extension_id: str, config: Mapping[str, Any], precedence: int = 100
    ) -> ModuleContribution:
        if type(extension_id) is not str or not extension_id:
            raise CompositionError("extension id must be a non-empty string")
        if type(precedence) is not int:
            raise CompositionError("module precedence must be an integer")
        if extension_id not in self._builders:
            raise CompositionError(f"unknown extension id: {extension_id!r}")
        if not isinstance(config, Mapping):
            raise CompositionError("extension config must be a mapping")
        try:
            built = self._builders[extension_id](_copy_json(config, False))
        except Exception as error:
            raise CompositionError(
                f"extension builder {extension_id!r} failed: {error}"
            ) from None
        if not isinstance(built, _OwnedContribution):
            raise CompositionError(
                f"extension builder {extension_id!r} must return an owned contribution"
            )
        return _OwnedContribution(
            f"extension:{extension_id}:{precedence}",
            precedence,
            built.operations,
            built.validator,
            _key=_OWNER_KEY,
        )
