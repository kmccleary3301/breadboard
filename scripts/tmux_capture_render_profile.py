#!/usr/bin/env python3
"""
Centralized, versioned render profiles for tmux ANSI->PNG conversion.

This lets replay capture tooling pin a deterministic profile and record that
profile id in capture metadata for drift triage.
"""

from __future__ import annotations

from dataclasses import dataclass


LOCKED_PHASE4_PROFILE_ID = "phase4_locked_v1"
LEGACY_PROFILE_ID = "legacy"
DEFAULT_RENDER_PROFILE_ID = LOCKED_PHASE4_PROFILE_ID
SUPPORTED_RENDER_PROFILES: tuple[str, ...] = (
    LOCKED_PHASE4_PROFILE_ID,
    LEGACY_PROFILE_ID,
)


@dataclass(frozen=True)
class RenderProfile:
    id: str
    description: str
    renderer: str
    bg: str
    fg: str
    font_size: int
    scale: float
    cell_width: int
    cell_height: int
    line_gap: int
    pad_x: int
    pad_y: int


LOCKED_PHASE4_PROFILE = RenderProfile(
    id=LOCKED_PHASE4_PROFILE_ID,
    description="Deterministic phase4 replay profile locked for visual capture stability.",
    renderer="pillow",
    bg="1f2430",
    fg="f1f5f9",
    font_size=18,
    scale=1.0,
    cell_width=0,
    cell_height=0,
    line_gap=0,
    pad_x=0,
    pad_y=0,
)


def normalize_render_profile_id(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "default"}:
        return DEFAULT_RENDER_PROFILE_ID
    return normalized


def resolve_render_profile(value: str | None) -> RenderProfile | None:
    profile_id = normalize_render_profile_id(value)
    if profile_id == LEGACY_PROFILE_ID:
        return None
    if profile_id != LOCKED_PHASE4_PROFILE_ID:
        raise ValueError(
            f"unsupported render profile {value!r}; expected one of {SUPPORTED_RENDER_PROFILES}"
        )
    return LOCKED_PHASE4_PROFILE


def append_profile_cli_args(base_cmd: list[str], profile: RenderProfile | None) -> list[str]:
    if profile is None:
        return list(base_cmd)
    return [
        *base_cmd,
        "--renderer",
        profile.renderer,
        "--bg",
        profile.bg,
        "--fg",
        profile.fg,
        "--font-size",
        str(profile.font_size),
        "--scale",
        str(profile.scale),
        "--cell-width",
        str(profile.cell_width),
        "--cell-height",
        str(profile.cell_height),
        "--line-gap",
        str(profile.line_gap),
        "--pad-x",
        str(profile.pad_x),
        "--pad-y",
        str(profile.pad_y),
    ]
