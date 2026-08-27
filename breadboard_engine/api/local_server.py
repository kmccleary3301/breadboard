from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def local_server(workspace: Path) -> Iterator[str]:
    """Run the product API on an ephemeral loopback port for local harness runs."""
    import uvicorn

    from breadboard_engine.api.cli_bridge.app import create_app

    settings = {
        "BREADBOARD_LEGACY_ROUTES": "0",
        "BREADBOARD_ENABLE_PUBLIC_API": "1",
        "BREADBOARD_ENABLE_E4_API": "0",
        "BREADBOARD_PUBLIC_WORKSPACE": str(workspace),
        "RAY_SCE_LOCAL_MODE": "1",
    }
    previous = {name: os.environ.get(name) for name in settings}
    os.environ.update(settings)
    listener: socket.socket | None = None

    def restore_environment() -> None:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(),
                host="127.0.0.1",
                port=int(listener.getsockname()[1]),
                log_level="critical",
                access_log=False,
            )
        )
    except BaseException:
        if listener is not None:
            listener.close()
        restore_environment()
        raise

    def serve() -> None:
        server.run(sockets=[listener])

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        restore_environment()
        raise RuntimeError("local create_app server did not start")
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        restore_environment()
        if thread.is_alive():
            raise RuntimeError("local create_app server did not stop")
