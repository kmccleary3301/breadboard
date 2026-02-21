#!/usr/bin/env python3
"""
Centralized, versioned render profiles for tmux ANSI->PNG conversion.

This lets replay capture tooling pin a deterministic profile and record that
profile id in capture metadata for drift triage.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LATEST_LOCKED_PHASE4_PROFILE_ID = "phase4_locked_v5"
LOCKED_PHASE4_PROFILE_ID = "phase4_locked_v3"
NEXT_LOCKED_PHASE4_PROFILE_ID = "phase4_locked_v4"
PREVIOUS_LOCKED_PHASE4_PROFILE_ID = "phase4_locked_v2"
LEGACY_LOCKED_PHASE4_PROFILE_ID = "phase4_locked_v1"
LEGACY_PROFILE_ID = "legacy"
DEFAULT_RENDER_PROFILE_ID = LATEST_LOCKED_PHASE4_PROFILE_ID
SUPPORTED_RENDER_PROFILES: tuple[str, ...] = (
    LATEST_LOCKED_PHASE4_PROFILE_ID,
    NEXT_LOCKED_PHASE4_PROFILE_ID,
    LOCKED_PHASE4_PROFILE_ID,
    PREVIOUS_LOCKED_PHASE4_PROFILE_ID,
    LEGACY_LOCKED_PHASE4_PROFILE_ID,
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
    baseline_offset: int
    underline_offset: int
    font_path: str
    font_bold_path: str
    font_italic_path: str
    font_bold_italic_path: str
    font_path_sha256: str
    font_bold_path_sha256: str
    font_italic_path_sha256: str
    font_bold_italic_path_sha256: str


LATEST_LOCKED_PHASE4_PROFILE = RenderProfile(
    id=LATEST_LOCKED_PHASE4_PROFILE_ID,
    description=(
        "Deterministic phase4 replay profile locked for VSCode-terminal parity with Consolas "
        "assets (v5)."
    ),
    renderer="pillow",
    bg="1f2430",
    fg="f1f5f9",
    # Consolas lock; geometry inherited from v4 candidate calibration.
    font_size=14,
    scale=1.0,
    cell_width=9,
    cell_height=22,
    line_gap=0,
    pad_x=0,
    pad_y=1,
    baseline_offset=17,
    underline_offset=2,
    font_path="renderer_assets/fonts/Consolas-Regular.ttf",
    font_bold_path="renderer_assets/fonts/Consolas-Bold.ttf",
    font_italic_path="renderer_assets/fonts/Consolas-Italic.ttf",
    font_bold_italic_path="renderer_assets/fonts/Consolas Bold Italic.ttf",
    font_path_sha256="5f8d58e719a7d724be036145f506acf38b0942e253a7c331887c7056b93deac8",
    font_bold_path_sha256="5295a046a10ef96b7050891920212295ee8e86e4dd8177ceb6cf054ec1dba5f1",
    font_italic_path_sha256="9de4ef7b2cb04285224a23c1b7725a80a3852d321a6006b1a22b559cd5450ae9",
    font_bold_italic_path_sha256="fc9e9f0b6ae6d068b01e1511e475b508e15799529f53679f1e927f5418040c3f",
)

LOCKED_PHASE4_PROFILE = RenderProfile(
    id=LOCKED_PHASE4_PROFILE_ID,
    description="Deterministic phase4 replay profile locked for ground-truth calibrated capture stability (v3).",
    renderer="pillow",
    bg="1f2430",
    fg="f1f5f9",
    # Ground-truth calibrated geometry for current tmux fullpane targets.
    font_size=15,
    scale=1.0,
    cell_width=9,
    cell_height=22,
    line_gap=0,
    pad_x=0,
    pad_y=1,
    baseline_offset=16,
    underline_offset=2,
    font_path="renderer_assets/fonts/DejaVuSansMono.ttf",
    font_bold_path="renderer_assets/fonts/DejaVuSansMono-Bold.ttf",
    font_italic_path="renderer_assets/fonts/DejaVuSansMono-Oblique.ttf",
    font_bold_italic_path="renderer_assets/fonts/DejaVuSansMono-BoldOblique.ttf",
    font_path_sha256="39c29931201f08dd89fdba4129c76288e6baeecf7c94fe4f6a757f2b50718b1b",
    font_bold_path_sha256="f8aa70b5d4210d77624f5b5b5cf094fadf9fd019caf5b528fa5af42fc4ac43a5",
    font_italic_path_sha256="6df35684aa7ed7060354c946464d6e047a957d307c3aef4c7bc87aee9e33d729",
    font_bold_italic_path_sha256="166f485cb39da15fb04de913e7572a76739ba9580a78ef876f97b593e42fd376",
)

NEXT_LOCKED_PHASE4_PROFILE = RenderProfile(
    id=NEXT_LOCKED_PHASE4_PROFILE_ID,
    description="Deterministic phase4 replay profile locked for improved ground-truth alignment stability (v4 candidate).",
    renderer="pillow",
    bg="1f2430",
    fg="f1f5f9",
    # Candidate tuned against live ground-truth tmux sessions.
    font_size=14,
    scale=1.0,
    cell_width=9,
    cell_height=22,
    line_gap=0,
    pad_x=0,
    pad_y=1,
    baseline_offset=17,
    underline_offset=2,
    font_path="renderer_assets/fonts/DejaVuSansMono.ttf",
    font_bold_path="renderer_assets/fonts/DejaVuSansMono-Bold.ttf",
    font_italic_path="renderer_assets/fonts/DejaVuSansMono-Oblique.ttf",
    font_bold_italic_path="renderer_assets/fonts/DejaVuSansMono-BoldOblique.ttf",
    font_path_sha256="39c29931201f08dd89fdba4129c76288e6baeecf7c94fe4f6a757f2b50718b1b",
    font_bold_path_sha256="f8aa70b5d4210d77624f5b5b5cf094fadf9fd019caf5b528fa5af42fc4ac43a5",
    font_italic_path_sha256="6df35684aa7ed7060354c946464d6e047a957d307c3aef4c7bc87aee9e33d729",
    font_bold_italic_path_sha256="166f485cb39da15fb04de913e7572a76739ba9580a78ef876f97b593e42fd376",
)

PREVIOUS_LOCKED_PHASE4_PROFILE = RenderProfile(
    id=PREVIOUS_LOCKED_PHASE4_PROFILE_ID,
    description="Deterministic phase4 replay profile locked for visual capture stability (v2).",
    renderer="pillow",
    bg="1f2430",
    fg="f1f5f9",
    font_size=18,
    scale=1.0,
    cell_width=11,
    cell_height=22,
    line_gap=0,
    pad_x=0,
    pad_y=0,
    baseline_offset=17,
    underline_offset=2,
    font_path="renderer_assets/fonts/DejaVuSansMono.ttf",
    font_bold_path="renderer_assets/fonts/DejaVuSansMono-Bold.ttf",
    font_italic_path="renderer_assets/fonts/DejaVuSansMono-Oblique.ttf",
    font_bold_italic_path="renderer_assets/fonts/DejaVuSansMono-BoldOblique.ttf",
    font_path_sha256="39c29931201f08dd89fdba4129c76288e6baeecf7c94fe4f6a757f2b50718b1b",
    font_bold_path_sha256="f8aa70b5d4210d77624f5b5b5cf094fadf9fd019caf5b528fa5af42fc4ac43a5",
    font_italic_path_sha256="6df35684aa7ed7060354c946464d6e047a957d307c3aef4c7bc87aee9e33d729",
    font_bold_italic_path_sha256="166f485cb39da15fb04de913e7572a76739ba9580a78ef876f97b593e42fd376",
)

LEGACY_LOCKED_PHASE4_PROFILE = RenderProfile(
    id=LEGACY_LOCKED_PHASE4_PROFILE_ID,
    description="Legacy locked phase4 profile (kept for backward compatibility of prior artifacts).",
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
    baseline_offset=-1,
    underline_offset=-1,
    font_path="renderer_assets/fonts/DejaVuSansMono.ttf",
    font_bold_path="renderer_assets/fonts/DejaVuSansMono-Bold.ttf",
    font_italic_path="renderer_assets/fonts/DejaVuSansMono-Oblique.ttf",
    font_bold_italic_path="renderer_assets/fonts/DejaVuSansMono-BoldOblique.ttf",
    font_path_sha256="39c29931201f08dd89fdba4129c76288e6baeecf7c94fe4f6a757f2b50718b1b",
    font_bold_path_sha256="f8aa70b5d4210d77624f5b5b5cf094fadf9fd019caf5b528fa5af42fc4ac43a5",
    font_italic_path_sha256="6df35684aa7ed7060354c946464d6e047a957d307c3aef4c7bc87aee9e33d729",
    font_bold_italic_path_sha256="166f485cb39da15fb04de913e7572a76739ba9580a78ef876f97b593e42fd376",
)

PROFILES_BY_ID: dict[str, RenderProfile] = {
    LATEST_LOCKED_PHASE4_PROFILE_ID: LATEST_LOCKED_PHASE4_PROFILE,
    NEXT_LOCKED_PHASE4_PROFILE_ID: NEXT_LOCKED_PHASE4_PROFILE,
    LOCKED_PHASE4_PROFILE_ID: LOCKED_PHASE4_PROFILE,
    PREVIOUS_LOCKED_PHASE4_PROFILE_ID: PREVIOUS_LOCKED_PHASE4_PROFILE,
    LEGACY_LOCKED_PHASE4_PROFILE_ID: LEGACY_LOCKED_PHASE4_PROFILE,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runtime_asset_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)).resolve())
    roots.append(_repo_root())
    return roots


def _resolve_asset_path(path_value: str) -> Path:
    raw = Path(path_value)
    if raw.is_absolute():
        return raw.resolve()
    for root in _runtime_asset_roots():
        candidate = (root / raw).resolve()
        if candidate.exists():
            return candidate
    # Keep deterministic fallback even when running before assets are unpacked.
    return (_runtime_asset_roots()[0] / raw).resolve()


def resolve_profile_font_paths(profile: RenderProfile) -> dict[str, Path]:
    return {
        "font_path": _resolve_asset_path(profile.font_path),
        "font_bold_path": _resolve_asset_path(profile.font_bold_path),
        "font_italic_path": _resolve_asset_path(profile.font_italic_path),
        "font_bold_italic_path": _resolve_asset_path(profile.font_bold_italic_path),
    }


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_render_profile_lock(profile: RenderProfile) -> list[str]:
    errors: list[str] = []
    font_paths = resolve_profile_font_paths(profile)
    expected = {
        "font_path": profile.font_path_sha256,
        "font_bold_path": profile.font_bold_path_sha256,
        "font_italic_path": profile.font_italic_path_sha256,
        "font_bold_italic_path": profile.font_bold_italic_path_sha256,
    }
    for key, path in font_paths.items():
        if not path.exists():
            errors.append(f"{profile.id}: missing locked font file for {key}: {path}")
            continue
        actual = _sha256_file(path)
        if actual != expected[key]:
            errors.append(
                f"{profile.id}: checksum mismatch for {key}: expected {expected[key]}, got {actual}"
            )
    return errors


def assert_render_profile_lock(profile: RenderProfile) -> None:
    errors = validate_render_profile_lock(profile)
    if errors:
        raise ValueError("render profile lock check failed:\n- " + "\n- ".join(errors))


def profile_lock_manifest(profile: RenderProfile) -> dict[str, Any]:
    font_paths = resolve_profile_font_paths(profile)
    payload = asdict(profile)
    payload["resolved_font_paths"] = {k: str(v) for k, v in font_paths.items()}
    payload["default_render_profile_id"] = DEFAULT_RENDER_PROFILE_ID
    payload["supported_render_profiles"] = list(SUPPORTED_RENDER_PROFILES)
    return payload


def normalize_render_profile_id(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "default"}:
        return DEFAULT_RENDER_PROFILE_ID
    return normalized


def resolve_render_profile(value: str | None) -> RenderProfile | None:
    profile_id = normalize_render_profile_id(value)
    if profile_id == LEGACY_PROFILE_ID:
        return None
    if profile_id not in PROFILES_BY_ID:
        raise ValueError(
            f"unsupported render profile {value!r}; expected one of {SUPPORTED_RENDER_PROFILES}"
        )
    return PROFILES_BY_ID[profile_id]


def append_profile_cli_args(base_cmd: list[str], profile: RenderProfile | None) -> list[str]:
    if profile is None:
        return list(base_cmd)
    resolved_fonts = resolve_profile_font_paths(profile)
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
        "--font-path",
        str(resolved_fonts["font_path"]),
        "--font-bold-path",
        str(resolved_fonts["font_bold_path"]),
        "--font-italic-path",
        str(resolved_fonts["font_italic_path"]),
        "--font-bold-italic-path",
        str(resolved_fonts["font_bold_italic_path"]),
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
        "--baseline-offset",
        str(profile.baseline_offset),
        "--underline-offset",
        str(profile.underline_offset),
    ]
