"""Snapcompact compaction package."""

from __future__ import annotations

from .archive import Archive, compact_archive, history_blocks
from .inline import apply_inline_snapcompact
from .method import SnapcompactCompaction
from .renderer import render_snapcompact_png, snapcompact_supported_chars
from .shapes import (
    FRAME_DATA_BYTES_BUDGET,
    FRAME_TOKEN_ESTIMATE,
    MAX_FRAMES_DEFAULT,
    SHAPE_VARIANTS,
    Shape,
    geometry,
    resolve_shape,
)

__all__ = [
    "SnapcompactCompaction",
    "apply_inline_snapcompact",
    "Archive",
    "compact_archive",
    "history_blocks",
    "render_snapcompact_png",
    "snapcompact_supported_chars",
    "Shape",
    "geometry",
    "resolve_shape",
    "FRAME_TOKEN_ESTIMATE",
    "FRAME_DATA_BYTES_BUDGET",
    "MAX_FRAMES_DEFAULT",
    "SHAPE_VARIANTS",
]
