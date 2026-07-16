from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned

_ROOTS = frozenset({"tools", "enhanced_tools"})


def _unique_names(value: object, label: str, *, require_list: bool = False) -> None:
    if isinstance(value, str) and not require_list:
        if not value.strip():
            raise CompositionError(f"{label} must be nonempty")
        return
    if not isinstance(value, (list, tuple)):
        if require_list:
            raise CompositionError(f"{label} must be a list")
        return
    names = [name.strip() for name in value if isinstance(name, str)]
    if len(names) != len(value) or not all(names):
        raise CompositionError(f"{label} requires nonempty string names")
    if len(names) != len(set(names)):
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
            if (
                not isinstance(alias, str)
                or not alias
                or not isinstance(target, str)
                or not target
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
    for section_name in ("preference", "selection"):
        section = dialects.get(section_name)
        if not isinstance(section, Mapping):
            continue
        if section_name == "preference" and "default" in section:
            _unique_names(section["default"], "tools.dialects.preference.default")
        by_model = section.get("by_model")
        if not isinstance(by_model, Mapping):
            continue
        for model, value in by_model.items():
            label = f"tools.dialects.{section_name}.by_model.{model}"
            if isinstance(value, Mapping):
                value = value.get("order")
            if section_name == "selection" and isinstance(value, str):
                raise CompositionError("tool selection order must be a list")
            _unique_names(value, label)


def build_tool_module(operations: _Ops, precedence: int = 20) -> _Mod:
    return _owned("tool", precedence, operations, _ROOTS, _validate)
