from agentic_coder_prototype.compilation.tool_registry import load_tool_registry

from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned


def _validate(document: Mapping[str, object]) -> None:
    concurrency = document.get("concurrency")
    if not isinstance(concurrency, Mapping):
        return
    config = document.get("tools") or {}
    aliases = load_tool_registry(
        config.get("defs_dir"), aliases=config.get("aliases") or {}
    ).alias_map()

    def _clean(value: object) -> bool:
        return type(value) is str and bool(value) and value == value.strip()

    def _names(values: object) -> bool:
        return isinstance(values, (list, tuple)) and all(
            _clean(value) for value in values
        )

    def canonical(name: str) -> str:
        while name in aliases:
            name = aliases[name]
        return name

    for field in ("nonblocking_tools", "at_most_one_of"):
        values = concurrency.get(field)
        if values is not None and not _names(values):
            raise CompositionError(f"{field} must contain nonempty tool names")
    groups = concurrency.get("groups")
    if not isinstance(groups, (list, tuple)):
        return
    seen = set()
    claimed = set()
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        name = group.get("name", "")
        raw_tools = group.get("match_tools", ())
        if not _clean(name) or name in seen or not raw_tools or not _names(raw_tools):
            raise CompositionError("concurrency group is invalid")
        tools = [canonical(tool) for tool in raw_tools]
        if len(tools) != len(set(tools)) or claimed.intersection(tools):
            raise CompositionError("tool belongs to multiple concurrency groups")
        claimed.update(tools)
        seen.add(name)
        maximum = group.get("max_parallel", 1)
        if type(maximum) is not int or maximum < 1:
            raise CompositionError("concurrency group max_parallel must be positive")
        barrier = group.get("barrier_after")
        if barrier is not None and (not _clean(barrier) or barrier not in raw_tools):
            raise CompositionError("concurrency group barrier is invalid")


def build_resource_module(operations: _Ops, precedence: int = 30) -> _Mod:
    return _owned(
        "resource", precedence, operations, frozenset({"concurrency"}), _validate
    )
