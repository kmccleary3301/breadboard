"""Entry point for serving the CLI bridge via uvicorn."""

from __future__ import annotations

import os
from typing import Any, Dict

import uvicorn

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

from agentic_coder_prototype.api.cli_bridge.app import create_app


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv()

def _is_local_bind(host: str) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _is_externally_reachable_bind(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if not normalized:
        return False
    # Explicit wildcard binds are always externally reachable.
    if normalized in {"0.0.0.0", "::"}:
        return True
    return not _is_local_bind(normalized)


def build_uvicorn_config() -> Dict[str, Any]:
    host = os.environ.get("BREADBOARD_CLI_HOST", "127.0.0.1")
    port = int(os.environ.get("BREADBOARD_CLI_PORT", "9099"))
    reload_enabled = bool(os.environ.get("BREADBOARD_CLI_RELOAD", ""))
    log_level = os.environ.get("BREADBOARD_CLI_LOG_LEVEL", "info")
    if _is_externally_reachable_bind(host):
        token = (os.environ.get("BREADBOARD_API_TOKEN") or "").strip()
        allow_insecure = (os.environ.get("BREADBOARD_ALLOW_INSECURE_REMOTE") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not token and not allow_insecure:
            raise SystemExit(
                "Refusing to start BreadBoard engine on a non-local bind without auth.\n"
                f"Host: {host}\n"
                "Set BREADBOARD_API_TOKEN to require a Bearer token for all requests, or bind locally "
                "(BREADBOARD_CLI_HOST=127.0.0.1).\n"
                "To override (unsafe), set BREADBOARD_ALLOW_INSECURE_REMOTE=1."
            )
    return {"host": host, "port": port, "reload": reload_enabled, "log_level": log_level}


def main() -> None:
    _load_env()
    config = build_uvicorn_config()
    app = create_app()
    uvicorn.run(app, **config)


if __name__ == "__main__":
    main()
