#!/usr/bin/env python3
"""
Compare one tmux capture run directory against a golden run directory.

Outputs:
- comparison_report.json (machine readable)
- comparison_report.md (human readable)

Exit codes:
- 0: comparison completed; no strict failure
- 2: strict mode failure (threshold breach)
- 3: invalid input or runtime error
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageChops, ImageStat
except Exception:  # pragma: no cover - optional runtime dependency
    Image = None  # type: ignore[assignment]
    ImageChops = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]


ANSI_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.IGNORECASE)
STAMP_RE = re.compile(r"\b\d{8}-\d{6}\b")
DURATION_RE = re.compile(r"\b\d+m\s+\d+s\b|\b\d+(?:\.\d+)?s\b")
ISO_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b")
TOKEN_ASSIGN_RE = re.compile(
    r"(api[-_]?key|authorization|bearer)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{8,}",
    re.IGNORECASE,
)
TOKEN_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.=]{12,}\b", re.IGNORECASE)
TOKEN_SK_RE = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b", re.IGNORECASE)

# Prompt line in Codex/Claude tends to be either:
# - "› Try \"...\""
# - "❯ /command ..."
# - a bare prompt like "❯" (often with trailing spaces that tmux capture may rstrip)
PROMPT_LINE_RE = re.compile(r"^\s*[›❯>](?:\s+.*)?$")
CONTEXT_LEFT_RE = re.compile(r"\b\d{1,3}%\s+context\s+left\b", re.IGNORECASE)
WORKING_RE = re.compile(r"\bWorking\s*\([^)]*\)")
SHORTCUTS_RE = re.compile(r"\?\s+for\s+shortcuts\b", re.IGNORECASE)
BYPASS_PERMS_RE = re.compile(r"\bbypass\s+permissions\s+on\b", re.IGNORECASE)
ESC_INTERRUPT_RE = re.compile(r"\besc\s+to\s+interrupt\b", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalize_text(text: str) -> str:
    text = strip_ansi(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = UUID_RE.sub("<uuid>", text)
    text = STAMP_RE.sub("<stamp>", text)
    text = ISO_TS_RE.sub("<iso_ts>", text)
    text = DURATION_RE.sub("<duration>", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def redact_text(text: str) -> str:
    text = TOKEN_ASSIGN_RE.sub(r"\1=<redacted>", text)
    text = TOKEN_BEARER_RE.sub("Bearer <redacted>", text)
    text = TOKEN_SK_RE.sub("<redacted>", text)
    return text


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_obj(item) for key, item in value.items()}
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid JSON for {label}: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def load_summary_with_legacy_fallback(run_dir: Path, manifest: dict[str, Any], label: str) -> dict[str, Any]:
    summary_path = run_dir / "run_summary.json"
    if summary_path.exists():
        return load_json(summary_path, label)
    # Legacy runs may only have scenario_manifest.json.
    return {
        "scenario": manifest.get("scenario"),
        "run_id": manifest.get("run_id"),
        "scenario_result": manifest.get("scenario_result", "unknown"),
        "actions_count": manifest.get("actions_count", 0),
        "semantic_failures_count": len(manifest.get("semantic_failures", []) or []),
    }


def discover_run_dir(path: Path) -> Path:
    if (path / "scenario_manifest.json").exists():
        return path
    candidates = sorted(
        [p.parent for p in path.rglob("scenario_manifest.json") if p.is_file()],
        key=lambda p: p.name,
    )
    if not candidates:
        raise FileNotFoundError(f"no scenario_manifest.json found under {path}")
    return candidates[-1]


def load_thresholds(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise ValueError(
            f"threshold file must be JSON-compatible YAML (YAML superset of JSON): {path} ({exc})"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("threshold config root must be an object")
    return payload


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def first_existing_text_frame(run_dir: Path) -> str:
    frames_dir = run_dir / "frames"
    if not frames_dir.exists():
        return ""
    for txt_file in sorted(frames_dir.glob("frame_*.txt")):
        return txt_file.read_text(encoding="utf-8", errors="replace")
    return ""


def last_existing_text_frame(run_dir: Path) -> str:
    frames_dir = run_dir / "frames"
    if not frames_dir.exists():
        return ""
    txt_files = sorted(frames_dir.glob("frame_*.txt"))
    if not txt_files:
        return ""
    return txt_files[-1].read_text(encoding="utf-8", errors="replace")


def collect_frame_map(run_dir: Path) -> dict[int, str]:
    frames_dir = run_dir / "frames"
    out: dict[int, str] = {}
    if not frames_dir.exists():
        return out
    for txt_file in frames_dir.glob("frame_*.txt"):
        match = re.match(r"^frame_(\d{4})\.txt$", txt_file.name)
        if not match:
            continue
        idx = int(match.group(1))
        out[idx] = normalize_text(txt_file.read_text(encoding="utf-8", errors="replace"))
    return out


def collect_png_frame_map(run_dir: Path) -> dict[int, Path]:
    frames_dir = run_dir / "frames"
    out: dict[int, Path] = {}
    if not frames_dir.exists():
        return out
    for png_file in frames_dir.glob("frame_*.png"):
        match = re.match(r"^frame_(\d{4})\.png$", png_file.name)
        if not match:
            continue
        idx = int(match.group(1))
        out[idx] = png_file
    return out


def frame_overlap_window(run_indices: list[int], golden_indices: list[int]) -> tuple[int, int] | None:
    if not run_indices or not golden_indices:
        return None
    start = max(min(run_indices), min(golden_indices))
    end = min(max(run_indices), max(golden_indices))
    if start > end:
        return None
    return (start, end)


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _layout_normalize_line(line: str) -> str:
    """
    Normalize volatile UI elements that should not count as "layout drift":
    - prompt / suggestion lines (content varies between runs)
    - context-left counters (value varies)
    - active turn timers (string varies)
    """
    if CONTEXT_LEFT_RE.search(line):
        line = CONTEXT_LEFT_RE.sub("<pct>% context left", line)
    if WORKING_RE.search(line):
        line = WORKING_RE.sub("Working(<duration>)", line)
    if PROMPT_LINE_RE.match(line):
        # Avoid brittle compares over dynamic prompt suggestions like:
        #   › Try "fix lint errors"
        #   › Run /review on my current changes
        # Keep only the chevron to preserve indentation/placement.
        stripped = line.lstrip()
        if stripped.startswith("›"):
            return "› <prompt>"
        if stripped.startswith("❯"):
            return "❯ <prompt>"
        if stripped.startswith(">"):
            return "> <prompt>"
        return "<prompt>"
    return line


def layout_signature(text: str, *, provider: str, head_lines: int = 30, tail_lines: int = 30) -> str:
    """
    Generate a layout-focused signature from a full-frame capture.

    We intentionally avoid comparing the entire transcript content verbatim because
    provider UIs can include dynamic suggestions, counters, and non-deterministic
    assistant text. Instead, we focus on:
    - top chrome (header box / banners)
    - bottom chrome (composer/input + hints)
    - any box-drawing lines (strong indicator of wrapping/width changes)
    """
    # NOTE: Layout signatures should be stable across:
    # - differing transcript content (assistant text varies run-to-run)
    # - differing capture duration (extra frames at the beginning/end)
    # - minor prompt suggestion changes
    #
    # They should remain sensitive to:
    # - width/height regressions that cause wrapping/tearing of chrome
    # - composer/input bar structure changes (extra blank lines, missing borders)
    # - provider banner presence/absence
    normalized = normalize_text(text)
    lines = normalized.splitlines()
    if not lines:
        return ""

    box_chars = set("┌┐└┘─│╭╮╰╯")

    # 1) Identify a "composer window" around the last prompt line.
    # We use this both for stable layout comparisons and to detect the extra-blank-line
    # regression in the input bar area.
    composer_window: set[int] = set()
    last_prompt_index: int | None = None
    prompt_indices = [i for i, line in enumerate(lines) if PROMPT_LINE_RE.match(line)]
    if prompt_indices and tail_lines > 0:
        i_prompt = prompt_indices[-1]
        last_prompt_index = i_prompt
        tail_window = min(max(6, tail_lines // 3), 14)
        start = max(0, i_prompt - tail_window)
        end = min(len(lines), i_prompt + 8)
        composer_window = set(range(start, end))

    # 2) Identify a "header window" (top chrome) without pulling in transcript content.
    header_window: set[int] = set()
    if head_lines > 0:
        limit = min(head_lines, len(lines))
        for i in range(limit):
            line = lines[i]
            if any(ch in box_chars for ch in line):
                header_window.add(i)
                continue
            if "OpenAI Codex" in line or "Claude Code" in line:
                header_window.add(i)
                continue
            if "[codex-logged]" in line or "[claude-code-logged]" in line:
                header_window.add(i)
                continue
            if "Auth conflict" in line or "warning:" in line.lower():
                header_window.add(i)

    # 3) Collect lines that are strong indicators of layout/chrome, but only inside
    # the windows above. This avoids signature drift when transcript content changes
    # or when the view scrolls.
    important_idx: set[int] = set()
    for i, line in enumerate(lines):
        if i not in header_window and i not in composer_window:
            continue
        if any(ch in box_chars for ch in line):
            important_idx.add(i)
            continue
        if i in composer_window and last_prompt_index is not None and i == last_prompt_index:
            important_idx.add(i)
            continue
        if SHORTCUTS_RE.search(line) or CONTEXT_LEFT_RE.search(line):
            important_idx.add(i)
            continue
        if BYPASS_PERMS_RE.search(line) or ESC_INTERRUPT_RE.search(line):
            important_idx.add(i)
            continue
        if "OpenAI Codex" in line or "Claude Code" in line:
            important_idx.add(i)
            continue
        if "[codex-logged]" in line or "[claude-code-logged]" in line:
            important_idx.add(i)
            continue
        if "Auth conflict" in line or "warning:" in line.lower():
            important_idx.add(i)

    # 4) Build signature preserving order, and encoding blank-line gaps between
    # kept lines as "<empty>" markers so spacing regressions are visible.
    picked: list[str] = []
    last_kept: int | None = None
    for i in sorted(important_idx):
        if last_kept is not None:
            gap = i - last_kept - 1
            # Only encode small gaps; large gaps are usually transcript height drift.
            if 0 < gap <= 3:
                picked.extend(["<empty>"] * gap)
            elif gap > 3:
                picked.append("<...>")
        picked.append(_layout_normalize_line(lines[i]))
        last_kept = i

    # De-noise: drop consecutive duplicates (common when borders repeat).
    deduped: list[str] = []
    for line in picked:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)

    # Trim to a bounded size so diffs are readable and SequenceMatcher stays fast.
    if len(deduped) > (head_lines + tail_lines + 120):
        head = deduped[:head_lines]
        tail = deduped[-tail_lines:]
        return "\n".join(head + ["<...>"] + tail)
    return "\n".join(deduped)


def _load_rgba(path: Path):
    if Image is None:
        raise RuntimeError("Pillow unavailable")
    with Image.open(path) as img:
        return img.convert("RGBA")


def pixel_delta(path_a: Path, path_b: Path) -> dict[str, Any]:
    if Image is None or ImageChops is None or ImageStat is None:
        return {
            "available": False,
            "reason": "pillow_unavailable",
        }

    image_a = _load_rgba(path_a)
    image_b = _load_rgba(path_b)
    width = max(image_a.width, image_b.width)
    height = max(image_a.height, image_b.height)

    if image_a.size != (width, height):
        padded = Image.new("RGBA", (width, height), (15, 23, 42, 255))
        padded.paste(image_a, (0, 0))
        image_a = padded
    if image_b.size != (width, height):
        padded = Image.new("RGBA", (width, height), (15, 23, 42, 255))
        padded.paste(image_b, (0, 0))
        image_b = padded

    diff = ImageChops.difference(image_a, image_b)
    # Mean absolute per-channel RGB delta in [0, 1].
    mean_rgb = ImageStat.Stat(diff).mean[:3]
    mean_abs_diff = (mean_rgb[0] + mean_rgb[1] + mean_rgb[2]) / (3.0 * 255.0)
    changed_pixels = sum(1 for rgba in diff.getdata() if (rgba[0] | rgba[1] | rgba[2]) != 0)
    total_pixels = max(1, width * height)
    change_ratio = changed_pixels / total_pixels
    return {
        "available": True,
        "width": width,
        "height": height,
        "changed_pixels": changed_pixels,
        "total_pixels": total_pixels,
        "change_ratio": round(change_ratio, 6),
        "mean_abs_diff": round(mean_abs_diff, 6),
    }


def detect_provider(run_manifest: dict[str, Any], run_text: str, explicit: str) -> str:
    if explicit:
        return explicit.lower().strip()
    scenario = str(run_manifest.get("scenario", "")).lower()
    target = str(run_manifest.get("target", "")).lower()
    probe = f"{scenario}\n{target}\n{run_text[:2000]}".lower()
    if "claude" in probe:
        return "claude"
    if "codex" in probe or "openai codex" in probe:
        return "codex"
    return "unknown"


def default_thresholds() -> dict[str, Any]:
    return {
        "layout": {
            "max_drift_score": 0.35,
            "max_missing_frames": 0,
            "max_max_line_count_delta": 6,
        },
        "semantic": {
            "max_misses": 0,
            "max_action_count_delta": 1,
        },
        "provider": {
            "allow_banner_drift": False,
            "max_token_drifts": 0,
            "require_compaction_markers": True,
        },
        "pixel": {
            "enabled": False,
            "max_final_change_ratio": 0.12,
            "max_final_mean_abs_diff": 0.08,
        },
    }


@dataclass
class CompareResult:
    report: dict[str, Any]
    strict_fail: bool


def compare(
    *,
    run_dir: Path,
    golden_dir: Path,
    provider_hint: str,
    scenario_hint: str,
    thresholds_cfg: dict[str, Any],
    fail_mode: str,
    max_layout_drift: float | None,
    max_semantic_misses: int | None,
    allow_provider_banner_drift: bool | None,
    redact: bool = False,
    enable_pixel_checks: bool | None = None,
    max_pixel_change_ratio: float | None = None,
    max_pixel_mean_abs_diff: float | None = None,
) -> CompareResult:
    run_manifest = load_json(run_dir / "scenario_manifest.json", "run manifest")
    golden_manifest = load_json(golden_dir / "scenario_manifest.json", "golden manifest")
    run_summary = load_summary_with_legacy_fallback(run_dir, run_manifest, "run summary")
    golden_summary = load_summary_with_legacy_fallback(golden_dir, golden_manifest, "golden summary")

    run_frames = collect_frame_map(run_dir)
    golden_frames = collect_frame_map(golden_dir)
    run_indices = sorted(run_frames.keys())
    golden_indices = sorted(golden_frames.keys())
    common = sorted(set(run_indices) & set(golden_indices))

    run_final = normalize_text(last_existing_text_frame(run_dir))
    golden_final = normalize_text(last_existing_text_frame(golden_dir))
    run_first = normalize_text(first_existing_text_frame(run_dir))
    golden_first = normalize_text(first_existing_text_frame(golden_dir))

    if redact:
        run_final = redact_text(run_final)
        golden_final = redact_text(golden_final)
        run_first = redact_text(run_first)
        golden_first = redact_text(golden_first)

    provider = detect_provider(run_manifest, run_first, provider_hint)
    run_scenario = str(run_manifest.get("scenario", ""))
    golden_scenario = str(golden_manifest.get("scenario", ""))
    scenario_name = scenario_hint or run_scenario

    # threshold resolution: defaults -> file defaults -> provider -> scenario -> CLI
    merged = default_thresholds()
    merged = deep_merge(merged, thresholds_cfg.get("defaults", {}))
    if provider in thresholds_cfg.get("providers", {}):
        merged = deep_merge(merged, thresholds_cfg["providers"][provider])
    if scenario_name in thresholds_cfg.get("scenarios", {}):
        merged = deep_merge(merged, thresholds_cfg["scenarios"][scenario_name])
    if max_layout_drift is not None:
        merged.setdefault("layout", {})["max_drift_score"] = float(max_layout_drift)
    if max_semantic_misses is not None:
        merged.setdefault("semantic", {})["max_misses"] = int(max_semantic_misses)
    if allow_provider_banner_drift is not None:
        merged.setdefault("provider", {})["allow_banner_drift"] = bool(allow_provider_banner_drift)
    if enable_pixel_checks is not None:
        merged.setdefault("pixel", {})["enabled"] = bool(enable_pixel_checks)
    if max_pixel_change_ratio is not None:
        merged.setdefault("pixel", {})["max_final_change_ratio"] = float(max_pixel_change_ratio)
    if max_pixel_mean_abs_diff is not None:
        merged.setdefault("pixel", {})["max_final_mean_abs_diff"] = float(max_pixel_mean_abs_diff)

    # Layout metrics
    overlap = frame_overlap_window(run_indices, golden_indices)
    if overlap is None:
        overlap_indices: set[int] = set()
    else:
        overlap_indices = set(range(overlap[0], overlap[1] + 1))

    # Only treat missing frames as layout failures inside the overlap window.
    # Extra leading/trailing frames are usually duration/capture-window drift.
    run_missing = sorted(idx for idx in overlap_indices if idx not in run_frames)
    golden_missing = sorted(idx for idx in overlap_indices if idx not in golden_frames)
    run_extra = sorted(idx for idx in run_indices if idx not in overlap_indices)
    golden_extra = sorted(idx for idx in golden_indices if idx not in overlap_indices)
    # Use *composer-only* layout signatures (not raw frame line counts) so terminal
    # height/padding differences and transcript scroll state don't automatically fail
    # the layout check.
    line_deltas = []
    for idx in common:
        run_sig_lines = len(layout_signature(run_frames[idx], provider=provider, head_lines=0, tail_lines=30).splitlines())
        golden_sig_lines = len(
            layout_signature(golden_frames[idx], provider=provider, head_lines=0, tail_lines=30).splitlines()
        )
        line_deltas.append(abs(run_sig_lines - golden_sig_lines))
    max_line_delta = max(line_deltas) if line_deltas else 0
    mean_line_delta = (sum(line_deltas) / len(line_deltas)) if line_deltas else 0.0
    # Compare "final" layout at the *last captured frame* for each run. This is
    # more meaningful than aligning by frame indices when capture durations differ.
    final_similarity = similarity(
        layout_signature(run_final, provider=provider, head_lines=0, tail_lines=30),
        layout_signature(golden_final, provider=provider, head_lines=0, tail_lines=30),
    )
    head_similarity = similarity(
        layout_signature("\n".join(run_first.splitlines()[:80]), provider=provider, head_lines=30, tail_lines=0),
        layout_signature("\n".join(golden_first.splitlines()[:80]), provider=provider, head_lines=30, tail_lines=0),
    )
    layout_drift_score = round(1.0 - (0.7 * final_similarity + 0.3 * head_similarity), 4)

    # Semantic metrics
    golden_must_contain = tuple(str(x) for x in golden_manifest.get("must_contain", []) if str(x))
    golden_must_not = tuple(str(x) for x in golden_manifest.get("must_not_contain", []) if str(x))
    golden_regex = tuple(str(x) for x in golden_manifest.get("must_match_regex", []) if str(x))
    semantic_misses: list[str] = []
    for token in golden_must_contain:
        if token not in run_final:
            semantic_misses.append(f"missing must_contain token: {token!r}")
    for token in golden_must_not:
        if token in run_final:
            semantic_misses.append(f"forbidden token present: {token!r}")
    for pattern in golden_regex:
        try:
            if re.search(pattern, run_final, re.MULTILINE) is None:
                semantic_misses.append(f"missing regex match: {pattern!r}")
        except re.error as exc:
            semantic_misses.append(f"invalid regex in golden: {pattern!r} ({exc})")
    action_count_delta = abs(
        int(run_manifest.get("actions_count", run_summary.get("actions_count", 0)))
        - int(golden_manifest.get("actions_count", golden_summary.get("actions_count", 0)))
    )
    run_semantic_failures = int(run_summary.get("semantic_failures_count", 0))
    semantic_miss_count = len(semantic_misses) + run_semantic_failures

    # Provider metrics
    provider_tokens: dict[str, list[str]] = {
        "claude": ["Claude Code", "bypass permissions on"],
        "codex": ["OpenAI Codex", "context left"],
    }
    compaction_tokens = ["compact", "Conversation compacted", "Continue after compaction"]
    token_drifts: list[str] = []
    for token in provider_tokens.get(provider, []):
        run_has = token in run_first or token in run_final
        golden_has = token in golden_first or token in golden_final
        if run_has != golden_has:
            token_drifts.append(f"provider token drift: {token!r} (run={run_has}, golden={golden_has})")
    compaction_marker_missing: list[str] = []
    if merged.get("provider", {}).get("require_compaction_markers", True):
        for marker in compaction_tokens:
            if marker in golden_final and marker not in run_final:
                compaction_marker_missing.append(marker)

    # Status classification
    layout_failures = []
    if layout_drift_score > float(merged["layout"]["max_drift_score"]):
        layout_failures.append(
            f"layout_drift_score={layout_drift_score} > max_drift_score={merged['layout']['max_drift_score']}"
        )
    missing_frame_count = len(run_missing) + len(golden_missing)
    if missing_frame_count > int(merged["layout"]["max_missing_frames"]):
        layout_failures.append(
            f"missing_frame_count={missing_frame_count} > max_missing_frames={merged['layout']['max_missing_frames']}"
        )
    if max_line_delta > int(merged["layout"]["max_max_line_count_delta"]):
        layout_failures.append(
            f"max_line_delta={max_line_delta} > max_max_line_count_delta={merged['layout']['max_max_line_count_delta']}"
        )

    semantic_failures = []
    if run_scenario != golden_scenario:
        semantic_failures.append(f"scenario mismatch: run={run_scenario!r} golden={golden_scenario!r}")
    if semantic_miss_count > int(merged["semantic"]["max_misses"]):
        semantic_failures.append(
            f"semantic_miss_count={semantic_miss_count} > max_misses={merged['semantic']['max_misses']}"
        )
    if action_count_delta > int(merged["semantic"]["max_action_count_delta"]):
        semantic_failures.append(
            f"action_count_delta={action_count_delta} > max_action_count_delta={merged['semantic']['max_action_count_delta']}"
        )

    provider_failures = []
    allow_banner_drift = bool(merged["provider"]["allow_banner_drift"])
    if not allow_banner_drift and token_drifts:
        provider_failures.extend(token_drifts)
    if len(token_drifts) > int(merged["provider"]["max_token_drifts"]):
        provider_failures.append(
            f"token_drift_count={len(token_drifts)} > max_token_drifts={merged['provider']['max_token_drifts']}"
        )
    if compaction_marker_missing:
        provider_failures.append(f"missing compaction markers: {compaction_marker_missing}")

    # Pixel metrics (diagnostic by default, strict only when explicitly enabled).
    run_png = collect_png_frame_map(run_dir)
    golden_png = collect_png_frame_map(golden_dir)
    png_common = sorted(set(run_png.keys()) & set(golden_png.keys()))
    png_common_overlap = sorted(idx for idx in png_common if idx in overlap_indices)
    pixel_index: int | None = None
    if png_common_overlap:
        pixel_index = png_common_overlap[-1]
    elif png_common:
        pixel_index = png_common[-1]

    pixel_cfg = merged.get("pixel", {})
    pixel_enabled = bool(pixel_cfg.get("enabled", False))
    pixel_failures: list[str] = []
    pixel_metrics: dict[str, Any] = {
        "enabled": pixel_enabled,
        "status": "skip",
        "frame_index": pixel_index,
        "frame_count_run": len(run_png),
        "frame_count_golden": len(golden_png),
        "frame_count_common": len(png_common),
        "metrics": {},
        "failures": pixel_failures,
    }
    if pixel_enabled:
        if pixel_index is None:
            pixel_failures.append("no overlapping PNG frames for pixel comparison")
            pixel_metrics["status"] = "fail"
        else:
            metrics = pixel_delta(run_png[pixel_index], golden_png[pixel_index])
            pixel_metrics["metrics"] = metrics
            if not bool(metrics.get("available")):
                pixel_failures.append(str(metrics.get("reason", "pixel_delta_unavailable")))
                pixel_metrics["status"] = "fail"
            else:
                final_change_ratio = float(metrics.get("change_ratio", 0.0))
                final_mean_abs = float(metrics.get("mean_abs_diff", 0.0))
                max_change_ratio = float(pixel_cfg.get("max_final_change_ratio", 0.12))
                max_mean_abs = float(pixel_cfg.get("max_final_mean_abs_diff", 0.08))
                if final_change_ratio > max_change_ratio:
                    pixel_failures.append(
                        f"final_change_ratio={final_change_ratio} > max_final_change_ratio={max_change_ratio}"
                    )
                if final_mean_abs > max_mean_abs:
                    pixel_failures.append(
                        f"final_mean_abs_diff={final_mean_abs} > max_final_mean_abs_diff={max_mean_abs}"
                    )
                pixel_metrics["status"] = "pass" if not pixel_failures else "fail"

    layout_status = "pass" if not layout_failures else "fail"
    semantic_status = "pass" if not semantic_failures else "fail"
    provider_status = "pass" if not provider_failures else "fail"
    pixel_status = str(pixel_metrics.get("status", "skip"))
    overall_status = "pass"
    failing_statuses = {layout_status, semantic_status, provider_status}
    if pixel_enabled:
        failing_statuses.add(pixel_status)
    if "fail" in failing_statuses:
        overall_status = "warn" if fail_mode == "warn" else "fail"

    report = {
        "run_dir": str(run_dir),
        "golden_dir": str(golden_dir),
        "scenario": scenario_name,
        "provider": provider,
        "fail_mode": fail_mode,
        "overall_status": overall_status,
        "thresholds_effective": merged,
        "layout": {
            "status": layout_status,
            "failures": layout_failures,
            "frame_count_run": len(run_indices),
            "frame_count_golden": len(golden_indices),
            "frame_count_delta": len(run_indices) - len(golden_indices),
            "missing_in_run": run_missing,
            "missing_in_golden": golden_missing,
            "extra_in_run_outside_overlap": run_extra,
            "extra_in_golden_outside_overlap": golden_extra,
            "overlap_window": list(overlap) if overlap else None,
            "max_line_delta": max_line_delta,
            "mean_line_delta": round(mean_line_delta, 3),
            "final_similarity": round(final_similarity, 4),
            "head_similarity": round(head_similarity, 4),
            "layout_drift_score": layout_drift_score,
        },
        "semantic": {
            "status": semantic_status,
            "failures": semantic_failures,
            "run_scenario": run_scenario,
            "golden_scenario": golden_scenario,
            "semantic_miss_count": semantic_miss_count,
            "semantic_misses": semantic_misses,
            "run_semantic_failures_count": run_semantic_failures,
            "action_count_delta": action_count_delta,
            "golden_must_contain": list(golden_must_contain),
            "golden_must_not_contain": list(golden_must_not),
            "golden_must_match_regex": list(golden_regex),
        },
        "provider_checks": {
            "status": provider_status,
            "failures": provider_failures,
            "token_drifts": token_drifts,
            "allow_banner_drift": allow_banner_drift,
            "compaction_marker_missing": compaction_marker_missing,
        },
        "pixel": pixel_metrics,
        "excerpts": {
            "run_first_20": "\n".join(run_first.splitlines()[:20]),
            "golden_first_20": "\n".join(golden_first.splitlines()[:20]),
            "run_final_20": "\n".join(run_final.splitlines()[-20:]),
            "golden_final_20": "\n".join(golden_final.splitlines()[-20:]),
        },
    }
    strict_fail = bool(overall_status == "fail" and fail_mode == "strict")
    return CompareResult(report=report, strict_fail=strict_fail)


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# tmux run vs golden comparison")
    lines.append("")
    lines.append(f"- run: `{report['run_dir']}`")
    lines.append(f"- golden: `{report['golden_dir']}`")
    lines.append(f"- scenario: `{report['scenario']}`")
    lines.append(f"- provider: `{report['provider']}`")
    lines.append(f"- overall: `{report['overall_status']}`")
    lines.append("")
    lines.append("| category | status | key metrics |")
    lines.append("|---|---|---|")
    lines.append(
        "| layout | {status} | drift={drift}, frames(run/golden)={run_fc}/{gold_fc}, missing={missing} |".format(
            status=report["layout"]["status"],
            drift=report["layout"]["layout_drift_score"],
            run_fc=report["layout"]["frame_count_run"],
            gold_fc=report["layout"]["frame_count_golden"],
            missing=len(report["layout"]["missing_in_run"]) + len(report["layout"]["missing_in_golden"]),
        )
    )
    lines.append(
        "| semantic | {status} | misses={miss}, semantic_failures={sf}, action_delta={ad} |".format(
            status=report["semantic"]["status"],
            miss=report["semantic"]["semantic_miss_count"],
            sf=report["semantic"]["run_semantic_failures_count"],
            ad=report["semantic"]["action_count_delta"],
        )
    )
    lines.append(
        "| provider | {status} | token_drifts={td}, compaction_missing={cm} |".format(
            status=report["provider_checks"]["status"],
            td=len(report["provider_checks"]["token_drifts"]),
            cm=len(report["provider_checks"]["compaction_marker_missing"]),
        )
    )
    pixel = report.get("pixel", {})
    pixel_metrics = pixel.get("metrics", {}) if isinstance(pixel, dict) else {}
    lines.append(
        "| pixel | {status} | enabled={enabled}, index={idx}, change_ratio={cr}, mean_abs={mad} |".format(
            status=pixel.get("status", "skip"),
            enabled=pixel.get("enabled", False),
            idx=pixel.get("frame_index"),
            cr=pixel_metrics.get("change_ratio", "n/a"),
            mad=pixel_metrics.get("mean_abs_diff", "n/a"),
        )
    )
    lines.append("")
    for section in ("layout", "semantic", "provider_checks", "pixel"):
        failures = report[section]["failures"]
        lines.append(f"## {section} failures")
        if not failures:
            lines.append("- none")
        else:
            for item in failures:
                lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare tmux run output against a golden run.")
    parser.add_argument("--run-dir", required=True, help="run directory or parent containing scenario_manifest.json")
    parser.add_argument("--golden-dir", required=True, help="golden run directory or parent containing scenario_manifest.json")
    parser.add_argument(
        "--thresholds",
        default="config/tmux_golden_thresholds.yaml",
        help="threshold config file (JSON-compatible YAML)",
    )
    parser.add_argument("--provider", default="", help="provider hint: claude|codex")
    parser.add_argument("--scenario", default="", help="scenario id override for threshold lookup")
    parser.add_argument("--fail-mode", choices=["warn", "strict"], default="warn")
    parser.add_argument("--max-layout-drift", type=float, default=None)
    parser.add_argument("--max-semantic-misses", type=int, default=None)
    parser.add_argument("--enable-pixel-checks", action="store_true", help="enable PNG pixel comparison checks")
    parser.add_argument("--disable-pixel-checks", action="store_true", help="disable PNG pixel comparison checks")
    parser.add_argument("--max-pixel-change-ratio", type=float, default=None)
    parser.add_argument("--max-pixel-mean-abs-diff", type=float, default=None)
    parser.add_argument("--allow-provider-banner-drift", action="store_true")
    parser.add_argument("--disallow-provider-banner-drift", action="store_true")
    parser.add_argument("--redact", action="store_true", help="redact secret-like patterns in excerpts")
    parser.add_argument("--output-json", default="", help="path for machine report (default: <run_dir>/comparison_report.json)")
    parser.add_argument("--output-md", default="", help="path for markdown report (default: <run_dir>/comparison_report.md)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = discover_run_dir(Path(args.run_dir).expanduser().resolve())
    golden_dir = discover_run_dir(Path(args.golden_dir).expanduser().resolve())
    thresholds = load_thresholds(Path(args.thresholds).expanduser().resolve())
    allow_provider_banner_drift: bool | None = None
    if args.allow_provider_banner_drift and args.disallow_provider_banner_drift:
        raise SystemExit("choose only one of --allow-provider-banner-drift or --disallow-provider-banner-drift")
    if args.enable_pixel_checks and args.disable_pixel_checks:
        raise SystemExit("choose only one of --enable-pixel-checks or --disable-pixel-checks")
    if args.allow_provider_banner_drift:
        allow_provider_banner_drift = True
    if args.disallow_provider_banner_drift:
        allow_provider_banner_drift = False
    enable_pixel_checks: bool | None = None
    if args.enable_pixel_checks:
        enable_pixel_checks = True
    if args.disable_pixel_checks:
        enable_pixel_checks = False

    result = compare(
        run_dir=run_dir,
        golden_dir=golden_dir,
        provider_hint=args.provider,
        scenario_hint=args.scenario,
        thresholds_cfg=thresholds,
        fail_mode=args.fail_mode,
        max_layout_drift=args.max_layout_drift,
        max_semantic_misses=args.max_semantic_misses,
        allow_provider_banner_drift=allow_provider_banner_drift,
        enable_pixel_checks=enable_pixel_checks,
        max_pixel_change_ratio=args.max_pixel_change_ratio,
        max_pixel_mean_abs_diff=args.max_pixel_mean_abs_diff,
        redact=bool(args.redact),
    )
    if args.redact:
        result.report = redact_obj(result.report)

    json_path = Path(args.output_json).expanduser().resolve() if args.output_json else run_dir / "comparison_report.json"
    md_path = Path(args.output_md).expanduser().resolve() if args.output_md else run_dir / "comparison_report.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result.report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result.report), encoding="utf-8")
    print(str(json_path))
    print(str(md_path))
    if result.strict_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
