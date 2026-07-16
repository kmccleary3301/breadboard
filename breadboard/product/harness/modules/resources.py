from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned


def _validate(document: Mapping[str, object]) -> None:
    concurrency = document.get("concurrency")
    if not isinstance(concurrency, Mapping):
        return
    config = document.get("tools")
    aliases = config.get("aliases", {}) if isinstance(config, Mapping) else {}

    def canonical(name: str) -> str:
        while name in aliases:
            name = aliases[name]
        return name

    for field in ("nonblocking_tools", "at_most_one_of"):
        values = concurrency.get(field)
        if values is not None and (
            not isinstance(values, (list, tuple))
            or any(type(value) is not str or not value.strip() for value in values)
        ):
            raise CompositionError(f"{field} must contain nonempty tool names")
    groups = concurrency.get("groups")
    if not isinstance(groups, (list, tuple)):
        return
    seen = set()
    claimed = set()
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        name = group.get("name", "").strip()
        tools = [canonical(tool.strip()) for tool in group.get("match_tools", ())]
        if not name or name in seen or not tools or any(not tool for tool in tools):
            raise CompositionError("concurrency group is invalid")
        if len(tools) != len(set(tools)) or claimed.intersection(tools):
            raise CompositionError("tool belongs to multiple concurrency groups")
        claimed.update(tools)
        seen.add(name)
        maximum = group.get("max_parallel", 1)
        if type(maximum) is not int or maximum < 1:
            raise CompositionError("concurrency group max_parallel must be positive")
        barrier = group.get("barrier_after")
        if barrier is not None and (
            type(barrier) is not str
            or not barrier.strip()
            or canonical(barrier.strip()) not in tools
        ):
            raise CompositionError("concurrency group barrier is invalid")


def build_resource_module(operations: _Ops, precedence: int = 30) -> _Mod:
    return _owned(
        "resource", precedence, operations, frozenset({"concurrency"}), _validate
    )
