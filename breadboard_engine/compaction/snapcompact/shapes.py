"""Snapcompact frame shapes, provider billing arithmetic, and geometry."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional, Tuple


FRAME_TOKEN_ESTIMATE = 5024
FRAME_DATA_BYTES_BUDGET = 1_500_000
FRAME_DATA_BYTES_ESTIMATE = 55_000
MAX_FRAMES_DEFAULT = 80
DOC_GUTTER = 3

DEFAULT_PROVIDER_IMAGE_BUDGET = 20
PROVIDER_IMAGE_BUDGETS: Mapping[str, int] = {
    "anthropic": 100,
    "bedrock": 100,
    "google": 50,
    "google-vertex": 50,
    "openai": 50,
    "azure": 50,
    "github-copilot": 50,
    "openrouter": 100,
}


def provider_image_budget(provider: Optional[str]) -> int:
    if provider is not None and provider in PROVIDER_IMAGE_BUDGETS:
        return PROVIDER_IMAGE_BUDGETS[provider]
    return DEFAULT_PROVIDER_IMAGE_BUDGET


def provider_frame_budget(provider: Optional[str]) -> int:
    return min(provider_image_budget(provider), MAX_FRAMES_DEFAULT)


def max_frames_for_data_budget(max_frame_data_bytes: int = FRAME_DATA_BYTES_BUDGET) -> int:
    return max(1, math.floor(max_frame_data_bytes / FRAME_DATA_BYTES_ESTIMATE))


def frame_data_bytes(frames: Any) -> int:
    return sum(len(f.get("data", "") if isinstance(f, Mapping) else getattr(f, "data", "")) for f in frames)


@dataclass(frozen=True)
class ShapeGeometry:
    font: str
    cell_width: int
    cell_height: int
    variant: str = "bw"  # "bw" | "sent"
    line_repeat: int = 1
    frame_size: int = 1568
    stretch: Optional[bool] = None
    stopword_dim: bool = False
    columns: Optional[int] = None


@dataclass(frozen=True)
class Shape(ShapeGeometry):
    frame_token_estimate: int = FRAME_TOKEN_ESTIMATE
    image_detail: Optional[str] = None


@dataclass(frozen=True)
class Geometry:
    cols: int
    rows: int
    capacity: int


SHAPE_VARIANTS: Mapping[str, ShapeGeometry] = {
    "8x8r-bw": ShapeGeometry(font="8x8", cell_width=8, cell_height=8, variant="bw", line_repeat=2, frame_size=1568),
    "8x8r-sent": ShapeGeometry(font="8x8", cell_width=8, cell_height=8, variant="sent", line_repeat=2, frame_size=1568),
    "8x8u-bw": ShapeGeometry(font="8x8", cell_width=8, cell_height=8, variant="bw", line_repeat=1, frame_size=1568),
    "8x8u-sent": ShapeGeometry(font="8x8", cell_width=8, cell_height=8, variant="sent", line_repeat=1, frame_size=1568),
    "6x6u-bw": ShapeGeometry(font="8x8", cell_width=6, cell_height=6, variant="bw", line_repeat=1, frame_size=1568),
    "6x6u-sent": ShapeGeometry(font="8x8", cell_width=6, cell_height=6, variant="sent", line_repeat=1, frame_size=1568),
    "5x8-bw": ShapeGeometry(font="5x8", cell_width=5, cell_height=8, variant="bw", line_repeat=1, frame_size=2576),
    "5x8-sent": ShapeGeometry(font="5x8", cell_width=5, cell_height=8, variant="sent", line_repeat=1, frame_size=2576),
    "6x12-dim": ShapeGeometry(font="6x12", cell_width=6, cell_height=12, variant="bw", stopword_dim=True, line_repeat=1, frame_size=1568),
    "8x13-bw": ShapeGeometry(font="8x13", cell_width=8, cell_height=13, variant="bw", line_repeat=1, frame_size=1568),
    "8on16-bw": ShapeGeometry(font="8x13", cell_width=8, cell_height=16, stretch=False, variant="bw", line_repeat=1, frame_size=1568),
    "8on22-bw": ShapeGeometry(font="8x13", cell_width=8, cell_height=22, stretch=False, variant="bw", line_repeat=1, frame_size=1568),
    "11on16-bw": ShapeGeometry(font="8x13", cell_width=11, cell_height=16, stretch=False, variant="bw", line_repeat=1, frame_size=1568),
    "silver16-bw": ShapeGeometry(font="silver", cell_width=16, cell_height=16, stretch=False, variant="bw", line_repeat=1, frame_size=1568),
}

SHAPE_VARIANT_NAMES: Tuple[str, ...] = tuple(SHAPE_VARIANTS.keys())


def is_shape_variant_name(name: Any) -> bool:
    return isinstance(name, str) and name in SHAPE_VARIANTS


def is_shape(obj: Any) -> bool:
    if not isinstance(obj, Shape):
        return False
    if obj.cell_width <= 0 or obj.cell_height <= 0 or obj.frame_size <= 0:
        return False
    if obj.variant not in ("bw", "sent"):
        return False
    return True


def billing_family(api: Optional[str]) -> str:
    if api in ("anthropic-messages", "bedrock-converse-stream"):
        return "anthropic"
    if api in ("openai-completions", "openai-responses", "openai-codex-responses", "azure-openai-responses"):
        return "openai"
    if api in ("google-generative-ai", "google-gemini-cli", "google-vertex"):
        return "google"
    return "unknown"


def family_billing(family: str, frame_size: int) -> Tuple[int, Optional[str]]:
    if family == "google":
        return (1120, None)
    if family == "openai":
        patches = min(math.ceil(frame_size / 32) ** 2, 10_000)
        return (math.ceil(patches * 1.2), "original")
    patches = min(math.ceil(frame_size / 28) ** 2, 4784)
    return (math.ceil(patches * 1.05), None)


def price_shape(base: ShapeGeometry, family: str) -> Shape:
    tokens, detail = family_billing(family, base.frame_size)
    return Shape(
        font=base.font,
        cell_width=base.cell_width,
        cell_height=base.cell_height,
        variant=base.variant,
        line_repeat=base.line_repeat,
        frame_size=base.frame_size,
        stretch=base.stretch,
        stopword_dim=base.stopword_dim,
        columns=base.columns,
        frame_token_estimate=tokens,
        image_detail=detail,
    )


SHAPES = {
    "anthropic": price_shape(SHAPE_VARIANTS["11on16-bw"], "anthropic"),
    "google": price_shape(
        ShapeGeometry(
            font="8x13", cell_width=8, cell_height=22, stretch=False, variant="bw", line_repeat=1, frame_size=2048
        ),
        "google",
    ),
    "openai": price_shape(SHAPE_VARIANTS["8on22-bw"], "openai"),
    "legacy": price_shape(SHAPE_VARIANTS["5x8-sent"], "anthropic"),
}


def ideal_shape_variant(model_id: str) -> Optional[Tuple[str, int]]:
    """Return (variant_name, frame_size) if model has an ideal shape."""
    mid = model_id.lower()
    # High-res Claude lines: opus 4.7+, fable, mythos get 1932px
    if "claude" in mid:
        if "fable" in mid or "mythos" in mid:
            return ("11on16-bw", 1932)
        # Check opus version
        if "opus" in mid:
            import re
            m = re.search(r"opus[^\d]*(\d+)[._-](\d+)", mid)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                if (major > 4) or (major == 4 and minor >= 7):
                    return ("11on16-bw", 1932)
            else:
                m_maj = re.search(r"opus[^\d]*(\d+)", mid)
                if m_maj and int(m_maj.group(1)) >= 5:
                    return ("11on16-bw", 1932)
    if "gemini" in mid:
        return ("8on22-bw", 2048)
    if "kimi" in mid:
        return ("8on22-bw", 1568)
    if "glm" in mid:
        return ("8on16-bw", 1568)
    return None


def resolve_shape(
    model: Optional[Any] = None,
    variant: Optional[str] = None,
    *,
    api: Optional[str] = None,
    model_id: Optional[str] = None,
) -> Shape:
    if model is not None:
        if hasattr(model, "api"):
            api = getattr(model, "api", None) or api
        elif isinstance(model, Mapping):
            api = model.get("api") or api
        if hasattr(model, "id"):
            model_id = getattr(model, "id", None) or model_id
        elif hasattr(model, "model"):
            model_id = getattr(model, "model", None) or model_id
        elif isinstance(model, Mapping):
            model_id = model.get("id") or model.get("model") or model_id

    family = billing_family(api)
    if variant and variant != "auto" and variant in SHAPE_VARIANTS:
        return price_shape(SHAPE_VARIANTS[variant], family)

    if model_id:
        ideal = ideal_shape_variant(model_id)
        if ideal is not None:
            v_name, f_size = ideal
            base = SHAPE_VARIANTS[v_name]
            if f_size != base.frame_size:
                base = ShapeGeometry(
                    font=base.font,
                    cell_width=base.cell_width,
                    cell_height=base.cell_height,
                    variant=base.variant,
                    line_repeat=base.line_repeat,
                    frame_size=f_size,
                    stretch=base.stretch,
                    stopword_dim=base.stopword_dim,
                    columns=base.columns,
                )
            return price_shape(base, family)

    if family == "anthropic":
        return SHAPES["anthropic"]
    if family == "google":
        return SHAPES["google"]
    if family == "openai":
        return SHAPES["openai"]
    # Unknown fallback: 8on22-bw with unknown family billing
    return price_shape(SHAPE_VARIANTS["8on22-bw"], "unknown")


def geometry(shape: ShapeGeometry, size: Optional[int] = None) -> Geometry:
    s = size if size is not None else shape.frame_size
    grid_cols = s // shape.cell_width
    rows = s // shape.cell_height // max(1, shape.line_repeat)
    if shape.columns == 2:
        col_w = max(0, grid_cols - DOC_GUTTER) // 2
        return Geometry(cols=col_w, rows=rows, capacity=col_w * rows * 2)
    return Geometry(cols=grid_cols, rows=rows, capacity=grid_cols * rows)
