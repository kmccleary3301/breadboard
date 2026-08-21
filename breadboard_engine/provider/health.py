from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Deque, Dict, Optional


@dataclass
class RouteHealthState:
    failures: Deque[float]
    successes: Deque[float]
    circuit_open_until: float = 0.0
    last_error: Optional[str] = None
    last_failure_ts: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "failures": list(self.failures),
            "successes": list(self.successes),
            "circuit_open_until": self.circuit_open_until,
            "last_error": self.last_error,
            "last_failure_ts": self.last_failure_ts,
        }


class RouteHealthManager:
    """Tracks provider route health and manages circuit breakers."""

    FAILURE_WINDOW_SECONDS = 600.0
    FAILURE_THRESHOLD = 3
    CIRCUIT_COOLDOWN_SECONDS = 900.0

    def __init__(self) -> None:
        self._routes: Dict[str, RouteHealthState] = {}

    def _get(self, route: str) -> RouteHealthState:
        state = self._routes.get(route)
        if state is None:
            state = RouteHealthState(failures=deque(), successes=deque())
            self._routes[route] = state
        return state

    def is_circuit_open(self, route: str, now: Optional[float] = None) -> bool:
        state = self._get(route)
        now = now or time.time()
        if state.circuit_open_until > now:
            return True
        return False

    def record_success(self, route: str, now: Optional[float] = None) -> None:
        now = now or time.time()
        state = self._get(route)
        state.successes.append(now)
        self._prune(state, now)
        if state.circuit_open_until and state.circuit_open_until <= now:
            state.circuit_open_until = 0.0
            state.last_error = None

    def record_failure(self, route: str, reason: str, now: Optional[float] = None) -> None:
        now = now or time.time()
        state = self._get(route)
        state.failures.append(now)
        state.last_error = reason
        state.last_failure_ts = now
        self._prune(state, now)
        if len(state.failures) >= self.FAILURE_THRESHOLD:
            if state.circuit_open_until < now + self.CIRCUIT_COOLDOWN_SECONDS:
                state.circuit_open_until = now + self.CIRCUIT_COOLDOWN_SECONDS

    def _prune(self, state: RouteHealthState, now: float) -> None:
        window = self.FAILURE_WINDOW_SECONDS
        while state.failures and now - state.failures[0] > window:
            state.failures.popleft()
        while state.successes and now - state.successes[0] > window:
            state.successes.popleft()

    def describe(self, route: str) -> Dict[str, object]:
        return self._get(route).to_dict()

    def snapshot(self) -> Dict[str, Dict[str, object]]:
        return {route: state.to_dict() for route, state in self._routes.items()}
