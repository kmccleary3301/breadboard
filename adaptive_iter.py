from __future__ import annotations

from typing import Any, Iterable, Iterator, Tuple

ADAPTIVE_PREFIX_ITERABLE = "__adaptive_iterable__"
ADAPTIVE_PREFIX_NON_ITERABLE = "__adaptive_scalar__"


def _is_iterable(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes, dict)):
        return False
    return hasattr(value, "__iter__")


def encode_adaptive_iterable(value: Any) -> Iterator[Any]:
    """
    Prefix an iterable stream so the receiver can distinguish streaming
    iterables from single scalar payloads.
    """
    if _is_iterable(value):
        yield ADAPTIVE_PREFIX_ITERABLE
        for item in value:
            yield item
        return
    yield ADAPTIVE_PREFIX_NON_ITERABLE
    yield value


def decode_adaptive_iterable(items: Any) -> Tuple[bool, Any]:
    """
    Decode an adaptive iterable stream.
    Returns (is_iterable, value_or_iterable).
    """
    if items is None:
        return False, None
    if isinstance(items, list):
        if not items:
            return False, None
        head = items[0]
        if head == ADAPTIVE_PREFIX_ITERABLE:
            return True, items[1:]
        if head == ADAPTIVE_PREFIX_NON_ITERABLE:
            return False, items[1] if len(items) > 1 else None
        return True, items

    if _is_iterable(items):
        it = iter(items)
        try:
            head = next(it)
        except StopIteration:
            return True, iter(())
        if head == ADAPTIVE_PREFIX_ITERABLE:
            return True, it
        if head == ADAPTIVE_PREFIX_NON_ITERABLE:
            try:
                payload = next(it)
            except StopIteration:
                payload = None
            return False, payload

        def _chain() -> Iterator[Any]:
            yield head
            for item in it:
                yield item

        return True, _chain()

    return False, items
