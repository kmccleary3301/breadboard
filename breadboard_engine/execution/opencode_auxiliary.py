"""Cancellable HTTP forwarding for the stock-owned auxiliary sessions."""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
import threading

import httpx


class AuxiliaryCancelled(Exception):
    pass


@dataclass(eq=False)
class _Request:
    session_id: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    started: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    future: object = None


class AuxiliaryTransport:
    def __init__(self):
        self.lock = threading.RLock()
        self.close_lock = threading.Lock()
        self.requests: set[_Request] = set()
        self.loop = None
        self.thread = None
        self.closed = False

    def forward(self, session_id, url, headers, body):
        with self.lock:
            if self.closed:
                raise AuxiliaryCancelled()
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
                self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
                self.thread.start()
            request = _Request(session_id)
            self.requests.add(request)

            async def run():
                try:
                    request.started.set()
                    if request.cancelled.is_set():
                        raise AuxiliaryCancelled()
                    async with httpx.AsyncClient(timeout=600) as client:
                        response = await client.post(url, headers=headers, content=body)
                        return response.status_code, dict(response.headers), response.content
                finally:
                    request.finished.set()

            request.future = asyncio.run_coroutine_threadsafe(run(), self.loop)
        try:
            return request.future.result()
        except (CancelledError, asyncio.CancelledError):
            raise AuxiliaryCancelled() from None
        finally:
            if not request.finished.wait(5):
                raise RuntimeError(f"Auxiliary HTTP cleanup did not finish: {session_id}")
            with self.lock:
                self.requests.discard(request)

    def cancel(self, session_id):
        with self.lock:
            requests = [request for request in self.requests if request.session_id == session_id]
            for request in requests:
                request.cancelled.set()
                if request.started.is_set():
                    request.future.cancel()
        for request in requests:
            if not request.finished.wait(5):
                raise RuntimeError(f"Auxiliary provider cancellation did not finish: {session_id}")

    def close(self):
        with self.close_lock:
            return self._close_locked()

    def _close_locked(self):
        with self.lock:
            if self.closed:
                return set()
            self.closed = True
            sessions = {request.session_id for request in self.requests}
        for session_id in sessions:
            self.cancel(session_id)
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Auxiliary HTTP loop did not stop")
            self.loop.close()
        return sessions
