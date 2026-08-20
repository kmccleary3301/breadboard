#!/usr/bin/env python3
"""
Build deterministic launch media assets from an existing tmux capture run.

This script is intentionally file-only: it does not run the engine or execute
runtime tasks. It packages pre-recorded capture artifacts into docs/media.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True)


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"required file missing: {path}")


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Build launch media bundle from tmux capture run.")
    p.add_argument(
        "--run-dir",
        required=True,
        help="Path to tmux capture run dir (must contain frames/ and scenario_manifest.json).",
    )
    p.add_argument(
        "--repo-root",
        default=".",
        help="Path to breadboard_repo root (default: current directory).",
    )
    p.add_argument("--screenshot-frame", type=int, default=44, help="Frame index for still screenshot.")
    p.add_argument("--clip-start", type=int, default=36, help="First frame index for short clip.")
    p.add_argument("--clip-end", type=int, default=45, help="Last frame index for short clip.")
    p.add_argument("--fps", type=int, default=4, help="Framerate for rendered clip.")
    args = p.parse_args()

    run_dir = Path(args.run_dir).resolve()
    repo_root = Path(args.repo_root).resolve()

    frames_dir = run_dir / "frames"
    _require_file(run_dir / "scenario_manifest.json")
    if not frames_dir.is_dir():
        raise FileNotFoundError(f"frames dir missing: {frames_dir}")

    screenshot_src = frames_dir / f"frame_{args.screenshot_frame:04d}.png"
    _require_file(screenshot_src)

    if args.clip_end < args.clip_start:
        raise ValueError("--clip-end must be >= --clip-start")
    frame_count = args.clip_end - args.clip_start + 1

    proof_root = repo_root / "docs" / "media" / "proof"
    launch_root = proof_root / "launch_v1"
    bundles_root = proof_root / "bundles"
    launch_root.mkdir(parents=True, exist_ok=True)
    bundles_root.mkdir(parents=True, exist_ok=True)

    screenshot_out = launch_root / "launch_tui_screenshot_v1.png"
    clip_mp4 = launch_root / "launch_tui_clip_v1.mp4"
    clip_gif = launch_root / "launch_tui_clip_v1.gif"
    metadata_out = launch_root / "launch_tui_capture_source_v1.json"
    checksums_out = launch_root / "checksums.sha256"
    bundle_zip = bundles_root / "launch_proof_bundle_v1.zip"

    shutil.copy2(screenshot_src, screenshot_out)

    ffmpeg_input = str(frames_dir / "frame_%04d.png")
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(args.fps),
            "-start_number",
            str(args.clip_start),
            "-i",
            ffmpeg_input,
            "-vframes",
            str(frame_count),
            "-vf",
            "scale=iw:ih:flags=neighbor",
            "-pix_fmt",
            "yuv420p",
            str(clip_mp4),
        ]
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(args.fps),
            "-start_number",
            str(args.clip_start),
            "-i",
            ffmpeg_input,
            "-vframes",
            str(frame_count),
            "-vf",
            "scale=iw:ih:flags=neighbor",
            str(clip_gif),
        ]
    )

    copied = []
    for name in [
        "scenario_manifest.json",
        "run_summary.json",
        "comparison_report.md",
        "comparison_report.json",
        "actions.json",
        "meta.json",
        "initial.txt",
    ]:
        src = run_dir / name
        dst = launch_root / name
        if _copy_if_exists(src, dst):
            copied.append(dst.name)

    metadata = {
        "version": "v1",
        "source_run_dir": str(run_dir),
        "source_frames_dir": str(frames_dir),
        "screenshot_frame": args.screenshot_frame,
        "clip_start_frame": args.clip_start,
        "clip_end_frame": args.clip_end,
        "fps": args.fps,
        "outputs": {
            "screenshot_png": str(screenshot_out.relative_to(repo_root)),
            "clip_mp4": str(clip_mp4.relative_to(repo_root)),
            "clip_gif": str(clip_gif.relative_to(repo_root)),
            "bundle_zip": str(bundle_zip.relative_to(repo_root)),
        },
        "copied_metadata_files": copied,
    }
    metadata_out.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    checksum_targets = [
        screenshot_out,
        clip_mp4,
        clip_gif,
        metadata_out,
    ]
    for name in copied:
        checksum_targets.append(launch_root / name)

    with checksums_out.open("w", encoding="utf-8") as f:
        for path in sorted(checksum_targets):
            rel = path.relative_to(repo_root)
            f.write(f"{_sha256(path)}  {rel}\n")

    with zipfile.ZipFile(bundle_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(checksum_targets + [checksums_out]):
            zf.write(path, arcname=str(path.relative_to(repo_root)))

    print(f"[launch-media] wrote screenshot: {screenshot_out}")
    print(f"[launch-media] wrote clip mp4:  {clip_mp4}")
    print(f"[launch-media] wrote clip gif:  {clip_gif}")
    print(f"[launch-media] wrote bundle:    {bundle_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
