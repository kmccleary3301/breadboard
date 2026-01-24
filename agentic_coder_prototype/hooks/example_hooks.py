from __future__ import annotations

from typing import Any, Dict, Optional

from .model import HookResult


class ExampleBeforeEmitTaggerHook:
    """Example hook that tags outgoing events (transform)."""

    hook_id = "example_before_emit_tagger"
    owner = "example"
    phases = ("before_emit",)
    priority = 100

    def run(
        self,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any] = None,
        turn: Optional[int] = None,
        hook_executor: Optional[Any] = None,
    ) -> HookResult:
        if phase != "before_emit":
            return HookResult(action="allow")
        if not isinstance(payload, dict):
            return HookResult(action="allow", reason="invalid_payload")
        envelope = dict(payload)
        raw_payload = envelope.get("payload")
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        tagged = dict(raw_payload)
        meta = tagged.get("debug")
        if not isinstance(meta, dict):
            meta = {}
        meta = dict(meta)
        meta["tag"] = "example"
        tagged["debug"] = meta
        envelope["payload"] = tagged
        return HookResult(action="transform", payload=envelope)


class ExamplePolicyDecisionHook:
    """Example hook that emits a policy decision for audit."""

    hook_id = "example_policy_decision"
    owner = "example"
    phases = ("before_model",)
    priority = 100

    def run(
        self,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any] = None,
        turn: Optional[int] = None,
        hook_executor: Optional[Any] = None,
    ) -> HookResult:
        if phase != "before_model":
            return HookResult(action="allow")
        return HookResult(
            action="allow",
            payload={
                "policy_decisions": [
                    {
                        "action": "WARN",
                        "reason_code": "example.before_model",
                        "message": "Example hook invoked before model call.",
                        "plugin_id": self.owner,
                        "priority": self.priority,
                    }
                ]
            },
        )

