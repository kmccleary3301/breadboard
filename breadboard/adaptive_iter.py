import base64
import pickle
import time
from collections.abc import Iterable, Iterator
from typing import Any, Generator, Tuple, Union


ADAPTIVE_PREFIX_ITERABLE = ">>>>> ITERABLE"
ADAPTIVE_PREFIX_NON_ITERABLE = ">>>>> NON-ITERABLE"


def _is_iterable_but_not_textlike(value: Any) -> bool:
    if isinstance(value, (str, bytes, bytearray)):
        return False
    # Treat dict as non-iterable for our purposes (avoid streaming keys)
    if isinstance(value, dict):
        return False
    return isinstance(value, Iterable)


def encode_adaptive_iterable(value: Union[Iterable[Any], Any]) -> Generator[Any, None, None]:
    """
    Wrap any value into a generator with an adaptive header so the receiver can
    tell whether to stream or reconstruct a single object.

    Yields:
      - First item: one of ADAPTIVE_PREFIX_ITERABLE or ADAPTIVE_PREFIX_NON_ITERABLE
      - If iterable: subsequent items are the original iterable's items
      - If non-iterable: second item is a base64-encoded pickle of the object
    """

    if _is_iterable_but_not_textlike(value):
        yield ADAPTIVE_PREFIX_ITERABLE
        for item in value:  # type: ignore[assignment]
            yield item
        return

    yield ADAPTIVE_PREFIX_NON_ITERABLE
    payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    yield base64.b64encode(payload).decode("ascii")


def decode_adaptive_iterable(stream: Iterable[Any]) -> Tuple[bool, Union[Iterator[Any], Any]]:
    """
    Given an adaptive stream (as produced by encode_adaptive_iterable), return a
    tuple (is_iterable, value). If is_iterable is True, value is an iterator over
    the subsequent items. Otherwise, value is the reconstructed original object.
    """
    iterator = iter(stream)
    try:
        header = next(iterator)
    except StopIteration:
        raise ValueError("Adaptive stream is empty; expected header prefix")

    if header == ADAPTIVE_PREFIX_ITERABLE:
        def tail_iter() -> Generator[Any, None, None]:
            for item in iterator:
                yield item

        return True, tail_iter()

    if header == ADAPTIVE_PREFIX_NON_ITERABLE:
        try:
            b64 = next(iterator)
        except StopIteration:
            raise ValueError("Adaptive stream missing payload for NON-ITERABLE")
        data = base64.b64decode(b64)
        obj = pickle.loads(data)
        return False, obj

    raise ValueError(f"Unrecognized adaptive header: {header}")


def drain_to_list_with_timeout(stream: Iterable[Any], timeout_seconds: float | None = None) -> list[Any]:
    """
    Utility to drain an adaptive-encoded iterable (or any iterable) into a list with
    an optional timeout. If timeout is reached, stops draining and returns what it has.
    """
    results: list[Any] = []
    start = time.monotonic()
    for item in stream:
        results.append(item)
        if timeout_seconds is not None and (time.monotonic() - start) > timeout_seconds:
            break
    return results


