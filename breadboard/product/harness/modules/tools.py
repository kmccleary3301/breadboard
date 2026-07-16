"""Composable tool and enhanced-tool settings."""

from collections.abc import Mapping, Sequence

from .extensions import CompositionError, ModuleContribution, Operation, _owned

_ROOTS = frozenset({"tools", "enhanced_tools"})


def _unique_names(value: object, label: str, *, require_list: bool = False) -> None:
    if isinstance(value, str) and not require_list:
        if not value:
            raise CompositionError(f"{label} must be nonempty")
        return
    if not isinstance(value, (list, tuple)):
        if require_list:
            raise CompositionError(f"{label} must be a list")
        return
    if any(not isinstance(name, str) or not name for name in value):
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
            if (
                not isinstance(alias, str)
                or not alias
                or not isinstance(target, str)
                or not target
            ):
                raise CompositionError(
                    "tool aliases require nonempty string names and targets"
                )

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
            _unique_names(value, label)


def build_tool_module(
    operations: Sequence[Operation], precedence: int = 20
) -> ModuleContribution:
    return _owned("tool", precedence, operations, _ROOTS, _validate)
