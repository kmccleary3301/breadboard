#!/usr/bin/env python3
"""
Deterministic schema checks for phase4 fullpane visual review packs.

Checks:
- pack has required top-level files (manifest.json, INDEX.md, reference screenshot copy)
- manifest has all required lane keys with fixed scenario ids
- required lane artifact paths exist on disk
- lane metrics files contain required fields
- INDEX includes required lock/version lines and per-lane headings
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LANE_SCENARIOS: dict[str, str] = {
    "streaming": "phase4_replay/streaming_v1_fullpane_v8",
    "todo": "phase4_replay/todo_preview_v1_fullpane_v7",
    "subagents": "phase4_replay/subagents_v1_fullpane_v7",
    "everything": "phase4_replay/everything_showcase_v1_fullpane_v1",
}

DEFAULT_INDEX_ANCHORS: tuple[str, ...] = (
    "# Visual Review Pack (Phase4 Fullpane Lock V1)",
    "Locked render profile: `phase4_locked_v1`",
    "## Scenario Summary",
    "### streaming",
    "### todo",
    "### subagents",
    "### everything",
    "## Files Per Scenario Folder",
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    pack_dir: str
    lanes_seen: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "pack_dir": self.pack_dir,
            "lanes_seen": self.lanes_seen,
        }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _must_exist(path: Path, errors: list[str], label: str) -> None:
    if not path.exists():
        errors.append(f"missing {label}: {path}")


def _check_rel_file(pack_dir: Path, rel: str, errors: list[str], label: str) -> None:
    rel_clean = str(rel or "").strip()
    if not rel_clean:
        errors.append(f"missing relative path for {label}")
        return
    path = (pack_dir / rel_clean).resolve()
    _must_exist(path, errors, label)


def validate_pack(
    pack_dir: Path,
    *,
    expected_lanes: dict[str, str] = DEFAULT_LANE_SCENARIOS,
    required_index_anchors: tuple[str, ...] = DEFAULT_INDEX_ANCHORS,
) -> ValidationResult:
    errors: list[str] = []

    manifest_path = pack_dir / "manifest.json"
    index_path = pack_dir / "INDEX.md"
    ref_path = pack_dir / "everything_showcase_ref_v2.png"

    _must_exist(manifest_path, errors, "manifest.json")
    _must_exist(index_path, errors, "INDEX.md")
    _must_exist(ref_path, errors, "everything_showcase_ref_v2.png")

    if errors:
        return ValidationResult(False, errors, str(pack_dir), [])

    manifest = _load_json(manifest_path, "manifest.json")

    lanes_seen = sorted(manifest.keys())
    expected_keys = sorted(expected_lanes.keys())
    if lanes_seen != expected_keys:
        errors.append(f"manifest lane keys mismatch: expected {expected_keys}, got {lanes_seen}")

    for lane, expected_scenario in expected_lanes.items():
        lane_payload = manifest.get(lane)
        if not isinstance(lane_payload, dict):
            errors.append(f"manifest lane missing or not object: {lane}")
            continue

        scenario = str(lane_payload.get("scenario") or "")
        if scenario != expected_scenario:
            errors.append(
                f"lane scenario mismatch for {lane}: expected {expected_scenario!r}, got {scenario!r}"
            )

        selected = lane_payload.get("selected")
        if not isinstance(selected, dict):
            errors.append(f"lane selected payload missing/not object: {lane}")
            continue

        for triplet_name in ("landing", "active", "final"):
            triplet = selected.get(triplet_name)
            if not isinstance(triplet, dict):
                errors.append(f"lane {lane} selected.{triplet_name} missing/not object")
                continue
            for ext in ("png", "txt", "ansi"):
                _check_rel_file(pack_dir, str(triplet.get(ext) or ""), errors, f"{lane}.{triplet_name}.{ext}")

        prev_triplet = selected.get("prev_final")
        if not isinstance(prev_triplet, dict):
            errors.append(f"lane {lane} selected.prev_final missing/not object")
        else:
            _check_rel_file(pack_dir, str(prev_triplet.get("png") or ""), errors, f"{lane}.prev_final.png")

        for file_key in (
            "final_fit_to_prev_png",
            "compare_side_by_side_png",
            "compare_heat_x3_png",
            "compare_metrics_json",
        ):
            _check_rel_file(pack_dir, str(selected.get(file_key) or ""), errors, f"{lane}.{file_key}")

        metrics_rel = str(selected.get("compare_metrics_json") or "")
        if metrics_rel:
            metrics_path = (pack_dir / metrics_rel).resolve()
            if metrics_path.exists():
                try:
                    metrics = _load_json(metrics_path, f"{lane} metrics")
                except Exception as exc:
                    errors.append(str(exc))
                    metrics = {}
                if isinstance(metrics, dict):
                    for mkey in (
                        "mean_abs_diff_rgb",
                        "rms_diff_rgb",
                        "prev_size",
                        "new_size",
                        "new_fit_size",
                        "scenario",
                        "selected_frame_indices",
                    ):
                        if mkey not in metrics:
                            errors.append(f"missing metrics key {mkey!r} in {metrics_path}")

    index_text = index_path.read_text(encoding="utf-8", errors="replace")
    for anchor in required_index_anchors:
        if anchor not in index_text:
            errors.append(f"INDEX.md missing required anchor: {anchor!r}")

    return ValidationResult(
        ok=(len(errors) == 0),
        errors=errors,
        pack_dir=str(pack_dir),
        lanes_seen=lanes_seen,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate phase4 fullpane visual review pack schema.")
    p.add_argument("--pack-dir", required=True, help="pack directory containing manifest.json and INDEX.md")
    p.add_argument("--output-json", default="", help="optional output report path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        pack_dir = Path(args.pack_dir).expanduser().resolve()
        if not pack_dir.exists() or not pack_dir.is_dir():
            raise FileNotFoundError(f"pack dir not found: {pack_dir}")

        result = validate_pack(pack_dir)
        out_json = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else pack_dir / "visual_pack_schema_report.json"
        )
        out_json.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")

        if result.ok:
            print(f"[phase4-visual-pack] pass: {pack_dir}")
            return 0

        print(f"[phase4-visual-pack] fail: {pack_dir}")
        for err in result.errors:
            print(f"- {err}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[phase4-visual-pack] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
