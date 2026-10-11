"""Admit stock-owned root notifications through the ordinary session input queue."""
from __future__ import annotations

import asyncio
from collections import deque
import threading
from typing import Any, Awaitable, Callable

from .models import SessionInputRequest


class StockInputAdmissionFailure(RuntimeError):
    """Deliver admission failures to the existing lifecycle failure owner."""


class StockInputController:
    def __init__(self, runner: Any, submit: Callable[..., Awaitable[Any]]):
        self.runner = runner
        self.submit = submit
        metadata = runner.session.metadata
        self.headless = bool(
            metadata.get("non_interactive_cli_session")
            or metadata.get("cli_session_kind") == "oneshot"
        )
        self._sources: deque[dict] = deque()
        self._lock = threading.Lock()
        self._drain_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.origins: dict[str, dict] = {}

    def receive(self, source: dict) -> None:
        # Append synchronously at provider-request observation. A simultaneous
        # public send_input drains this buffer before admitting a user turn.
        with self._lock:
            self._sources.append(source)
        loop = self.runner._loop
        if loop is None or loop.is_closed():
            raise StockInputAdmissionFailure("Stock input has no live session runner")
        loop.call_soon_threadsafe(self._schedule)

    def _schedule(self) -> None:
        task = asyncio.create_task(self.drain())
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and not self.runner._closed:
            self.runner._input_queue.put_nowait(StockInputAdmissionFailure(str(error)))

    async def drain(self) -> None:
        async with self._drain_lock:
            owner = getattr(self.runner._agent, "agent", None)
            native = getattr(owner, "_opencode_tool_sessions", {}).get(self.runner.session.session_id)
            if native is not None and not native.closed:
                await asyncio.to_thread(native.wait_for_idle_source_input)
            while True:
                with self._lock:
                    if not self._sources:
                        return
                    source = self._sources.popleft()
                await self.submit(
                    self.runner.session.session_id,
                    SessionInputRequest(content=source["content"],
                        client_message_id="stock:" + source["request_id"]),
                    _stock_origin=source,
                )

    async def headless_should_continue(self) -> bool:
        await self.drain()
        owner = getattr(self.runner._agent, "agent", None)
        native = getattr(owner, "_opencode_tool_sessions", {}).get(self.runner.session.session_id)
        if native is None or native.headless_idle_seen:
            return False
        with native.condition:
            return any(
                pending is not None and pending.get("stock_origin") is not None
                and not pending.get("stock_claimed")
                for pending in (native.pending, native.compaction_pending)
            )
