from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned

_ROOTS = frozenset({"tools", "enhanced_tools"})


def _unique_names(value: object, label: str, *, require_list: bool = False) -> None:
    if isinstance(value, str) and not require_list:
        if not value or value != value.strip():
            raise CompositionError(f"{label} must be nonempty")
        return
    if not isinstance(value, (list, tuple)):
        if require_list:
            raise CompositionError(f"{label} must be a list")
        return
    if any(type(name) is not str or not name or name != name.strip() for name in value):
        raise CompositionError(f"{label} requires nonempty string names")
    if len(value) != len(set(value)):
        raise CompositionError(f"{label} names must be unique")


def _validate(document: Mapping[str, object]) -> None:
    tools = document.get("tools")
    if not isinstance(tools, Mapping):
        return

    registry = tools.get("registry")
    if isinstance(registry, Mapping):
        for field in ("include", "paths"):
            if field in registry:
                _unique_names(
                    registry[field], f"tools.registry.{field}", require_list=True
                )

    aliases = tools.get("aliases")
    if isinstance(aliases, Mapping):
        for alias, target in aliases.items():
            if any(
                type(value) is not str or not value or value != value.strip()
                for value in (alias, target)
            ):
                raise CompositionError("invalid tool alias")
        for root in aliases:
            seen = set()
            current = root
            while current in aliases:
                if current in seen:
                    raise CompositionError("tool alias graph must be acyclic")
                seen.add(current)
                current = aliases[current]

    dialects = tools.get("dialects")
    if not isinstance(dialects, Mapping):
        return
    if "preference" in dialects:
        raise CompositionError("V1 tool dialect preference is unsupported")
    selection = dialects.get("selection") or {}
    by_model = selection.get("by_model")
    if not isinstance(by_model, Mapping):
        return
    _unique_names(tuple(by_model), "tools.dialects.selection.by_model")
    for model, value in by_model.items():
        if isinstance(value, Mapping):
            value = value.get("order")
        if isinstance(value, str):
            raise CompositionError("tool selection order must be a list")
        _unique_names(value, f"tools.dialects.selection.by_model.{model}")


def build_tool_module(operations: _Ops, precedence: int = 20) -> _Mod:
    return _owned("tool", precedence, operations, _ROOTS, _validate)
