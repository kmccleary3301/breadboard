from collections.abc import Mapping, Sequence

from .extensions import CompositionError, ModuleContribution, Operation, _owned

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
                    raise CompositionError(
                        "permission shell patterns must be nonempty strings"
                    )
                if pattern in seen:
                    raise CompositionError(
                        f"duplicate permission shell pattern: {pattern}"
                    )
                seen.add(pattern)


def build_policy_module(
    operations: Sequence[Operation], precedence: int = 40
) -> ModuleContribution:
    return _owned("policy", precedence, operations, _ROOTS, _validate)
