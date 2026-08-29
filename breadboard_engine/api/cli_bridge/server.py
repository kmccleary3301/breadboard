"""Entry point for serving the CLI bridge via uvicorn."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any, Dict

import uvicorn

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

from breadboard_engine.api.cli_bridge.app import create_app


def _load_env() -> None:
    if load_dotenv is not None:
        repo_root = Path(__file__).resolve().parents[3]
        for candidate in (repo_root / ".env", repo_root / ".env.local"):
            if candidate.exists():
                load_dotenv(candidate, override=False)


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
        allow_insecure = (
            os.environ.get("BREADBOARD_ALLOW_INSECURE_REMOTE") or ""
        ).strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not token or not allow_insecure:
            raise SystemExit(
                "Refusing direct non-local BreadBoard engine bind.\n"
                f"Host: {host}\n"
                "The built-in server does not provide TLS. Bind locally behind "
                "a TLS-terminating protected channel, or set both "
                "BREADBOARD_API_TOKEN and BREADBOARD_ALLOW_INSECURE_REMOTE=1 "
                "for an explicitly unsupported insecure override."
            )
    return {
        "host": host,
        "port": port,
        "reload": reload_enabled,
        "log_level": log_level,
    }


def _request_shutdown() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def main() -> None:
    _load_env()
    config = build_uvicorn_config()
    app = create_app(request_shutdown=_request_shutdown)
    uvicorn.run(app, **config)


if __name__ == "__main__":
    main()
