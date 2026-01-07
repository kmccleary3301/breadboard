import os

from breadboard.sandbox_driver import resolve_driver_from_env


def test_resolve_driver_defaults_to_light(monkeypatch):
    monkeypatch.delenv("BREADBOARD_SANDBOX_DRIVER", raising=False)
    monkeypatch.delenv("SANDBOX_DRIVER", raising=False)
    monkeypatch.delenv("RAY_USE_DOCKER_SANDBOX", raising=False)
    assert resolve_driver_from_env() == "light"


def test_resolve_driver_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("BREADBOARD_SANDBOX_DRIVER", "docker")
    monkeypatch.delenv("RAY_USE_DOCKER_SANDBOX", raising=False)
    assert resolve_driver_from_env() == "docker"


def test_resolve_driver_uses_docker_flag(monkeypatch):
    monkeypatch.delenv("BREADBOARD_SANDBOX_DRIVER", raising=False)
    monkeypatch.delenv("SANDBOX_DRIVER", raising=False)
    monkeypatch.setenv("RAY_USE_DOCKER_SANDBOX", "1")
    assert resolve_driver_from_env() == "docker"

