"""Entry point for serving the CLI bridge via uvicorn."""

from __future__ import annotations

import os
from typing import Any, Dict

import uvicorn

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

from .app import create_app


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv()


def build_uvicorn_config() -> Dict[str, Any]:
    host = os.environ.get("KYLECODE_CLI_HOST", "127.0.0.1")
    port = int(os.environ.get("KYLECODE_CLI_PORT", "9099"))
    reload_enabled = bool(os.environ.get("KYLECODE_CLI_RELOAD", ""))
    log_level = os.environ.get("KYLECODE_CLI_LOG_LEVEL", "info")
    return {"host": host, "port": port, "reload": reload_enabled, "log_level": log_level}


def main() -> None:
    _load_env()
    config = build_uvicorn_config()
    app = create_app()
    uvicorn.run(app, **config)


if __name__ == "__main__":
    main()
