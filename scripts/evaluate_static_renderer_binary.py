#!/usr/bin/env python3
"""
Evaluate onefile static renderer viability with embedded font assets.

Outputs:
- <run_dir>/static_renderer_evaluation.json
- <run_dir>/static_renderer_evaluation.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from tmux_capture_render_profile import DEFAULT_RENDER_PROFILE_ID


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
DEFAULT_OUT_ROOT = WORKSPACE_ROOT / "docs_tmp" / "cli_phase_5" / "static_renderer_eval"
DEFAULT_CAPTURE_GLOB = "gt_live_*/repeat_*/renders/screenshot_ground_truth_1.ansi"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _run(cmd: list[str], *, cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return int(proc.returncode), proc.stdout, proc.stderr


def _find_default_ansi() -> Path:
    eval_root = WORKSPACE_ROOT / "docs_tmp" / "cli_phase_5" / "ground_truth_eval"
    candidates = sorted(
        eval_root.glob(DEFAULT_CAPTURE_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no ANSI captures found under {eval_root}")
    return candidates[0].resolve()


def _compute_image_delta(a_path: Path, b_path: Path) -> dict[str, Any]:
    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    overlap_w = min(a.width, b.width)
    overlap_h = min(a.height, b.height)
    diff = ImageChops.difference(a.crop((0, 0, overlap_w, overlap_h)), b.crop((0, 0, overlap_w, overlap_h)))
    stat = ImageStat.Stat(diff)
    mae_channels = [float(v) for v in stat.mean]
    rms_channels = [float(v) for v in stat.rms]
    mae_rgb = sum(mae_channels) / 3.0
    rmse_rgb = math.sqrt(sum((v * v for v in rms_channels)) / 3.0)
    return {
        "size_a": [a.width, a.height],
        "size_b": [b.width, b.height],
        "overlap_size": [overlap_w, overlap_h],
        "mae_rgb": round(mae_rgb, 6),
        "rmse_rgb": round(rmse_rgb, 6),
        "mae_channels": [round(v, 6) for v in mae_channels],
        "rms_channels": [round(v, 6) for v in rms_channels],
        "pixel_equal": diff.getbbox() is None and a.size == b.size,
    }


def _sidecar_summary(png_path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for suffix in (".txt", ".render_lock.json", ".row_parity.json"):
        sidecar = png_path.with_suffix(suffix)
        out[suffix] = {
            "path": str(sidecar),
            "exists": sidecar.exists(),
            "sha256": _sha256_file(sidecar) if sidecar.exists() else "",
        }
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate static renderer binary with embedded assets.")
    parser.add_argument("--ansi", default="", help="ANSI capture path; defaults to latest ground-truth capture.")
    parser.add_argument("--cols", type=int, default=153)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--render-profile", default=DEFAULT_RENDER_PROFILE_ID)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--binary-name", default="tmux_capture_to_png_static_eval")
    parser.add_argument("--pyinstaller-bin", default="pyinstaller")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = args.run_id.strip() or time.strftime("%Y%m%d-%H%M%S")
    out_root = Path(args.out_root).expanduser().resolve()
    run_dir = out_root / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    ansi_path = Path(args.ansi).expanduser().resolve() if args.ansi else _find_default_ansi()
    if not ansi_path.exists():
        raise FileNotFoundError(f"ANSI capture not found: {ansi_path}")

    pyinstaller_version_cmd = [args.pyinstaller_bin, "--version"]
    pyinstaller_version_rc, pyinstaller_version_out, pyinstaller_version_err = _run(
        pyinstaller_version_cmd,
        cwd=REPO_ROOT,
    )
    if pyinstaller_version_rc != 0:
        raise SystemExit(f"pyinstaller version check failed: {pyinstaller_version_err.strip()}")

    dist_dir = run_dir / "dist"
    build_dir = run_dir / "build"
    spec_dir = run_dir / "spec"
    for d in (dist_dir, build_dir, spec_dir):
        d.mkdir(parents=True, exist_ok=True)

    add_data = f"{(REPO_ROOT / 'renderer_assets' / 'fonts').resolve()}:renderer_assets/fonts"
    build_cmd = [
        args.pyinstaller_bin,
        "--onefile",
        "--clean",
        "--name",
        args.binary_name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str((REPO_ROOT / "scripts").resolve()),
        "--add-data",
        add_data,
        str((REPO_ROOT / "scripts" / "tmux_capture_to_png.py").resolve()),
    ]
    build_rc, build_out, build_err = _run(build_cmd, cwd=REPO_ROOT)
    if build_rc != 0:
        raise SystemExit(f"pyinstaller build failed (rc={build_rc})\n{build_err.strip()}")

    binary_path = (dist_dir / args.binary_name).resolve()
    if not binary_path.exists():
        raise FileNotFoundError(f"expected static binary missing: {binary_path}")

    self_check_cmd = [str(binary_path), "--render-profile", args.render_profile, "--self-check"]
    self_check_rc, self_check_out, self_check_err = _run(self_check_cmd, cwd=REPO_ROOT)

    python_out_png = run_dir / "python_render.png"
    static_out_png = run_dir / "static_render.png"
    python_cmd = [
        "python3",
        str((REPO_ROOT / "scripts" / "tmux_capture_to_png.py").resolve()),
        "--ansi",
        str(ansi_path),
        "--cols",
        str(args.cols),
        "--rows",
        str(args.rows),
        "--render-profile",
        args.render_profile,
        "--out",
        str(python_out_png),
    ]
    static_cmd = [
        str(binary_path),
        "--ansi",
        str(ansi_path),
        "--cols",
        str(args.cols),
        "--rows",
        str(args.rows),
        "--render-profile",
        args.render_profile,
        "--out",
        str(static_out_png),
    ]
    py_rc, py_out, py_err = _run(python_cmd, cwd=REPO_ROOT)
    st_rc, st_out, st_err = _run(static_cmd, cwd=REPO_ROOT)
    if py_rc != 0:
        raise SystemExit(f"python renderer failed (rc={py_rc})\n{py_err.strip()}")
    if st_rc != 0:
        raise SystemExit(f"static renderer failed (rc={st_rc})\n{st_err.strip()}")

    image_delta = _compute_image_delta(python_out_png, static_out_png)
    payload = {
        "schema_version": "static_renderer_eval_v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "ansi_path": str(ansi_path),
        "cols": int(args.cols),
        "rows": int(args.rows),
        "render_profile": str(args.render_profile),
        "pyinstaller_version": pyinstaller_version_out.strip(),
        "build": {
            "command": build_cmd,
            "returncode": build_rc,
            "stdout_log": str(run_dir / "pyinstaller.stdout.log"),
            "stderr_log": str(run_dir / "pyinstaller.stderr.log"),
            "binary_path": str(binary_path),
            "binary_sha256": _sha256_file(binary_path),
        },
        "self_check": {
            "command": self_check_cmd,
            "returncode": self_check_rc,
            "ok_marker_present": "render-lock:self-check:ok" in self_check_out,
            "stdout_log": str(run_dir / "self_check.stdout.log"),
            "stderr_log": str(run_dir / "self_check.stderr.log"),
        },
        "python_render": {
            "command": python_cmd,
            "returncode": py_rc,
            "png": str(python_out_png),
            "png_sha256": _sha256_file(python_out_png),
            "sidecars": _sidecar_summary(python_out_png),
            "stdout_log": str(run_dir / "python_render.stdout.log"),
            "stderr_log": str(run_dir / "python_render.stderr.log"),
        },
        "static_render": {
            "command": static_cmd,
            "returncode": st_rc,
            "png": str(static_out_png),
            "png_sha256": _sha256_file(static_out_png),
            "sidecars": _sidecar_summary(static_out_png),
            "stdout_log": str(run_dir / "static_render.stdout.log"),
            "stderr_log": str(run_dir / "static_render.stderr.log"),
        },
        "comparison": image_delta,
        "overall_pass": bool(
            self_check_rc == 0
            and "render-lock:self-check:ok" in self_check_out
            and image_delta["pixel_equal"]
        ),
    }

    (run_dir / "pyinstaller.stdout.log").write_text(build_out, encoding="utf-8")
    (run_dir / "pyinstaller.stderr.log").write_text(build_err, encoding="utf-8")
    (run_dir / "self_check.stdout.log").write_text(self_check_out, encoding="utf-8")
    (run_dir / "self_check.stderr.log").write_text(self_check_err, encoding="utf-8")
    (run_dir / "python_render.stdout.log").write_text(py_out, encoding="utf-8")
    (run_dir / "python_render.stderr.log").write_text(py_err, encoding="utf-8")
    (run_dir / "static_render.stdout.log").write_text(st_out, encoding="utf-8")
    (run_dir / "static_render.stderr.log").write_text(st_err, encoding="utf-8")

    json_path = run_dir / "static_renderer_evaluation.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_lines = [
        "# Static Renderer Binary Evaluation",
        "",
        f"- run id: `{run_id}`",
        f"- ANSI capture: `{ansi_path}`",
        f"- render profile: `{args.render_profile}`",
        f"- pyinstaller: `{payload['pyinstaller_version']}`",
        f"- binary: `{binary_path}`",
        f"- binary sha256: `{payload['build']['binary_sha256']}`",
        f"- self-check ok marker: `{payload['self_check']['ok_marker_present']}`",
        f"- python png sha256: `{payload['python_render']['png_sha256']}`",
        f"- static png sha256: `{payload['static_render']['png_sha256']}`",
        f"- pixel equal: `{payload['comparison']['pixel_equal']}`",
        f"- MAE RGB: `{payload['comparison']['mae_rgb']}`",
        f"- RMSE RGB: `{payload['comparison']['rmse_rgb']}`",
        f"- overall pass: `{payload['overall_pass']}`",
        "",
        "## Artifacts",
        f"- `{json_path}`",
        f"- `{run_dir / 'pyinstaller.stdout.log'}`",
        f"- `{run_dir / 'pyinstaller.stderr.log'}`",
        f"- `{run_dir / 'self_check.stdout.log'}`",
        f"- `{run_dir / 'python_render.stdout.log'}`",
        f"- `{run_dir / 'static_render.stdout.log'}`",
        "",
    ]
    (run_dir / "static_renderer_evaluation.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(str(json_path))


if __name__ == "__main__":
    main()
