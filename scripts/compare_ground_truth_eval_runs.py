#!/usr/bin/env python3
"""Compare two ground-truth evaluation runs for deterministic parity."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_run_payload(run_dir: Path) -> dict[str, Any]:
    metrics_path = run_dir / "comparison_metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"missing comparison_metrics.json: {metrics_path}")
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON payload: {metrics_path}")
    targets = payload.get("targets")
    if not isinstance(targets, dict):
        raise ValueError(f"missing targets object: {metrics_path}")
    return payload


def _to_repeat_map(target_block: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows = target_block.get("repeats")
    if not isinstance(rows, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        repeat_idx = int(row.get("repeat", 0) or 0)
        if repeat_idx <= 0:
            continue
        out[repeat_idx] = row
    return out


def _file_hash(path_value: str) -> str:
    if not path_value:
        return ""
    path = Path(path_value).resolve()
    if not path.exists():
        return ""
    return _sha256_file(path)


def compare_runs(run_a: Path, run_b: Path) -> dict[str, Any]:
    payload_a = _load_run_payload(run_a)
    payload_b = _load_run_payload(run_b)

    targets_a = payload_a.get("targets", {})
    targets_b = payload_b.get("targets", {})
    target_names = sorted(set(targets_a.keys()) | set(targets_b.keys()))

    result: dict[str, Any] = {
        "run_a": str(run_a),
        "run_b": str(run_b),
        "schema_version_a": payload_a.get("schema_version"),
        "schema_version_b": payload_b.get("schema_version"),
        "schema_match": payload_a.get("schema_version") == payload_b.get("schema_version"),
        "render_profile_a": payload_a.get("render_profile"),
        "render_profile_b": payload_b.get("render_profile"),
        "render_profile_match": payload_a.get("render_profile") == payload_b.get("render_profile"),
        "deterministic_input_hash_a": payload_a.get("deterministic_input_hash"),
        "deterministic_input_hash_b": payload_b.get("deterministic_input_hash"),
        "deterministic_input_hash_equal": payload_a.get("deterministic_input_hash")
        == payload_b.get("deterministic_input_hash"),
        "repeats_a": payload_a.get("repeats"),
        "repeats_b": payload_b.get("repeats"),
        "repeats_match": payload_a.get("repeats") == payload_b.get("repeats"),
        "targets": {},
    }

    mismatch_count = 0

    for target in target_names:
        block_a = targets_a.get(target)
        block_b = targets_b.get(target)
        if not isinstance(block_a, dict) or not isinstance(block_b, dict):
            mismatch_count += 1
            result["targets"][target] = {
                "present_in_a": isinstance(block_a, dict),
                "present_in_b": isinstance(block_b, dict),
                "repeats": [],
            }
            continue

        map_a = _to_repeat_map(block_a)
        map_b = _to_repeat_map(block_b)
        repeat_ids = sorted(set(map_a.keys()) | set(map_b.keys()))
        rows: list[dict[str, Any]] = []

        for repeat_idx in repeat_ids:
            row_a = map_a.get(repeat_idx)
            row_b = map_b.get(repeat_idx)
            if not isinstance(row_a, dict) or not isinstance(row_b, dict):
                mismatch_count += 1
                rows.append(
                    {
                        "repeat": repeat_idx,
                        "present_in_a": isinstance(row_a, dict),
                        "present_in_b": isinstance(row_b, dict),
                    }
                )
                continue

            png_equal = _file_hash(str(row_a.get("png") or "")) == _file_hash(str(row_b.get("png") or ""))
            txt_equal = _file_hash(str(row_a.get("txt") or "")) == _file_hash(str(row_b.get("txt") or ""))
            ansi_equal = _file_hash(str(row_a.get("ansi") or "")) == _file_hash(str(row_b.get("ansi") or ""))
            lock_equal = _file_hash(str(row_a.get("render_lock_json") or "")) == _file_hash(
                str(row_b.get("render_lock_json") or "")
            )
            row_parity_equal = _file_hash(str(row_a.get("row_parity_summary_json") or "")) == _file_hash(
                str(row_b.get("row_parity_summary_json") or "")
            )

            metrics_a = row_a.get("metrics") if isinstance(row_a.get("metrics"), dict) else {}
            metrics_b = row_b.get("metrics") if isinstance(row_b.get("metrics"), dict) else {}
            row_occ_a = metrics_a.get("row_occupancy") if isinstance(metrics_a.get("row_occupancy"), dict) else {}
            row_occ_b = metrics_b.get("row_occupancy") if isinstance(metrics_b.get("row_occupancy"), dict) else {}

            mae_a = float(metrics_a.get("mae_rgb", 0.0) or 0.0)
            mae_b = float(metrics_b.get("mae_rgb", 0.0) or 0.0)
            rmse_a = float(metrics_a.get("rmse_rgb", 0.0) or 0.0)
            rmse_b = float(metrics_b.get("rmse_rgb", 0.0) or 0.0)
            dim_abs_sum_a = int(metrics_a.get("dim_abs_sum", 0) or 0)
            dim_abs_sum_b = int(metrics_b.get("dim_abs_sum", 0) or 0)

            missing_a = int(row_occ_a.get("missing_count", 0) or 0)
            missing_b = int(row_occ_b.get("missing_count", 0) or 0)
            extra_a = int(row_occ_a.get("extra_count", 0) or 0)
            extra_b = int(row_occ_b.get("extra_count", 0) or 0)
            span_delta_a = int(row_occ_a.get("row_span_delta", 0) or 0)
            span_delta_b = int(row_occ_b.get("row_span_delta", 0) or 0)
            text_hash_equal = str(row_occ_a.get("text_sha256_normalized") or "") == str(
                row_occ_b.get("text_sha256_normalized") or ""
            )

            row = {
                "repeat": repeat_idx,
                "png_sha_equal": png_equal,
                "txt_sha_equal": txt_equal,
                "ansi_sha_equal": ansi_equal,
                "render_lock_sha_equal": lock_equal,
                "row_parity_summary_sha_equal": row_parity_equal,
                "mae_rgb_a": mae_a,
                "mae_rgb_b": mae_b,
                "mae_rgb_delta": round(mae_b - mae_a, 6),
                "rmse_rgb_a": rmse_a,
                "rmse_rgb_b": rmse_b,
                "rmse_rgb_delta": round(rmse_b - rmse_a, 6),
                "dim_abs_sum_a": dim_abs_sum_a,
                "dim_abs_sum_b": dim_abs_sum_b,
                "dim_abs_sum_delta": dim_abs_sum_b - dim_abs_sum_a,
                "missing_count_a": missing_a,
                "missing_count_b": missing_b,
                "missing_count_delta": missing_b - missing_a,
                "extra_count_a": extra_a,
                "extra_count_b": extra_b,
                "extra_count_delta": extra_b - extra_a,
                "row_span_delta_a": span_delta_a,
                "row_span_delta_b": span_delta_b,
                "row_span_delta_delta": span_delta_b - span_delta_a,
                "text_sha_equal": text_hash_equal,
            }
            rows.append(row)

            if not (
                png_equal
                and txt_equal
                and ansi_equal
                and lock_equal
                and row_parity_equal
                and row["mae_rgb_delta"] == 0.0
                and row["rmse_rgb_delta"] == 0.0
                and row["dim_abs_sum_delta"] == 0
                and row["missing_count_delta"] == 0
                and row["extra_count_delta"] == 0
                and row["row_span_delta_delta"] == 0
                and text_hash_equal
            ):
                mismatch_count += 1

        result["targets"][target] = {
            "present_in_a": True,
            "present_in_b": True,
            "repeat_count_a": len(map_a),
            "repeat_count_b": len(map_b),
            "repeats": rows,
        }

    result["strict_equal"] = (
        result["schema_match"]
        and result["render_profile_match"]
        and result["deterministic_input_hash_equal"]
        and result["repeats_match"]
        and mismatch_count == 0
    )
    result["mismatch_count"] = mismatch_count
    return result


def _write_markdown(payload: dict[str, Any], out_md: Path) -> None:
    lines: list[str] = []
    lines.append("# Ground Truth Run Comparison")
    lines.append("")
    lines.append(f"- run_a: `{payload['run_a']}`")
    lines.append(f"- run_b: `{payload['run_b']}`")
    lines.append(f"- schema_match: `{payload['schema_match']}`")
    lines.append(f"- render_profile_match: `{payload['render_profile_match']}`")
    lines.append(f"- deterministic_input_hash_equal: `{payload['deterministic_input_hash_equal']}`")
    lines.append(f"- repeats_match: `{payload['repeats_match']}`")
    lines.append(f"- strict_equal: `{payload['strict_equal']}`")
    lines.append(f"- mismatch_count: `{payload['mismatch_count']}`")
    lines.append("")

    for target in sorted(payload.get("targets", {}).keys()):
        block = payload["targets"][target]
        lines.append(f"## {target}")
        lines.append("")
        lines.append(
            "| repeat | png | txt | ansi | lock | parity | mae_delta | rmse_delta | dim_delta | missing_delta | extra_delta | span_delta | text_hash |"
        )
        lines.append("|---:|:---:|:---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|---:|:---:|")
        repeats = block.get("repeats", [])
        if not repeats:
            lines.append("| - | - | - | - | - | - | - | - | - | - | - | - |")
        else:
            for row in repeats:
                if "present_in_a" in row and "present_in_b" in row:
                    lines.append(
                        f"| {row['repeat']} | {'Y' if row.get('present_in_a') else 'N'} | {'Y' if row.get('present_in_b') else 'N'} | - | - | - | - | - | - | - | - | - | - |"
                    )
                    continue
                lines.append(
                    "| {repeat} | {png} | {txt} | {ansi} | {lock} | {parity} | {mae:.6f} | {rmse:.6f} | {dim} | {miss} | {extra} | {span} | {text} |".format(
                        repeat=row["repeat"],
                        png="Y" if row["png_sha_equal"] else "N",
                        txt="Y" if row["txt_sha_equal"] else "N",
                        ansi="Y" if row["ansi_sha_equal"] else "N",
                        lock="Y" if row["render_lock_sha_equal"] else "N",
                        parity="Y" if row["row_parity_summary_sha_equal"] else "N",
                        mae=row["mae_rgb_delta"],
                        rmse=row["rmse_rgb_delta"],
                        dim=row["dim_abs_sum_delta"],
                        miss=row["missing_count_delta"],
                        extra=row["extra_count_delta"],
                        span=row["row_span_delta_delta"],
                        text="Y" if row["text_sha_equal"] else "N",
                    )
                )
        lines.append("")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-a", required=True, help="Path to run A directory")
    parser.add_argument("--run-b", required=True, help="Path to run B directory")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default="")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless strict_equal=true")
    args = parser.parse_args()

    run_a = Path(args.run_a).expanduser().resolve()
    run_b = Path(args.run_b).expanduser().resolve()
    out_json = Path(args.out_json).expanduser().resolve()
    out_md = Path(args.out_md).expanduser().resolve() if args.out_md else None

    payload = compare_runs(run_a, run_b)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        _write_markdown(payload, out_md)

    print(json.dumps(payload, indent=2))
    if args.strict and not payload.get("strict_equal", False):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
