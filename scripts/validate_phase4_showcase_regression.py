#!/usr/bin/env python3
"""
Deterministic regression checks for phase4 everything_showcase fullpane runs.

Checks:
- scenario_manifest reports the expected scenario id
- capture_mode is fullpane
- render_profile is phase4_locked_v1
- frame count meets a minimum floor
- final captured text includes required anchors
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SCENARIO_ID = "phase4_replay/everything_showcase_v1_fullpane_v1"
DEFAULT_MIN_FRAMES = 12
DEFAULT_REQUIRED_ANCHORS: tuple[str, ...] = (
    "BreadBoard v0.2.0",
    "Write(hello.c)",
    "Patch(hello.c)",
    "Markdown Showcase",
    "Inline link BreadBoard",
    "ls -la",
    "Cooked for",
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    run_dir: str
    scenario: str
    frame_count: int
    final_text_path: str
    missing_anchors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "run_dir": self.run_dir,
            "scenario": self.scenario,
            "frame_count": self.frame_count,
            "final_text_path": self.final_text_path,
            "missing_anchors": self.missing_anchors,
        }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _discover_run_dir(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"run-dir path not found: {root}")
    if root.is_file():
        root = root.parent
    if (root / "scenario_manifest.json").exists():
        return root
    manifests = sorted(root.rglob("scenario_manifest.json"))
    if manifests:
        return manifests[-1].parent
    raise FileNotFoundError(f"scenario_manifest.json not found under {root}")


def _last_frame_text_path(run_dir: Path) -> tuple[Path, int]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"index.jsonl missing: {index_path}")

    last_record: dict[str, Any] | None = None
    frame_count = 0
    with index_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            frame_count += 1
            last_record = payload

    if frame_count == 0 or last_record is None:
        raise ValueError(f"index.jsonl has no frame records: {index_path}")

    rel = str(last_record.get("text") or "").strip()
    if not rel:
        raise ValueError("last frame record missing text path")
    text_path = (run_dir / rel).resolve()
    if not text_path.exists():
        raise FileNotFoundError(f"final text frame missing: {text_path}")
    return text_path, frame_count


def validate_showcase_run(
    run_dir: Path,
    *,
    expected_scenario: str = DEFAULT_SCENARIO_ID,
    min_frames: int = DEFAULT_MIN_FRAMES,
    required_anchors: tuple[str, ...] = DEFAULT_REQUIRED_ANCHORS,
) -> ValidationResult:
    errors: list[str] = []
    manifest = _load_json(run_dir / "scenario_manifest.json", "scenario_manifest.json")
    scenario = str(manifest.get("scenario") or "")
    capture_mode = str(manifest.get("capture_mode") or "")
    render_profile = str(manifest.get("render_profile") or "")

    if scenario != expected_scenario:
        errors.append(
            f"scenario mismatch: expected {expected_scenario!r}, got {scenario!r}"
        )
    if capture_mode != "fullpane":
        errors.append(f"capture_mode mismatch: expected 'fullpane', got {capture_mode!r}")
    if render_profile != "phase4_locked_v1":
        errors.append(
            f"render_profile mismatch: expected 'phase4_locked_v1', got {render_profile!r}"
        )

    final_text_path, frame_count = _last_frame_text_path(run_dir)
    if frame_count < int(min_frames):
        errors.append(f"frame_count below minimum: frame_count={frame_count} min_frames={min_frames}")

    text = final_text_path.read_text(encoding="utf-8", errors="replace")
    missing_anchors = [anchor for anchor in required_anchors if anchor not in text]
    if missing_anchors:
        errors.append(f"missing required anchors: {missing_anchors}")

    return ValidationResult(
        ok=(len(errors) == 0),
        errors=errors,
        run_dir=str(run_dir),
        scenario=scenario,
        frame_count=frame_count,
        final_text_path=str(final_text_path),
        missing_anchors=missing_anchors,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate phase4 everything_showcase regression anchors.")
    p.add_argument("--run-dir", required=True, help="run dir or parent directory containing scenario runs")
    p.add_argument("--expected-scenario", default=DEFAULT_SCENARIO_ID, help="expected scenario id")
    p.add_argument("--min-frames", type=int, default=DEFAULT_MIN_FRAMES, help="minimum allowed frame count")
    p.add_argument(
        "--require-anchor",
        action="append",
        default=[],
        help="required final-text anchor (repeatable); defaults are used when omitted",
    )
    p.add_argument("--output-json", default="", help="optional output report path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_dir = _discover_run_dir(Path(args.run_dir))
        anchors = tuple(a for a in (str(x).strip() for x in args.require_anchor) if a)
        result = validate_showcase_run(
            run_dir,
            expected_scenario=str(args.expected_scenario),
            min_frames=max(1, int(args.min_frames)),
            required_anchors=anchors or DEFAULT_REQUIRED_ANCHORS,
        )
        out_json = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else run_dir / "showcase_regression_report.json"
        )
        out_json.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
        if result.ok:
            print(f"[showcase-regression] pass: {run_dir}")
            return 0
        print(f"[showcase-regression] fail: {run_dir}")
        for err in result.errors:
            print(f"- {err}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[showcase-regression] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
