from __future__ import annotations

from agentic_coder_prototype.state.session_state import SessionState


def test_ctree_mapping_rules_for_message_guardrail_transcript() -> None:
    state = SessionState("ws", "image", {})

    state.add_message({"role": "user", "content": "hello"}, to_provider=False)
    state.record_guardrail_event("test_guard", {"reason": "unit"})
    state.add_transcript_entry({"note": "transcript entry"})

    kinds = [node.get("kind") for node in state.ctree_store.nodes]
    assert "message" in kinds
    assert "guardrail" in kinds
    assert "transcript" in kinds

    # Validate payload shapes (minimal)
    message_node = next(node for node in state.ctree_store.nodes if node.get("kind") == "message")
    payload = message_node.get("payload") or {}
    assert payload.get("role") == "user"
    assert payload.get("content") == "hello"

    guard_node = next(node for node in state.ctree_store.nodes if node.get("kind") == "guardrail")
    guard_payload = guard_node.get("payload") or {}
    assert guard_payload.get("type") == "test_guard"
    assert guard_payload.get("payload", {}).get("reason") == "unit"

    transcript_node = next(node for node in state.ctree_store.nodes if node.get("kind") == "transcript")
    transcript_payload = transcript_node.get("payload") or {}
    assert transcript_payload.get("note") == "transcript entry"
