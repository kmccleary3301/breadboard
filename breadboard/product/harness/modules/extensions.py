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
    if isinstance(value, (Mapping, list, tuple)):
        identity = id(value)
        if identity in active:
            raise CompositionError("cyclic JSON value")
        active.add(identity)
        try:
            if isinstance(value, Mapping):
                if any(type(key) is not str for key in value):
                    raise CompositionError("JSON object keys must be strings")
                result = {
                    key: _copy_json(item, freeze, active, depth + 1)
                    for key, item in value.items()
                }
                return MappingProxyType(result) if freeze else result
            result = [_copy_json(item, freeze, active, depth + 1) for item in value]
            return tuple(result) if freeze else result
        finally:
            active.remove(identity)
    kind = type(value)
    if value is None or kind in (bool, str):
        return value
    if kind is int and abs(value) <= _MAX_JSON_INTEGER:
        return value
    if kind is float and math.isfinite(value):
        return value
    raise CompositionError(f"unsupported JSON value: {kind.__name__}")


@dataclass(frozen=True, slots=True)
class Operation:
    kind: Literal["remove", "replace", "add"]
    path: tuple[str, ...]
    value: JsonValue | _Missing = field(default=_MISSING, repr=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in ("remove", "replace", "add"):
            raise CompositionError(f"unknown operation kind: {self.kind!r}")
        if type(self.path) not in (list, tuple):
            raise CompositionError("operation path must be a sequence")
        path = tuple(self.path)
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


def _snapshot(value: object) -> Operation:
    if type(value) is not Operation:
        raise CompositionError("module operations must be exact Operation values")
    return Operation(value.kind, value.path, value.value)


def _seal(operations: tuple[Operation, ...]) -> str:
    if any(type(item) is not Operation for item in operations):
        raise CompositionError("module operations must be exact Operation values")
    return repr(tuple((item.kind, item.path, item.value) for item in operations))


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
        if type(self.operations) not in (list, tuple):
            raise CompositionError("module operations must be a sequence")
        operations = tuple(_snapshot(item) for item in self.operations)
        keys = [(item.kind, item.path) for item in operations]
        if len(keys) != len(set(keys)):
            raise CompositionError("module operations must not repeat a kind and path")
        if not callable(self.validator):
            raise CompositionError("module validator must be callable")
        object.__setattr__(self, "operations", operations)


_Ops: TypeAlias = list[Operation] | tuple[Operation, ...]
_Mod: TypeAlias = ModuleContribution


class _OwnedContribution(ModuleContribution):
    __slots__ = ("_sealed",)

    def __init__(self, *args: Any, _key: object | None = None, **kwargs: Any) -> None:
        if _key is not _OWNER_KEY:
            raise CompositionError("owned contributions require internal capability")
        super().__init__(*args, **kwargs)
        sealed = _seal(self.operations)
        object.__setattr__(self, "_sealed", (self.module_id, self.precedence, sealed))


_OWNERS: dict[type, tuple[frozenset[str], Validator]] = {}
_OWNED_TYPES: dict[tuple[frozenset[str], Validator], type[_OwnedContribution]] = {}


def _owned(
    module_id: str,
    precedence: int,
    operations: Sequence[Operation],
    roots: frozenset[str],
    validator: Validator,
) -> ModuleContribution:
    authority = (roots, validator)
    owned_type = _OWNED_TYPES.get(authority)
    if owned_type is None:
        owned_type = type("_Owned", (_OwnedContribution,), {"__slots__": ()})
        _OWNED_TYPES[authority] = owned_type
        _OWNERS[owned_type] = authority
    args = (f"{module_id}:{precedence}", precedence, operations, validator)
    return _admit(owned_type(*args, _key=_OWNER_KEY))[0]


def _admit(value: object) -> tuple[ModuleContribution, frozenset[str]]:
    authority = _OWNERS.get(type(value))
    if authority is None:
        raise CompositionError("invalid owned module contribution")
    roots, validator = authority
    if type(value.operations) is not tuple:
        raise CompositionError("owned module operations must be an exact tuple")
    fields = (value.module_id, value.precedence, _seal(value.operations))
    if fields != getattr(value, "_sealed", None):
        raise CompositionError("owned module was mutated after construction")
    operations = tuple(_snapshot(item) for item in value.operations)
    if any(item.path[0] not in roots for item in operations):
        raise CompositionError("owned module operation targets an unowned root")
    args = (value.module_id, value.precedence, operations, validator)
    return type(value)(*args, _key=_OWNER_KEY), roots


def _path(path: tuple[str, ...]) -> str:
    return "/" + "/".join(item.replace("~", "~0").replace("/", "~1") for item in path)


def _apply(document: dict[str, Any], operation: Operation) -> None:
    parent = document
    for index, item in enumerate(operation.path[:-1]):
        location = _path(operation.path[: index + 1])
        if item not in parent:
            raise CompositionError(f"operation parent does not exist: {location}")
        child = parent[item]
        if not isinstance(child, dict):
            raise CompositionError(f"operation crosses non-object: {location}")
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
        raise CompositionError("base must use bb.harness_definition.v1 version 1")
    extra = sorted(set(document) - _ROOTS)
    if extra:
        raise CompositionError(f"base contains non-canonical V1 root: {extra[0]!r}")
    if type(modules) not in (list, tuple):
        raise CompositionError("modules must be a sequence")
    snapshot = tuple(_admit(item)[0] for item in modules)
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
            message = f"validator for module {module.module_id!r} failed: {error}"
            raise CompositionError(message) from None
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
            message = f"extension builder {extension_id!r} failed: {error}"
            raise CompositionError(message) from None
        try:
            contribution, roots = _admit(built)
        except CompositionError as error:
            message = f"extension builder {extension_id!r} returned invalid ownership"
            raise CompositionError(f"{message}: {error}") from None
        name = f"extension:{extension_id}"
        operations = contribution.operations
        return _owned(name, precedence, operations, roots, contribution.validator)
