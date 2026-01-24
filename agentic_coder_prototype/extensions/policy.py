from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple


class PolicyDecisionAction(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    TRANSFORM = "TRANSFORM"
    WARN = "WARN"
    ANNOTATE = "ANNOTATE"


def _normalize_action(action: PolicyDecisionAction | str) -> PolicyDecisionAction:
    if isinstance(action, PolicyDecisionAction):
        return action
    text = str(action or "").strip().upper()
    for member in PolicyDecisionAction:
        if member.value == text:
            return member
    raise ValueError(f"Unknown policy decision action: {action}")


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyDecisionAction | str
    reason_code: str
    message: str = ""
    plugin_id: str = ""
    decision_id: str = ""
    priority: int = 100
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _normalize_action(self.action))
        object.__setattr__(self, "reason_code", str(self.reason_code or "").strip())
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "plugin_id", str(self.plugin_id or "").strip())
        object.__setattr__(self, "decision_id", str(self.decision_id or "").strip())
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "payload", dict(self.payload or {}))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def sort_key(self) -> Tuple[int, str, str, str]:
        return (self.priority, self.plugin_id, self.decision_id, self.reason_code)


@dataclass(frozen=True)
class PolicyDecisionMergeResult:
    action: PolicyDecisionAction
    deny: Optional[PolicyDecision]
    transforms: Tuple[PolicyDecision, ...]
    warnings: Tuple[PolicyDecision, ...]
    annotations: Tuple[PolicyDecision, ...]
    ordered: Tuple[PolicyDecision, ...]


def _coerce_decision(item: Any) -> PolicyDecision:
    if isinstance(item, PolicyDecision):
        return item
    if isinstance(item, dict):
        return PolicyDecision(
            action=item.get("action", PolicyDecisionAction.ALLOW),
            reason_code=item.get("reason_code") or item.get("reason") or "",
            message=item.get("message") or "",
            plugin_id=item.get("plugin_id") or "",
            decision_id=item.get("decision_id") or "",
            priority=item.get("priority", 100),
            payload=dict(item.get("payload") or {}),
            metadata=dict(item.get("metadata") or {}),
        )
    raise TypeError(f"Unsupported policy decision type: {type(item)}")


def merge_policy_decisions(decisions: Iterable[PolicyDecision | Dict[str, Any]]) -> PolicyDecisionMergeResult:
    ordered = sorted((_coerce_decision(item) for item in decisions), key=lambda d: d.sort_key())

    denies = [d for d in ordered if d.action == PolicyDecisionAction.DENY]
    transforms = [d for d in ordered if d.action == PolicyDecisionAction.TRANSFORM]
    warnings = [d for d in ordered if d.action == PolicyDecisionAction.WARN]
    annotations = [d for d in ordered if d.action == PolicyDecisionAction.ANNOTATE]

    action = PolicyDecisionAction.DENY if denies else PolicyDecisionAction.ALLOW
    deny = denies[0] if denies else None

    return PolicyDecisionMergeResult(
        action=action,
        deny=deny,
        transforms=tuple(transforms),
        warnings=tuple(warnings),
        annotations=tuple(annotations),
        ordered=tuple(ordered),
    )
