from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from collections.abc import Awaitable, Callable, Sequence
from types import FrameType
from typing import Any
import uvicorn


from .composition import load_production_composition


def _secret_file(value: str) -> tuple[str, str]:
    if value.count("=") != 1:
        raise argparse.ArgumentTypeError("secret file must be HANDLE=/absolute/path")
    handle, path = value.split("=", 1)
    if not handle or not path.startswith("/"):
        raise argparse.ArgumentTypeError("secret file must use a non-empty handle and absolute path")
    return handle, path

def _service_fd(value: str) -> tuple[str, int]:
    if value.count("=") != 1:
        raise argparse.ArgumentTypeError("service fd must be ROLE=FD")
    role, raw_fd = value.split("=", 1)
    try:
        fd = int(raw_fd)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("service fd must be ROLE=FD") from exc
    if not role or fd < 0:
        raise argparse.ArgumentTypeError("service fd must be ROLE=FD")
    return role, fd


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m breadboard.rl.harness", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "serve"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--composition-ref", required=True)
        command.add_argument("--secret-file", action="append", type=_secret_file, default=[], metavar="HANDLE=/absolute/path")
        command.add_argument("--prebound-service-fd", action="append", type=_service_fd, default=[], metavar="ROLE=FD")
    return parser


def _bindings(values: Sequence[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for handle, path in values:
        if handle in result:
            raise ValueError("duplicate secret handle")
        result[handle] = path
    return result

def _socket_bindings(values: Sequence[tuple[str, int]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for role, fd in values:
        if role in result:
            raise ValueError("duplicate prebound service socket role")
        result[role] = fd
    return result


async def _inspect(ref: str, bindings: dict[str, str], socket_fds: dict[str, int]) -> int:
    composition = load_production_composition(
        ref, bindings, prebound_service_socket_fds=socket_fds
    )
    try:
        sys.stdout.buffer.write(composition.manifest.canonical_bytes() + b"\n")
        sys.stdout.buffer.flush()
        return 0
    finally:
        await composition.close()


async def _await_owned_shutdown(task: asyncio.Task[None]) -> None:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
    failure: BaseException | None = None
    try:
        task.result()
    except BaseException as exc:
        failure = exc
    if cancellation is not None:
        if failure is not None:
            raise BaseExceptionGroup(
                "service shutdown cancelled and failed",
                [cancellation, failure],
            )
        raise cancellation
    if failure is not None:
        raise failure


class _LifecycleServer(uvicorn.Server):
    def __init__(
        self,
        config: uvicorn.Config,
        shutdown_service: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(config)
        self._shutdown_service = shutdown_service
        self._service_shutdown_task: asyncio.Task[None] | None = None

    def _start_service_shutdown(self) -> None:
        if self._service_shutdown_task is None:
            self._service_shutdown_task = asyncio.get_running_loop().create_task(
                self._shutdown_service()
            )

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        del sig, frame
        self._start_service_shutdown()
        if self.should_exit:
            self.force_exit = True
        else:
            self.should_exit = True

    async def _watch_for_exit(self) -> None:
        while not self.should_exit:
            await asyncio.sleep(0.01)
        self._start_service_shutdown()

    async def serve(self, sockets: Any = None) -> None:
        watcher = asyncio.create_task(self._watch_for_exit())
        cancellation: asyncio.CancelledError | None = None
        failure: BaseException | None = None
        try:
            await super().serve(sockets=sockets)
        except asyncio.CancelledError as exc:
            cancellation = exc
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
        finally:
            self._start_service_shutdown()
            if not watcher.done():
                watcher.cancel()
            if self._service_shutdown_task is not None:
                try:
                    await _await_owned_shutdown(
                        self._service_shutdown_task
                    )
                except BaseException as exc:
                    failure = exc
            await asyncio.gather(watcher, return_exceptions=True)
        if cancellation is not None:
            if failure is not None:
                raise BaseExceptionGroup(
                    "server cancellation and service shutdown failure",
                    [cancellation, failure],
                )
            raise cancellation
        if failure is not None:
            raise failure


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bindings = _bindings(args.secret_file)
        socket_fds = _socket_bindings(args.prebound_service_fd)
        if args.command == "inspect":
            return asyncio.run(_inspect(args.composition_ref, bindings, socket_fds))
        composition = load_production_composition(
            args.composition_ref,
            bindings,
            prebound_service_socket_fds=socket_fds,
        )
        try:
            config = uvicorn.Config(
                composition.app,
                host=composition.server.host,
                port=composition.server.port,
                proxy_headers=composition.server.proxy_headers,
                log_config=None,
            )
            try:
                harness_socket = socket.fromfd(
                    socket_fds["harness"],
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                )
            except KeyError as exc:
                raise ValueError("harness prebound service socket is required") from exc
            try:
                _LifecycleServer(
                    config,
                    composition.app.state.episode_service.close,
                ).run(sockets=[harness_socket])
            finally:
                harness_socket.close()
            return 0
        finally:
            asyncio.run(composition.close())
    except KeyboardInterrupt:
        return 130
    except BaseExceptionGroup:
        print("composition runtime failed", file=sys.stderr)
        return 2
    except Exception:
        print("composition runtime failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
