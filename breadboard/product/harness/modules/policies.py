from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned

_ROOTS = frozenset({"permissions", "guardrails", "replay"})


def _validate(document: Mapping[str, object]) -> None:
    permissions = document.get("permissions")
    if not isinstance(permissions, Mapping):
        return
    shell = permissions.get("shell")
    if not isinstance(shell, Mapping):
        return
    seen: set[str] = set()
    for bucket in ("allow", "ask", "deny"):
        entries = shell.get(bucket)
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if not isinstance(entry, str) or not entry:
                raise CompositionError("invalid permission shell entry")
            if entry in seen:
                raise CompositionError(f"duplicate permission shell pattern: {entry}")
            seen.add(entry)


def build_policy_module(operations: _Ops, precedence: int = 40) -> _Mod:
    return _owned("policy", precedence, operations, _ROOTS, _validate)
