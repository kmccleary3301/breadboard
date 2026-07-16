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
            if isinstance(entry, str):
                patterns = (entry,)
            elif bucket == "deny" and isinstance(entry, Mapping):
                patterns = entry.keys()
            else:
                continue
            for pattern in patterns:
                if not isinstance(pattern, str) or not pattern:
                    message = "permission shell patterns must be nonempty strings"
                    raise CompositionError(message)
                if pattern in seen:
                    message = f"duplicate permission shell pattern: {pattern}"
                    raise CompositionError(message)
                seen.add(pattern)


def build_policy_module(operations: _Ops, precedence: int = 40) -> _Mod:
    return _owned("policy", precedence, operations, _ROOTS, _validate)
