"""Registration of concrete built-in provider runtimes."""

from __future__ import annotations

from .registry import ProviderRuntimeRegistry, provider_registry
from .runtimes.testing import CliMockRuntime, MockRuntime, SmokeRuntime


def register_builtin_runtimes(registry: ProviderRuntimeRegistry = provider_registry) -> None:
    """Register deterministic and optional built-in runtimes in stable order.

    Registration overwrites the same implementation for an existing identifier,
    making repeated calls safe while preserving insertion order.
    """
    registry.register_runtime("mock_chat", MockRuntime)
    registry.register_runtime("smoke_chat", SmokeRuntime)
    registry.register_runtime("cli_mock_chat", CliMockRuntime)
    try:  # pragma: no cover - optional replay runtime
        from .runtime_replay import ReplayRuntime

        registry.register_runtime("replay", ReplayRuntime)
    except Exception:
        pass
    try:  # pragma: no cover - optional codex runtime
        from .runtime_codex import CodexAppServerRuntime

        registry.register_runtime("codex_app_server", CodexAppServerRuntime)
    except Exception:
        pass


__all__ = ["register_builtin_runtimes"]
