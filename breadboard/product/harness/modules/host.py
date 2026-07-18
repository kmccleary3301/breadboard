from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned


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


def build_host_module(operations: _Ops, precedence: int = 50) -> _Mod:
    return _owned("host", precedence, operations, frozenset({"workspace"}), _validate)
