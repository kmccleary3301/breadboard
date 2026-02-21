from __future__ import annotations

from agentic_coder_prototype.longrun.flags import (
    is_longrun_enabled,
    resolve_episode_max_steps,
    resolve_longrun_policy_profile,
)


def test_is_longrun_enabled_default_false(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_ENABLE", raising=False)
    assert is_longrun_enabled({}) is False
    assert is_longrun_enabled(None) is False


def test_is_longrun_enabled_from_config(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_ENABLE", raising=False)
    assert is_longrun_enabled({"long_running": {"enabled": True}}) is True
    assert is_longrun_enabled({"long_running": {"enabled": False}}) is False


def test_is_longrun_enabled_env_override(monkeypatch):
    monkeypatch.setenv("BREADBOARD_LONGRUN_ENABLE", "0")
    assert is_longrun_enabled({"long_running": {"enabled": True}}) is False
    monkeypatch.setenv("BREADBOARD_LONGRUN_ENABLE", "1")
    assert is_longrun_enabled({"long_running": {"enabled": False}}) is True


def test_is_longrun_enabled_invalid_env_ignored(monkeypatch):
    monkeypatch.setenv("BREADBOARD_LONGRUN_ENABLE", "maybe")
    assert is_longrun_enabled({"long_running": {"enabled": True}}) is True
    assert is_longrun_enabled({"long_running": {"enabled": False}}) is False


def test_resolve_episode_max_steps_enabled_override(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_ENABLE", raising=False)
    cfg = {"long_running": {"enabled": True, "episode": {"max_steps_override": 7}}}
    assert resolve_episode_max_steps(cfg, 12) == 7


def test_resolve_episode_max_steps_disabled_uses_default(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_ENABLE", raising=False)
    cfg = {"long_running": {"enabled": False, "episode": {"max_steps_override": 7}}}
    assert resolve_episode_max_steps(cfg, 12) == 12


def test_resolve_episode_max_steps_invalid_uses_default(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_ENABLE", raising=False)
    cfg = {"long_running": {"enabled": True, "episode": {"max_steps_override": "bad"}}}
    assert resolve_episode_max_steps(cfg, 9) == 9


def test_resolve_longrun_policy_profile_defaults(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_POLICY_PROFILE", raising=False)
    assert resolve_longrun_policy_profile(None) == "balanced"
    assert resolve_longrun_policy_profile({}, default="invalid") == "balanced"


def test_resolve_longrun_policy_profile_from_config(monkeypatch):
    monkeypatch.delenv("BREADBOARD_LONGRUN_POLICY_PROFILE", raising=False)
    cfg = {"long_running": {"policy_profile": "aggressive"}}
    assert resolve_longrun_policy_profile(cfg) == "aggressive"


def test_resolve_longrun_policy_profile_env_override(monkeypatch):
    monkeypatch.setenv("BREADBOARD_LONGRUN_POLICY_PROFILE", "conservative")
    cfg = {"long_running": {"policy_profile": "aggressive"}}
    assert resolve_longrun_policy_profile(cfg) == "conservative"


def test_resolve_longrun_policy_profile_invalid_env_ignored(monkeypatch):
    monkeypatch.setenv("BREADBOARD_LONGRUN_POLICY_PROFILE", "unknown")
    cfg = {"long_running": {"policy_profile": "aggressive"}}
    assert resolve_longrun_policy_profile(cfg) == "aggressive"
