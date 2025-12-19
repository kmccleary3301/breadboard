from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from .state.session_state import SessionState

_CURRENT_SESSION_STATE: ContextVar[Optional[SessionState]] = ContextVar("kyle_current_session_state", default=None)


def get_current_session_state() -> Optional[SessionState]:
    return _CURRENT_SESSION_STATE.get()


@contextmanager
def bind_session_state(session_state: SessionState) -> Iterator[None]:
    token = _CURRENT_SESSION_STATE.set(session_state)
    try:
        yield
    finally:
        _CURRENT_SESSION_STATE.reset(token)

