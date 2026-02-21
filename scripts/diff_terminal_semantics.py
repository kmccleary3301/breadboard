#!/usr/bin/env python3
"""Differential semantic comparison for terminal render artifacts."""

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


def _normalize_text(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _norm_text_sha(path: Path) -> str:
    payload = _normalize_text(path.read_text(encoding="utf-8", errors="replace"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be object: {path}")
    return payload


def _load_index(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _capture_rows_by_frame(run_dir: Path) -> dict[int, dict[str, Any]]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"capture run missing index.jsonl: {run_dir}")
    rows = _load_index(index_path)
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        frame = int(row.get("frame", -1))
        if frame < 0:
            continue
        out[frame] = row
    return out


def _safe_rel(run_dir: Path, rel: str) -> Path | None:
    if not rel:
        return None
    p = (run_dir / rel).resolve()
    try:
        p.relative_to(run_dir.resolve())
    except Exception:
        return None
    return p if p.exists() else None


def compare_capture_runs(reference_run_dir: Path, candidate_run_dir: Path) -> dict[str, Any]:
    ref = _capture_rows_by_frame(reference_run_dir)
    cand = _capture_rows_by_frame(candidate_run_dir)
    frames = sorted(set(ref.keys()) | set(cand.keys()))
    rows: list[dict[str, Any]] = []
    mismatch_count = 0

    for frame in frames:
        r = ref.get(frame)
        c = cand.get(frame)
        if not isinstance(r, dict) or not isinstance(c, dict):
            mismatch_count += 1
            rows.append(
                {
                    "frame": frame,
                    "present_in_reference": isinstance(r, dict),
                    "present_in_candidate": isinstance(c, dict),
                }
            )
            continue

        def rel_or_guess(rec: dict[str, Any], key: str, suffix: str) -> str:
            value = rec.get(key)
            if isinstance(value, str) and value:
                return value
            png = rec.get("png")
            if isinstance(png, str) and png:
                return str(Path(png).with_suffix(suffix))
            return ""

        ref_text = _safe_rel(reference_run_dir, rel_or_guess(r, "text", ".txt"))
        cand_text = _safe_rel(candidate_run_dir, rel_or_guess(c, "text", ".txt"))
        ref_ansi = _safe_rel(reference_run_dir, rel_or_guess(r, "ansi", ".ansi"))
        cand_ansi = _safe_rel(candidate_run_dir, rel_or_guess(c, "ansi", ".ansi"))
        ref_png = _safe_rel(reference_run_dir, rel_or_guess(r, "png", ".png"))
        cand_png = _safe_rel(candidate_run_dir, rel_or_guess(c, "png", ".png"))
        ref_lock = _safe_rel(reference_run_dir, rel_or_guess(r, "render_lock", ".render_lock.json"))
        cand_lock = _safe_rel(candidate_run_dir, rel_or_guess(c, "render_lock", ".render_lock.json"))
        ref_parity = _safe_rel(
            reference_run_dir, rel_or_guess(r, "render_parity_summary", ".row_parity.json")
        )
        cand_parity = _safe_rel(
            candidate_run_dir, rel_or_guess(c, "render_parity_summary", ".row_parity.json")
        )

        text_hash_equal = bool(ref_text and cand_text and _norm_text_sha(ref_text) == _norm_text_sha(cand_text))
        ansi_hash_equal = bool(ref_ansi and cand_ansi and _sha256_file(ref_ansi) == _sha256_file(cand_ansi))
        png_hash_equal = bool(ref_png and cand_png and _sha256_file(ref_png) == _sha256_file(cand_png))
        lock_hash_equal = bool(ref_lock and cand_lock and _sha256_file(ref_lock) == _sha256_file(cand_lock))
        parity_hash_equal = bool(
            ref_parity and cand_parity and _sha256_file(ref_parity) == _sha256_file(cand_parity)
        )

        parity_ref = _load_json(ref_parity, label=f"parity_ref[{frame}]") if ref_parity else {}
        parity_cand = _load_json(cand_parity, label=f"parity_cand[{frame}]") if cand_parity else {}
        p_ref = parity_ref.get("parity") if isinstance(parity_ref.get("parity"), dict) else {}
        p_cand = parity_cand.get("parity") if isinstance(parity_cand.get("parity"), dict) else {}

        row_counts_equal = (
            p_ref.get("missing_count") == p_cand.get("missing_count")
            and p_ref.get("extra_count") == p_cand.get("extra_count")
            and p_ref.get("row_span_delta") == p_cand.get("row_span_delta")
        )
        mismatch_localization_equal = p_ref.get("mismatch_localization") == p_cand.get(
            "mismatch_localization"
        )

        row = {
            "frame": frame,
            "text_hash_equal": text_hash_equal,
            "ansi_hash_equal": ansi_hash_equal,
            "png_hash_equal": png_hash_equal,
            "render_lock_hash_equal": lock_hash_equal,
            "row_parity_hash_equal": parity_hash_equal,
            "row_counts_equal": row_counts_equal,
            "mismatch_localization_equal": mismatch_localization_equal,
        }
        rows.append(row)
        if not all(
            (
                text_hash_equal,
                ansi_hash_equal,
                png_hash_equal,
                lock_hash_equal,
                parity_hash_equal,
                row_counts_equal,
                mismatch_localization_equal,
            )
        ):
            mismatch_count += 1

    return {
        "mode": "capture-run",
        "reference_run_dir": str(reference_run_dir),
        "candidate_run_dir": str(candidate_run_dir),
        "frames": rows,
        "mismatch_count": mismatch_count,
        "strict_equal": mismatch_count == 0,
    }


def _eval_rows_by_target_repeat(run_dir: Path) -> dict[tuple[str, int], dict[str, Any]]:
    metrics_path = run_dir / "comparison_metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"eval run missing comparison_metrics.json: {run_dir}")
    payload = _load_json(metrics_path, label="comparison_metrics")
    targets = payload.get("targets")
    if not isinstance(targets, dict):
        raise ValueError(f"eval run missing targets object: {run_dir}")
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for target, block in targets.items():
        if not isinstance(block, dict):
            continue
        repeats = block.get("repeats")
        if not isinstance(repeats, list):
            continue
        for row in repeats:
            if not isinstance(row, dict):
                continue
            repeat = int(row.get("repeat", 0) or 0)
            if repeat <= 0:
                continue
            out[(str(target), repeat)] = row
    return out


def compare_eval_runs(reference_run_dir: Path, candidate_run_dir: Path) -> dict[str, Any]:
    ref = _eval_rows_by_target_repeat(reference_run_dir)
    cand = _eval_rows_by_target_repeat(candidate_run_dir)
    keys = sorted(set(ref.keys()) | set(cand.keys()))
    rows: list[dict[str, Any]] = []
    mismatch_count = 0

    for key in keys:
        r = ref.get(key)
        c = cand.get(key)
        target, repeat = key
        if not isinstance(r, dict) or not isinstance(c, dict):
            mismatch_count += 1
            rows.append(
                {
                    "target": target,
                    "repeat": repeat,
                    "present_in_reference": isinstance(r, dict),
                    "present_in_candidate": isinstance(c, dict),
                }
            )
            continue

        def exists_hash(path_value: Any) -> str:
            if not isinstance(path_value, str) or not path_value:
                return ""
            p = Path(path_value).resolve()
            return _sha256_file(p) if p.exists() else ""

        metrics_ref = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        metrics_cand = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
        row_ref = metrics_ref.get("row_occupancy") if isinstance(metrics_ref.get("row_occupancy"), dict) else {}
        row_cand = (
            metrics_cand.get("row_occupancy") if isinstance(metrics_cand.get("row_occupancy"), dict) else {}
        )

        row_counts_equal = (
            row_ref.get("missing_count") == row_cand.get("missing_count")
            and row_ref.get("extra_count") == row_cand.get("extra_count")
            and row_ref.get("row_span_delta") == row_cand.get("row_span_delta")
        )
        mismatch_localization_equal = row_ref.get("mismatch_localization") == row_cand.get(
            "mismatch_localization"
        )

        item = {
            "target": target,
            "repeat": repeat,
            "text_hash_equal": str(row_ref.get("text_sha256_normalized") or "")
            == str(row_cand.get("text_sha256_normalized") or ""),
            "png_hash_equal": exists_hash(r.get("png")) == exists_hash(c.get("png")),
            "ansi_hash_equal": exists_hash(r.get("ansi")) == exists_hash(c.get("ansi")),
            "render_lock_hash_equal": exists_hash(r.get("render_lock_json"))
            == exists_hash(c.get("render_lock_json")),
            "row_parity_hash_equal": exists_hash(r.get("row_parity_summary_json"))
            == exists_hash(c.get("row_parity_summary_json")),
            "row_counts_equal": row_counts_equal,
            "mismatch_localization_equal": mismatch_localization_equal,
        }
        rows.append(item)
        if not all(
            (
                item["text_hash_equal"],
                item["png_hash_equal"],
                item["ansi_hash_equal"],
                item["render_lock_hash_equal"],
                item["row_parity_hash_equal"],
                item["row_counts_equal"],
                item["mismatch_localization_equal"],
            )
        ):
            mismatch_count += 1

    return {
        "mode": "eval-run",
        "reference_run_dir": str(reference_run_dir),
        "candidate_run_dir": str(candidate_run_dir),
        "rows": rows,
        "mismatch_count": mismatch_count,
        "strict_equal": mismatch_count == 0,
    }


def compare_runs(reference_run_dir: Path, candidate_run_dir: Path, mode: str) -> dict[str, Any]:
    mode = str(mode or "auto").strip().lower()
    if mode not in {"auto", "capture-run", "eval-run"}:
        raise ValueError(f"unsupported mode: {mode}")
    if mode == "capture-run":
        return compare_capture_runs(reference_run_dir, candidate_run_dir)
    if mode == "eval-run":
        return compare_eval_runs(reference_run_dir, candidate_run_dir)

    if (reference_run_dir / "comparison_metrics.json").exists() and (
        candidate_run_dir / "comparison_metrics.json"
    ).exists():
        return compare_eval_runs(reference_run_dir, candidate_run_dir)
    return compare_capture_runs(reference_run_dir, candidate_run_dir)


def _write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Terminal Semantics Diff")
    lines.append("")
    lines.append(f"- mode: `{payload.get('mode')}`")
    lines.append(f"- strict_equal: `{payload.get('strict_equal')}`")
    lines.append(f"- mismatch_count: `{payload.get('mismatch_count')}`")
    lines.append(f"- reference_run_dir: `{payload.get('reference_run_dir')}`")
    lines.append(f"- candidate_run_dir: `{payload.get('candidate_run_dir')}`")
    lines.append("")
    rows = payload.get("rows") if payload.get("mode") == "eval-run" else payload.get("frames")
    if isinstance(rows, list) and rows:
        for row in rows:
            lines.append(f"- `{row}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Differential semantic comparison for terminal runs.")
    p.add_argument("--reference-run-dir", required=True)
    p.add_argument("--candidate-run-dir", required=True)
    p.add_argument("--mode", default="auto", choices=("auto", "capture-run", "eval-run"))
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", default="")
    p.add_argument("--strict", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        reference_run_dir = Path(args.reference_run_dir).expanduser().resolve()
        candidate_run_dir = Path(args.candidate_run_dir).expanduser().resolve()
        payload = compare_runs(reference_run_dir, candidate_run_dir, mode=args.mode)
        out_json = Path(args.output_json).expanduser().resolve()
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if args.output_md:
            out_md = Path(args.output_md).expanduser().resolve()
            out_md.parent.mkdir(parents=True, exist_ok=True)
            _write_markdown(payload, out_md)
        print(json.dumps(payload, indent=2))
        if args.strict and not bool(payload.get("strict_equal", False)):
            return 2
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
