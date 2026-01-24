from __future__ import annotations

from agentic_coder_prototype.ctrees.context_engine import select_ctree_context
from agentic_coder_prototype.hooks.ctree_context_engine import CTreeContextEngineHook
from agentic_coder_prototype.state.session_state import SessionState


def test_ctree_context_engine_selection_deterministic_ignores_volatiles() -> None:
    store_a = SessionState(workspace=".", image="img").ctree_store
    store_a.record("message", {"role": "user", "content": "hello", "seq": 1, "timestamp": 1}, turn=1)

    store_b = SessionState(workspace=".", image="img").ctree_store
    store_b.record("message", {"role": "user", "content": "hello", "seq": 99, "timestamp": 999}, turn=1)

    selection_a, _ = select_ctree_context(
        store_a,
        selection_config={"kind_allowlist": ["message"]},
        header_config={"mode": "hash_only", "preview_chars": 16, "redact_secret_like": True},
        collapse_target=None,
        stage="SPEC",
        pin_latest=True,
    )
    selection_b, _ = select_ctree_context(
        store_b,
        selection_config={"kind_allowlist": ["message"]},
        header_config={"mode": "hash_only", "preview_chars": 16, "redact_secret_like": True},
        collapse_target=None,
        stage="SPEC",
        pin_latest=True,
    )

    assert selection_a["selection_sha256"] == selection_b["selection_sha256"]
    assert selection_a["kept_ids"] == selection_b["kept_ids"]


def test_ctree_context_engine_pins_latest_user_and_assistant() -> None:
    store = SessionState(workspace=".", image="img").ctree_store
    id1 = store.record("message", {"role": "user", "content": "u1"}, turn=1)
    id2 = store.record("message", {"role": "assistant", "content": "a1"}, turn=1)
    id3 = store.record("message", {"role": "user", "content": "u2"}, turn=2)

    selection, _ = select_ctree_context(
        store,
        selection_config={"kind_allowlist": ["message"]},
        header_config={"mode": "hash_only", "preview_chars": 16, "redact_secret_like": True},
        collapse_target=1,
        stage="SPEC",
        pin_latest=True,
    )

    assert selection["dropped_ids"] == [id1]
    assert id2 in selection["kept_ids"]
    assert id3 in selection["kept_ids"]
    assert selection["pinned_count"] == 2


def test_ctree_context_engine_hook_injects_system_note_when_enabled() -> None:
    config = {
        "hooks": {"enabled": True, "ctree_context_engine": True},
        "ctrees": {
            "context_engine": {
                "enabled": True,
                "mode": "prepend_system",
                "stage": "SPEC",
                "selection": {"kind_allowlist": ["message"]},
                "header": {"mode": "sanitized_content", "preview_chars": 8},
            }
        },
    }
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state.add_message({"role": "user", "content": "hello"}, to_provider=True)

    hook = CTreeContextEngineHook()
    result = hook.run("before_model", {"messages": list(session_state.provider_messages)}, session_state=session_state, turn=1)
    assert result.action == "transform"
    messages = result.payload.get("messages")
    assert isinstance(messages, list)
    assert messages and isinstance(messages[0], dict)
    assert messages[0].get("role") == "system"
    assert "C-TREES CONTEXT" in str(messages[0].get("content") or "")
    assert session_state.get_provider_metadata("ctrees_context_engine") is not None


def test_ctree_context_engine_hook_replace_messages_falls_back_without_dangerous_flag() -> None:
    config = {
        "hooks": {"enabled": True, "ctree_context_engine": True},
        "ctrees": {
            "context_engine": {
                "enabled": True,
                "mode": "replace_messages",
                "stage": "SPEC",
                "selection": {"kind_allowlist": ["message"]},
            }
        },
    }
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state.add_message({"role": "system", "content": "sys"}, to_provider=True)
    session_state.add_message({"role": "user", "content": "u1"}, to_provider=True)

    hook = CTreeContextEngineHook()
    result = hook.run("before_model", {"messages": list(session_state.provider_messages)}, session_state=session_state, turn=1)
    assert result.action == "transform"
    messages = result.payload.get("messages")
    assert isinstance(messages, list)
    assert messages and messages[0].get("role") == "system"
    assert "replace_messages_disabled" == session_state.get_provider_metadata("ctrees_context_engine_warning")


def test_ctree_context_engine_hook_replace_messages_uses_ctree_mapping() -> None:
    config = {
        "hooks": {"enabled": True, "ctree_context_engine": True},
        "ctrees": {
            "context_engine": {
                "enabled": True,
                "mode": "replace_messages",
                "dangerously_allow_replace": True,
                "stage": "SPEC",
                "selection": {"kind_allowlist": ["message"]},
                "collapse": {"target": 1},
            }
        },
    }
    session_state = SessionState(workspace=".", image="img", config=config)
    session_state.add_message({"role": "system", "content": "sys"}, to_provider=True)
    session_state.add_message({"role": "user", "content": "drop-me"}, to_provider=True)
    session_state.add_message({"role": "assistant", "content": "keep-a"}, to_provider=True)
    session_state.add_message({"role": "user", "content": "keep-u"}, to_provider=True)

    hook = CTreeContextEngineHook()
    original = list(session_state.provider_messages)
    result = hook.run("before_model", {"messages": original}, session_state=session_state, turn=1)
    assert result.action == "transform"
    messages = result.payload.get("messages")
    assert isinstance(messages, list)
    # system context note + a filtered subset of provider messages.
    assert len(messages) >= 2
    assert messages[0].get("role") == "system"
    kept_payloads = [m for m in messages[1:] if isinstance(m, dict)]
    assert {"role": "user", "content": "drop-me"} not in [{"role": m.get("role"), "content": m.get("content")} for m in kept_payloads]
    assert {"role": "assistant", "content": "keep-a"} in [{"role": m.get("role"), "content": m.get("content")} for m in kept_payloads]
    assert {"role": "user", "content": "keep-u"} in [{"role": m.get("role"), "content": m.get("content")} for m in kept_payloads]

