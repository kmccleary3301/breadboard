#!/usr/bin/env python3
"""
Validate integrity of a tmux capture run directory.

This is a fast, deterministic "bundle completeness" check used by:
- humans (preflight before comparing/blessing)
- CI (soft gate / harness self-check)

Exit codes:
- 0: pass
- 2: validation failed (or warnings treated as errors in --strict)
- 3: invalid input/runtime error
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:
                raise ValueError(f"invalid JSONL at {path}:{lineno} ({exc})") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL records must be objects at {path}:{lineno}")
            yield payload


def _discover_run_dir(root: Path) -> Path:
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"run-dir path not found: {root}")

    if root.is_file():
        # If given a file inside a run dir, treat its parent as run dir.
        root = root.parent

    if (root / "scenario_manifest.json").exists() or (root / "meta.json").exists():
        return root

    # Prefer scenario runs if present.
    scenario_manifests = sorted(root.rglob("scenario_manifest.json"))
    if scenario_manifests:
        return scenario_manifests[-1].parent

    metas = sorted(root.rglob("meta.json"))
    if metas:
        return metas[-1].parent

    raise FileNotFoundError(f"no scenario_manifest.json or meta.json found under {root}")


@dataclass
class ValidationResult:
    ok: bool
    status: str
    errors: list[str]
    warnings: list[str]
    run_dir: str
    frame_count: int
    missing_files_count: int
    render_lock_missing_count: int
    render_parity_violation_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
            "run_dir": self.run_dir,
            "frame_count": self.frame_count,
            "missing_files_count": self.missing_files_count,
            "render_lock_missing_count": self.render_lock_missing_count,
            "render_parity_violation_count": self.render_parity_violation_count,
        }


def validate_run_dir(
    run_dir: Path,
    *,
    strict: bool,
    expect_png: bool | None,
    max_missing_frames: int,
) -> ValidationResult:
    max_missing_text_rows = 0
    max_extra_render_rows = 0
    max_row_span_delta = 2

    errors: list[str] = []
    warnings: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    def warn(msg: str) -> None:
        warnings.append(msg)

    meta_path = run_dir / "meta.json"
    index_path = run_dir / "index.jsonl"
    frames_dir = run_dir / "frames"

    if not meta_path.exists():
        err(f"missing meta.json: {meta_path}")
        meta: dict[str, Any] = {}
    else:
        try:
            meta = _load_json(meta_path, "meta.json")
        except Exception as exc:
            err(str(exc))
            meta = {}

    if not index_path.exists():
        err(f"missing index.jsonl: {index_path}")
        index_records: list[dict[str, Any]] = []
    else:
        try:
            index_records = list(_iter_jsonl(index_path))
        except Exception as exc:
            err(str(exc))
            index_records = []

    if not frames_dir.exists():
        err(f"missing frames dir: {frames_dir}")

    # Initial snapshots are expected for easy QA.
    initial_txt = run_dir / "initial.txt"
    initial_ansi = run_dir / "initial.ansi"
    if not initial_txt.exists():
        (err if strict else warn)(f"missing initial.txt: {initial_txt}")
    if not initial_ansi.exists():
        (err if strict else warn)(f"missing initial.ansi: {initial_ansi}")

    # Scenario runs should have these.
    scenario_manifest = run_dir / "scenario_manifest.json"
    run_summary = run_dir / "run_summary.json"
    has_scenario = scenario_manifest.exists() or run_summary.exists()
    if has_scenario:
        if not scenario_manifest.exists():
            (err if strict else warn)(f"missing scenario_manifest.json: {scenario_manifest}")
        if not run_summary.exists():
            (err if strict else warn)(f"missing run_summary.json: {run_summary}")
        if scenario_manifest.exists() and run_summary.exists():
            try:
                manifest = _load_json(scenario_manifest, "scenario_manifest.json")
                summary = _load_json(run_summary, "run_summary.json")
                if manifest.get("scenario") and summary.get("scenario") and manifest.get("scenario") != summary.get("scenario"):
                    warn(f"scenario mismatch: manifest={manifest.get('scenario')!r} summary={summary.get('scenario')!r}")
                if manifest.get("run_id") and summary.get("run_id") and manifest.get("run_id") != summary.get("run_id"):
                    warn(f"run_id mismatch: manifest={manifest.get('run_id')!r} summary={summary.get('run_id')!r}")
            except Exception as exc:
                (err if strict else warn)(f"unable to parse scenario files: {exc}")

    # Determine PNG expectation.
    if expect_png is None:
        # Auto: rely on meta.render_png if present, otherwise infer from index records.
        render_png = meta.get("render_png")
        if isinstance(render_png, bool):
            expect_png = render_png
        else:
            expect_png = any(rec.get("png") for rec in index_records)

    missing_files = 0
    render_lock_missing = 0
    render_parity_violations = 0
    frames: list[int] = []
    last_frame = 0
    for rec in index_records:
        frame = rec.get("frame")
        if not isinstance(frame, int):
            (err if strict else warn)(f"index record missing int 'frame': {rec!r}")
            continue
        frames.append(frame)
        if frame <= last_frame:
            (err if strict else warn)(f"non-monotonic frame index: prev={last_frame} curr={frame}")
        last_frame = frame

        for key in ("text", "ansi"):
            rel = rec.get(key)
            if not isinstance(rel, str) or not rel:
                (err if strict else warn)(f"index record missing '{key}' path for frame {frame}: {rec!r}")
                continue
            path = run_dir / rel
            if not path.exists():
                missing_files += 1
                err(f"missing {key} file for frame {frame}: {path}")

        png_rel = rec.get("png")
        if expect_png:
            if not isinstance(png_rel, str) or not png_rel:
                missing_files += 1
                err(f"missing png path in index for frame {frame} (expected png)")
            else:
                png_path = run_dir / png_rel
                if not png_path.exists():
                    missing_files += 1
                    err(f"missing png file for frame {frame}: {png_path}")
                render_lock_rel = rec.get("render_lock")
                if not isinstance(render_lock_rel, str) or not render_lock_rel:
                    render_lock_rel = str(Path(png_rel).with_suffix(".render_lock.json"))
                render_lock_path = run_dir / render_lock_rel
                lock_row_occupancy: dict[str, Any] | None = None
                if not render_lock_path.exists():
                    render_lock_missing += 1
                    (err if strict else warn)(
                        f"missing render_lock sidecar for frame {frame}: {render_lock_path}"
                    )
                else:
                    try:
                        payload = _load_json(render_lock_path, f"render_lock[{frame}]")
                    except Exception as exc:
                        render_parity_violations += 1
                        (err if strict else warn)(str(exc))
                        payload = {}
                    schema_version = payload.get("schema_version")
                    if schema_version != "tmux_render_lock_frame_v1":
                        render_parity_violations += 1
                        (err if strict else warn)(
                            f"unexpected render_lock schema for frame {frame}: {schema_version!r}"
                        )
                    row_occupancy = payload.get("row_occupancy")
                    if not isinstance(row_occupancy, dict):
                        render_parity_violations += 1
                        (err if strict else warn)(
                            f"render_lock missing row_occupancy object for frame {frame}: {render_lock_path}"
                        )
                    else:
                        lock_row_occupancy = row_occupancy
                        if not isinstance(row_occupancy.get("text_sha256_normalized"), str) or not row_occupancy.get("text_sha256_normalized"):
                            render_parity_violations += 1
                            (err if strict else warn)(
                                f"render_lock missing text_sha256_normalized for frame {frame}: {render_lock_path}"
                            )
                        try:
                            missing_count = int(row_occupancy.get("missing_count", 0))
                            extra_count = int(row_occupancy.get("extra_count", 0))
                            row_span_delta = int(row_occupancy.get("row_span_delta", 0))
                        except Exception:
                            render_parity_violations += 1
                            (err if strict else warn)(
                                f"render_lock has non-integer parity fields for frame {frame}: {render_lock_path}"
                            )
                        else:
                            exceeds = (
                                missing_count > max_missing_text_rows
                                or extra_count > max_extra_render_rows
                                or row_span_delta > max_row_span_delta
                            )
                            if exceeds:
                                render_parity_violations += 1
                                (err if strict else warn)(
                                    "render parity out of bounds for frame "
                                    f"{frame}: missing={missing_count} extra={extra_count} "
                                    f"row_span_delta={row_span_delta} (limits missing<={max_missing_text_rows}, "
                                    f"extra<={max_extra_render_rows}, row_span_delta<={max_row_span_delta})"
                                )

                row_parity_rel = rec.get("render_parity_summary")
                if not isinstance(row_parity_rel, str) or not row_parity_rel:
                    row_parity_rel = str(Path(png_rel).with_suffix(".row_parity.json"))
                row_parity_path = run_dir / row_parity_rel
                if row_parity_path.exists():
                    try:
                        row_parity_payload = _load_json(row_parity_path, f"row_parity[{frame}]")
                    except Exception as exc:
                        render_parity_violations += 1
                        (err if strict else warn)(str(exc))
                        row_parity_payload = {}
                    row_schema = row_parity_payload.get("schema_version")
                    if row_schema != "tmux_row_parity_summary_v1":
                        render_parity_violations += 1
                        (err if strict else warn)(
                            f"unexpected row parity schema for frame {frame}: {row_schema!r}"
                        )
                    parity = row_parity_payload.get("parity")
                    if not isinstance(parity, dict):
                        render_parity_violations += 1
                        (err if strict else warn)(
                            f"row parity summary missing parity object for frame {frame}: {row_parity_path}"
                        )
                    else:
                        try:
                            missing_count = int(parity.get("missing_count", 0))
                            extra_count = int(parity.get("extra_count", 0))
                            row_span_delta = int(parity.get("row_span_delta", 0))
                        except Exception:
                            render_parity_violations += 1
                            (err if strict else warn)(
                                f"row parity summary has non-integer fields for frame {frame}: {row_parity_path}"
                            )
                        else:
                            exceeds = (
                                missing_count > max_missing_text_rows
                                or extra_count > max_extra_render_rows
                                or row_span_delta > max_row_span_delta
                            )
                            if exceeds:
                                render_parity_violations += 1
                                (err if strict else warn)(
                                    "row parity summary out of bounds for frame "
                                    f"{frame}: missing={missing_count} extra={extra_count} "
                                    f"row_span_delta={row_span_delta} (limits missing<={max_missing_text_rows}, "
                                    f"extra<={max_extra_render_rows}, row_span_delta<={max_row_span_delta})"
                                )
                            quick = rec.get("render_parity")
                            if isinstance(quick, dict):
                                q_missing = int(quick.get("missing_count", 0) or 0)
                                q_extra = int(quick.get("extra_count", 0) or 0)
                                q_span = int(quick.get("row_span_delta", 0) or 0)
                                if (
                                    q_missing != missing_count
                                    or q_extra != extra_count
                                    or q_span != row_span_delta
                                ):
                                    render_parity_violations += 1
                                    (err if strict else warn)(
                                        "index render_parity mismatch vs row parity summary for frame "
                                        f"{frame}: index=({q_missing},{q_extra},{q_span}) "
                                        f"summary=({missing_count},{extra_count},{row_span_delta})"
                                    )

                        if lock_row_occupancy is not None:
                            for key in ("missing_count", "extra_count", "row_span_delta", "text_sha256_normalized"):
                                if key in parity and lock_row_occupancy.get(key) != parity.get(key):
                                    render_parity_violations += 1
                                    (err if strict else warn)(
                                        f"render_lock vs row parity summary mismatch for frame {frame}: key={key}"
                                    )

    # Detect "holes" in the index frame range.
    if frames:
        present = set(frames)
        missing = [i for i in range(min(frames), max(frames) + 1) if i not in present]
        if missing and len(missing) > max_missing_frames:
            err(f"missing frame indices exceed max_missing_frames={max_missing_frames}: {missing[:30]}{'...' if len(missing) > 30 else ''}")
        elif missing:
            warn(f"missing frame indices within budget: {missing[:30]}{'...' if len(missing) > 30 else ''}")

    ok = len(errors) == 0 and (not strict or len(warnings) == 0)
    status = "pass" if ok else "fail"
    return ValidationResult(
        ok=ok,
        status=status,
        errors=errors,
        warnings=warnings,
        run_dir=str(run_dir),
        frame_count=len(frames),
        missing_files_count=missing_files,
        render_lock_missing_count=render_lock_missing,
        render_parity_violation_count=render_parity_violations,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate tmux capture run bundle integrity.")
    p.add_argument("--run-dir", required=True, help="run directory (or parent containing a run directory).")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors and use strict exit code.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--expect-png", action="store_true", help="require a PNG for every frame in index.jsonl.")
    group.add_argument("--allow-no-png", action="store_true", help="do not require PNGs even if meta says render_png.")
    p.add_argument("--max-missing-frames", type=int, default=0, help="allow up to N missing frame indices in index.jsonl range.")
    p.add_argument("--output-json", default="", help="write JSON report to this path (default: <run_dir>/validation_report.json)")
    p.add_argument("--output-md", default="", help="write markdown report to this path (default: <run_dir>/validation_report.md)")
    return p.parse_args()


def write_md(path: Path, result: ValidationResult) -> None:
    lines: list[str] = []
    lines.append("# tmux capture validation report")
    lines.append("")
    lines.append(f"- status: `{result.status}`")
    lines.append(f"- run_dir: `{result.run_dir}`")
    lines.append(f"- frame_count: `{result.frame_count}`")
    lines.append(f"- missing_files_count: `{result.missing_files_count}`")
    lines.append(f"- render_lock_missing_count: `{result.render_lock_missing_count}`")
    lines.append(f"- render_parity_violation_count: `{result.render_parity_violation_count}`")
    if result.errors:
        lines.append("")
        lines.append("## Errors")
        for e in result.errors:
            lines.append(f"- {e}")
    if result.warnings:
        lines.append("")
        lines.append("## Warnings")
        for w in result.warnings:
            lines.append(f"- {w}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        run_dir = _discover_run_dir(Path(args.run_dir))
        expect_png: bool | None
        if args.expect_png:
            expect_png = True
        elif args.allow_no_png:
            expect_png = False
        else:
            expect_png = None

        result = validate_run_dir(
            run_dir,
            strict=bool(args.strict),
            expect_png=expect_png,
            max_missing_frames=max(0, int(args.max_missing_frames)),
        )

        out_json = Path(args.output_json).expanduser().resolve() if args.output_json else (run_dir / "validation_report.json")
        out_md = Path(args.output_md).expanduser().resolve() if args.output_md else (run_dir / "validation_report.md")
        out_json.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
        write_md(out_md, result)

        if result.ok:
            print(f"[validate] pass: {run_dir}")
            return 0
        print(f"[validate] fail: {run_dir}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[validate] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
