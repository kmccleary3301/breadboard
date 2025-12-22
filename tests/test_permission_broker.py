from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentic_coder_prototype.permission_broker import PermissionBroker, PermissionDeniedError


class _DummySessionState:
    def __init__(self) -> None:
        self._meta: dict = {}
        self.transcript: list[dict] = []

    def get_provider_metadata(self, key: str, default=None):
        return self._meta.get(key, default)

    def set_provider_metadata(self, key: str, value) -> None:
        self._meta[key] = value

    def add_transcript_entry(self, entry) -> None:
        self.transcript.append(entry)


def _shell_call(command: str):
    return SimpleNamespace(function="bash", arguments={"command": command})


def _webfetch_call(url: str):
    return SimpleNamespace(function="webfetch", arguments={"url": url, "format": "text"})


def test_shell_permission_structured_matching_denies_rm_rf() -> None:
    broker = PermissionBroker(
        {
            "shell": {
                "default": "allow",
                "deny": ["rm -rf*"],
            }
        }
    )
    session = _DummySessionState()
    with pytest.raises(PermissionDeniedError):
        broker.ensure_allowed(session, [_shell_call("rm -rf /tmp/foo")])


def test_shell_permission_structured_matching_asks_when_pattern_matches() -> None:
    broker = PermissionBroker(
        {
            "shell": {
                "default": "allow",
                "ask": ["sort --output=*"],
            }
        }
    )
    session = _DummySessionState()
    broker.ensure_allowed(session, [_shell_call("sort -k 1 --output=out.txt input.txt")])
    assert any(
        entry.get("permission_broker", {}).get("status") == "ask"
        for entry in session.transcript
        if isinstance(entry, dict)
    )


def test_disabled_tool_mask_edit_denied_hides_mutating_tools() -> None:
    broker = PermissionBroker({"edit": {"default": "deny"}})
    disabled = broker.disabled_tool_names()
    assert "edit" in disabled
    assert "write" in disabled
    assert "patch" in disabled


def test_disabled_tool_mask_shell_denied_hides_bash_only_when_fully_denied() -> None:
    fully_denied = PermissionBroker({"shell": {"default": "deny"}})
    assert "bash" in fully_denied.disabled_tool_names()

    partially_allowed = PermissionBroker({"shell": {"default": "deny", "allow": ["ls*"]}})
    assert "bash" not in partially_allowed.disabled_tool_names()


def test_webfetch_permission_denied_blocks_execution() -> None:
    broker = PermissionBroker({"webfetch": {"default": "deny"}})
    session = _DummySessionState()
    with pytest.raises(PermissionDeniedError):
        broker.ensure_allowed(session, [_webfetch_call("https://example.com")])

