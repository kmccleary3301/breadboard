from collections.abc import Mapping, Sequence

from .extensions import CompositionError, ModuleContribution, Operation, owned


def _validate(document: Mapping[str, object]) -> None:
    concurrency = document.get("concurrency")
    if not isinstance(concurrency, Mapping):
        return
    groups = concurrency.get("at_most_one_of")
    if not isinstance(groups, (list, tuple)):
        return
    for group in groups:
        names = (group,) if isinstance(group, str) else group
        if not isinstance(names, (list, tuple)):
            continue
        if not names or any(not isinstance(name, str) or not name for name in names):
            raise CompositionError("at_most_one_of groups require nonempty names")
        if len(names) != len(set(names)):
            raise CompositionError("at_most_one_of group names must be unique")


def build_resource_module(
    operations: Sequence[Operation], precedence: int = 30
) -> ModuleContribution:
    return owned(
        "resource", precedence, operations, frozenset({"concurrency"}), _validate
    )
