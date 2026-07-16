from collections.abc import Mapping, Sequence

from .extensions import CompositionError, ModuleContribution, Operation, owned


def _validate(document: Mapping[str, object]) -> None:
    workspace = document.get("workspace")
    if not isinstance(workspace, Mapping):
        return
    mirror = workspace.get("mirror")
    if not isinstance(mirror, Mapping) or mirror.get("enabled") is not True:
        return
    path = mirror.get("path")
    if not isinstance(path, str) or not path:
        raise CompositionError("enabled workspace mirror requires a nonempty path")


def build_host_module(
    operations: Sequence[Operation], precedence: int = 50
) -> ModuleContribution:
    return owned("host", precedence, operations, frozenset({"workspace"}), _validate)
