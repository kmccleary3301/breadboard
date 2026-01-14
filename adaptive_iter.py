"""Compatibility shim for engine imports expecting top-level adaptive_iter."""

from breadboard.adaptive_iter import (  # noqa: F401
    ADAPTIVE_PREFIX_ITERABLE,
    ADAPTIVE_PREFIX_NON_ITERABLE,
    decode_adaptive_iterable,
    drain_to_list_with_timeout,
    encode_adaptive_iterable,
)

