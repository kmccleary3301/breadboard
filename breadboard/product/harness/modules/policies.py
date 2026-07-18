from collections.abc import Mapping

from .extensions import CompositionError, _Mod, _Ops, _owned

_ROOTS = frozenset({"permissions", "guardrails", "replay"})


def _validate(document: Mapping[str, object]) -> None:
    permissions = document.get("permissions")
    if not isinstance(permissions, Mapping):
        return
    for category in ("edit", "shell"):
        default = (permissions.get(category) or {}).get("default", "allow")
        if default not in ("allow", "ask", "deny") or default != default.strip():
            raise CompositionError(f"invalid permission {category} default")
    seen: set[str] = set()
    for bucket in ("allow", "ask", "deny"):
        entries = (permissions.get("shell") or {}).get(bucket)
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            pattern = entry if isinstance(entry, str) else ""
            if not pattern or pattern != pattern.strip():
                raise CompositionError("invalid permission shell entry")
            if pattern in seen:
                raise CompositionError(f"duplicate permission shell pattern: {pattern}")
            seen.add(pattern)


def build_policy_module(operations: _Ops, precedence: int = 40) -> _Mod:
    return _owned("policy", precedence, operations, _ROOTS, _validate)
