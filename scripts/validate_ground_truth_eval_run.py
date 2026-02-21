#!/usr/bin/env python3
"""
Validate screenshot ground-truth evaluation run artifacts.

Guards:
1) comparison_metrics.json shape and required fields,
2) referenced files exist,
3) render artifacts remain under repeat_XX/renders/,
4) diagnostic artifacts remain under repeat_XX/diagnostics/ and never leak into
   review/render paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TARGETS = (
    "screenshot_ground_truth_1",
    "screenshot_ground_truth_2",
    "screenshot_ground_truth_3",
)
EVAL_SCHEMA_VERSION = "ground_truth_eval_run_v1"
RENDER_LOCK_SCHEMA_VERSION = "tmux_render_lock_frame_v1"
ROW_PARITY_SCHEMA_VERSION = "tmux_row_parity_summary_v1"


@dataclass
class ValidationResult:
    ok: bool
    run_dir: str
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "run_dir": self.run_dir,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _load_payload(run_dir: Path) -> dict[str, Any]:
    metrics_path = run_dir / "comparison_metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"comparison_metrics.json missing: {metrics_path}")
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"comparison_metrics.json must be JSON object: {metrics_path}")
    return payload


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _contains_diagnostic_token(path_value: str) -> bool:
    return "DIAGNOSTIC" in path_value or "diagnostic" in path_value.lower()


def _norm_rel(base: Path, path_value: str) -> str:
    try:
        rel = Path(path_value).resolve().relative_to(base.resolve())
        return rel.as_posix()
    except Exception:
        return ""


def _expect_under_prefix(errors: list[str], *, rel_path: str, target: str, repeat_idx: int, prefix: str, label: str) -> None:
    expected = f"repeat_{repeat_idx:02d}/{prefix}/"
    if not rel_path.startswith(expected):
        errors.append(
            f"{target} repeat_{repeat_idx:02d}: {label} must be under `{expected}` (got `{rel_path}`)"
        )


def validate_run(run_dir: Path) -> ValidationResult:
    run_dir = run_dir.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if not run_dir.exists():
        raise FileNotFoundError(f"run dir not found: {run_dir}")
    payload = _load_payload(run_dir)
    schema_version = str(payload.get("schema_version") or "")
    if schema_version != EVAL_SCHEMA_VERSION:
        errors.append(
            f"invalid schema_version `{schema_version}`; expected `{EVAL_SCHEMA_VERSION}`"
        )
    payload_run_dir = str(payload.get("run_dir") or "")
    if payload_run_dir != str(run_dir):
        errors.append(
            f"run_dir mismatch: payload `{payload_run_dir}` != actual `{run_dir}`"
        )
    deterministic_inputs = payload.get("deterministic_inputs")
    deterministic_input_hash = str(payload.get("deterministic_input_hash") or "")
    if not isinstance(deterministic_inputs, dict):
        errors.append("missing `deterministic_inputs` object in comparison_metrics.json")
    else:
        expected_hash = _canonical_json_hash(deterministic_inputs)
        if not deterministic_input_hash:
            errors.append("missing `deterministic_input_hash` in comparison_metrics.json")
        elif deterministic_input_hash != expected_hash:
            errors.append(
                "deterministic_input_hash mismatch against canonical deterministic_inputs"
            )

    repeats = int(payload.get("repeats", 0) or 0)
    if repeats <= 0:
        errors.append("invalid or missing `repeats` in comparison_metrics.json")

    targets = payload.get("targets")
    if not isinstance(targets, dict):
        errors.append("missing `targets` object in comparison_metrics.json")
        targets = {}

    for target in TARGETS:
        block = targets.get(target)
        if not isinstance(block, dict):
            errors.append(f"missing target block: {target}")
            continue
        repeat_rows = block.get("repeats")
        if not isinstance(repeat_rows, list):
            errors.append(f"{target}: repeats must be a list")
            continue
        if repeats > 0 and len(repeat_rows) != repeats:
            errors.append(
                f"{target}: repeat count mismatch (payload repeats={repeats}, target repeats={len(repeat_rows)})"
            )

        for item in repeat_rows:
            if not isinstance(item, dict):
                errors.append(f"{target}: repeat row must be an object")
                continue
            repeat_idx = int(item.get("repeat", 0) or 0)
            if repeat_idx <= 0:
                errors.append(f"{target}: invalid repeat index in row: {item!r}")
                continue

            png = str(item.get("png") or "")
            txt = str(item.get("txt") or "")
            ansi = str(item.get("ansi") or "")
            diag = str(item.get("diagnostic_heatmap_png") or "")
            render_lock_json = str(item.get("render_lock_json") or "")
            render_lock_sha256 = str(item.get("render_lock_sha256") or "")
            row_parity_summary_json = str(item.get("row_parity_summary_json") or "")
            row_parity_summary_sha256 = str(item.get("row_parity_summary_sha256") or "")
            if (
                not png
                or not txt
                or not ansi
                or not diag
                or not render_lock_json
                or not render_lock_sha256
                or not row_parity_summary_json
                or not row_parity_summary_sha256
            ):
                errors.append(f"{target} repeat_{repeat_idx:02d}: missing required artifact path(s)")
                continue

            for label, value in (
                ("png", png),
                ("txt", txt),
                ("ansi", ansi),
                ("render_lock_json", render_lock_json),
                ("row_parity_summary_json", row_parity_summary_json),
                ("diagnostic_heatmap_png", diag),
            ):
                path = Path(value).resolve()
                if not path.exists():
                    errors.append(f"{target} repeat_{repeat_idx:02d}: {label} path missing: {value}")

            png_rel = _norm_rel(run_dir, png)
            txt_rel = _norm_rel(run_dir, txt)
            ansi_rel = _norm_rel(run_dir, ansi)
            render_lock_rel = _norm_rel(run_dir, render_lock_json)
            row_parity_rel = _norm_rel(run_dir, row_parity_summary_json)
            diag_rel = _norm_rel(run_dir, diag)
            if not png_rel:
                errors.append(f"{target} repeat_{repeat_idx:02d}: png path escapes run dir: {png}")
            if not txt_rel:
                errors.append(f"{target} repeat_{repeat_idx:02d}: txt path escapes run dir: {txt}")
            if not ansi_rel:
                errors.append(f"{target} repeat_{repeat_idx:02d}: ansi path escapes run dir: {ansi}")
            if not render_lock_rel:
                errors.append(
                    f"{target} repeat_{repeat_idx:02d}: render_lock_json path escapes run dir: {render_lock_json}"
                )
            if not row_parity_rel:
                errors.append(
                    f"{target} repeat_{repeat_idx:02d}: row_parity_summary_json path escapes run dir: {row_parity_summary_json}"
                )
            if not diag_rel:
                errors.append(f"{target} repeat_{repeat_idx:02d}: diagnostic path escapes run dir: {diag}")

            if png_rel:
                _expect_under_prefix(
                    errors,
                    rel_path=png_rel,
                    target=target,
                    repeat_idx=repeat_idx,
                    prefix="renders",
                    label="png",
                )
            if txt_rel:
                _expect_under_prefix(
                    errors,
                    rel_path=txt_rel,
                    target=target,
                    repeat_idx=repeat_idx,
                    prefix="renders",
                    label="txt",
                )
            if ansi_rel:
                _expect_under_prefix(
                    errors,
                    rel_path=ansi_rel,
                    target=target,
                    repeat_idx=repeat_idx,
                    prefix="renders",
                    label="ansi",
                )
            if render_lock_rel:
                _expect_under_prefix(
                    errors,
                    rel_path=render_lock_rel,
                    target=target,
                    repeat_idx=repeat_idx,
                    prefix="renders",
                    label="render_lock_json",
                )
            if row_parity_rel:
                _expect_under_prefix(
                    errors,
                    rel_path=row_parity_rel,
                    target=target,
                    repeat_idx=repeat_idx,
                    prefix="renders",
                    label="row_parity_summary_json",
                )
            if diag_rel:
                _expect_under_prefix(
                    errors,
                    rel_path=diag_rel,
                    target=target,
                    repeat_idx=repeat_idx,
                    prefix="diagnostics",
                    label="diagnostic_heatmap_png",
                )
                if "DIAGNOSTIC" not in Path(diag_rel).name:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: diagnostic heatmap filename must include `DIAGNOSTIC` "
                        f"(got `{diag_rel}`)"
                    )

            # Never allow diagnostics naming on render-review artifacts.
            for label, value in (("png", png_rel), ("txt", txt_rel), ("ansi", ansi_rel)):
                if value and _contains_diagnostic_token(value):
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: diagnostic artifact leaked into {label} path: `{value}`"
                    )
            if render_lock_rel and _contains_diagnostic_token(render_lock_rel):
                errors.append(
                    f"{target} repeat_{repeat_idx:02d}: diagnostic artifact leaked into render_lock_json path: `{render_lock_rel}`"
                )
            if row_parity_rel and _contains_diagnostic_token(row_parity_rel):
                errors.append(
                    f"{target} repeat_{repeat_idx:02d}: diagnostic artifact leaked into row_parity_summary_json path: `{row_parity_rel}`"
                )

            metrics = item.get("metrics")
            if not isinstance(metrics, dict):
                errors.append(f"{target} repeat_{repeat_idx:02d}: missing metrics object")
                continue
            heatmap_from_metrics = str(metrics.get("heatmap_path") or "")
            if not heatmap_from_metrics:
                errors.append(f"{target} repeat_{repeat_idx:02d}: metrics.heatmap_path missing")
            elif Path(heatmap_from_metrics).resolve() != Path(diag).resolve():
                errors.append(
                    f"{target} repeat_{repeat_idx:02d}: metrics.heatmap_path does not match diagnostic_heatmap_png"
                )
            row_occ = metrics.get("row_occupancy")
            if not isinstance(row_occ, dict):
                errors.append(f"{target} repeat_{repeat_idx:02d}: metrics.row_occupancy missing")
            else:
                for req in ("missing_count", "extra_count", "row_span_delta", "text_sha256_normalized"):
                    if req not in row_occ:
                        errors.append(f"{target} repeat_{repeat_idx:02d}: metrics.row_occupancy.{req} missing")
                if not isinstance(row_occ.get("text_sha256_normalized"), str) or not row_occ.get("text_sha256_normalized"):
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: metrics.row_occupancy.text_sha256_normalized invalid"
                    )

            lock_path = Path(render_lock_json).resolve()
            row_parity_path = Path(row_parity_summary_json).resolve()
            if lock_path.exists():
                actual_lock_sha = _sha256_file(lock_path)
                if render_lock_sha256 != actual_lock_sha:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: render_lock_sha256 mismatch for `{lock_path}`"
                    )
                try:
                    lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: render_lock_json unreadable: {exc}"
                    )
                    lock_payload = {}
                if not isinstance(lock_payload, dict):
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: render_lock_json must be object"
                    )
                    lock_payload = {}
                lock_schema = str(lock_payload.get("schema_version") or "")
                if lock_schema != RENDER_LOCK_SCHEMA_VERSION:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: unexpected render_lock schema `{lock_schema}`"
                    )
                lock_row_occ = lock_payload.get("row_occupancy")
                if not isinstance(lock_row_occ, dict):
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: render_lock.row_occupancy missing"
                    )
                else:
                    for req in ("missing_count", "extra_count", "row_span_delta", "text_sha256_normalized"):
                        if req not in lock_row_occ:
                            errors.append(
                                f"{target} repeat_{repeat_idx:02d}: render_lock.row_occupancy.{req} missing"
                            )
                    if isinstance(row_occ, dict):
                        for req in ("missing_count", "extra_count", "row_span_delta", "text_sha256_normalized"):
                            if req in lock_row_occ and req in row_occ and lock_row_occ.get(req) != row_occ.get(req):
                                errors.append(
                                    f"{target} repeat_{repeat_idx:02d}: row_occupancy parity mismatch for `{req}` between metrics and render_lock"
                                )

            if row_parity_path.exists():
                actual_row_parity_sha = _sha256_file(row_parity_path)
                if row_parity_summary_sha256 != actual_row_parity_sha:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: row_parity_summary_sha256 mismatch for `{row_parity_path}`"
                    )
                try:
                    row_parity_payload = json.loads(row_parity_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: row_parity_summary_json unreadable: {exc}"
                    )
                    row_parity_payload = {}
                if not isinstance(row_parity_payload, dict):
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: row_parity_summary_json must be object"
                    )
                    row_parity_payload = {}
                row_schema = str(row_parity_payload.get("schema_version") or "")
                if row_schema != ROW_PARITY_SCHEMA_VERSION:
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: unexpected row parity schema `{row_schema}`"
                    )
                parity_block = row_parity_payload.get("parity")
                if not isinstance(parity_block, dict):
                    errors.append(
                        f"{target} repeat_{repeat_idx:02d}: row_parity_summary_json.parity missing"
                    )
                    parity_block = {}
                if isinstance(row_occ, dict):
                    for req in ("missing_count", "extra_count", "row_span_delta", "text_sha256_normalized"):
                        if req in parity_block and req in row_occ and parity_block.get(req) != row_occ.get(req):
                            errors.append(
                                f"{target} repeat_{repeat_idx:02d}: row parity mismatch for `{req}` between metrics and row_parity_summary"
                            )

    # Sweep filesystem for leakage.
    for leaked in run_dir.rglob("*"):
        if not leaked.is_file():
            continue
        rel = leaked.relative_to(run_dir).as_posix()
        if "/renders/" in rel and _contains_diagnostic_token(rel):
            errors.append(f"diagnostic file leaked under renders/: `{rel}`")
        if "/diagnostics/" in rel and leaked.suffix.lower() == ".png" and "DIAGNOSTIC" not in leaked.name:
            warnings.append(f"non-DIAGNOSTIC png found under diagnostics/: `{rel}`")

    return ValidationResult(
        ok=(len(errors) == 0),
        run_dir=str(run_dir),
        errors=errors,
        warnings=warnings,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out-json", default="")
    args = parser.parse_args()

    result = validate_run(Path(args.run_dir))
    payload = result.to_dict()
    text = json.dumps(payload, indent=2)
    if args.out_json:
        out = Path(args.out_json).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not result.ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
