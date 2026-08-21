from __future__ import annotations

from contextlib import contextmanager
from itertools import count
from typing import Iterator
from unittest.mock import patch
import uuid


@contextmanager
def deterministic_self_capture_context(generated_at_utc: str) -> Iterator[None]:
    """Make the narrow self-capture run reproducible without post-processing records.

    The self-capture lane intentionally executes the real AgenticCoder runtime, but
    its evidence hashes should not change only because UUIDs and clocks changed.
    This context is scoped to that one capture and leaves production runtime
    emission untouched.
    """

    uuid_counter = count(1)
    time_counter = count(0)

    def deterministic_uuid4() -> uuid.UUID:
        return uuid.UUID(int=next(uuid_counter))

    def deterministic_time() -> float:
        return 1_783_460_000.0 + (next(time_counter) / 1000.0)

    def deterministic_utc_now() -> str:
        return generated_at_utc

    with (
        patch("time.time", deterministic_time),
        patch("agentic_coder_prototype.runtime.kernel_emitter._utc_now", deterministic_utc_now),
        patch("agentic_coder_prototype.orchestration.coordination.uuid.uuid4", deterministic_uuid4),
    ):
        yield
