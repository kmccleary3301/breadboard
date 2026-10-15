"""Preserve root order dynamically captured by an owning stock Responses SDK."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_SOURCE_FIELD_ORDER: ContextVar[tuple[str, ...] | None] = ContextVar("source_responses_field_order", default=None)


def source_field_order() -> tuple[str, ...] | None:
    return _SOURCE_FIELD_ORDER.get()


def project_source_request(body: dict, field_order: tuple[str, ...]) -> dict:
    """Reorder existing root members only; retain every value and nested order."""
    projected = {name: body[name] for name in field_order if name in body}
    projected.update((name, value) for name, value in body.items() if name not in projected)
    return projected


@contextmanager
def source_responses_wire_scope(field_order: tuple[str, ...] | None) -> Iterator[None]:
    token = _SOURCE_FIELD_ORDER.set(field_order)
    try:
        yield
    finally:
        _SOURCE_FIELD_ORDER.reset(token)
