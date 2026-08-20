#!/usr/bin/env python3
"""
Generate ANSI->TXT final-frame ordering/placement checks for Phase-5 signoff runs.

Inputs are one or more fresh signoff JSON files produced by
`run_phase4_fresh_recapture_signoff.py` (or equivalent wrappers).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

CSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
OSC_RE = re.compile(r"\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)")
ESC_RE = re.compile(r"\x1B[@-_]")


def strip_ansi(payload: str) -> str:
    out = OSC_RE.sub("", payload)
    out = CSI_RE.sub("", out)
    out = ESC_RE.sub("", out)
    return out


def normalize_text(payload: str) -> str:
    return payload.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        obj = json.loads(raw)
        if isinstance(obj, dict):
            records.append(obj)
    return records


def frame_basename(last_frame: dict[str, Any]) -> str:
    ansi_rel = str(last_frame.get("ansi") or "")
    if ansi_rel:
        return Path(ansi_rel).stem
    frame = last_frame.get("frame")
    if isinstance(frame, int):
        return f"frame_{frame:04d}"
    if isinstance(frame, str) and frame:
        return Path(frame).stem
    raise ValueError("Unable to infer final frame basename from index record.")


def parity_row_clean(parity_summary: dict[str, Any]) -> tuple[bool, int, int, int, list[Any]]:
    parity = parity_summary.get("parity", {}) if isinstance(parity_summary, dict) else {}
    missing = int(parity.get("missing_count") or 0)
    extra = int(parity.get("extra_count") or 0)
    row_span_delta = int(parity.get("row_span_delta") or 0)
    mismatch_rows = parity.get("mismatch_rows") or []
    if not isinstance(mismatch_rows, list):
        mismatch_rows = []
    ok = missing == 0 and extra == 0 and row_span_delta == 0 and len(mismatch_rows) == 0
    return ok, missing, extra, row_span_delta, mismatch_rows


def load_run_result(scenario: str, run_id: str, run_dir: Path) -> dict[str, Any]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"index.jsonl missing: {index_path}")
    records = read_jsonl(index_path)
    if not records:
        raise ValueError(f"index.jsonl has no frame records: {index_path}")
    last = records[-1]
    frame_name = frame_basename(last)

    render_ansi = run_dir / "frames" / f"{frame_name}.render_pane.ansi"
    render_txt = run_dir / "frames" / f"{frame_name}.render_pane.txt"
    # Fallback for runs that don't split render-pane artifacts.
    if not render_ansi.exists():
        render_ansi = run_dir / str(last.get("ansi") or "")
    if not render_txt.exists():
        render_txt = run_dir / str(last.get("text") or "")

    if not render_ansi.exists():
        raise FileNotFoundError(f"final ANSI artifact missing: {render_ansi}")
    if not render_txt.exists():
        raise FileNotFoundError(f"final TXT artifact missing: {render_txt}")

    ansi_raw = normalize_text(render_ansi.read_text(encoding="utf-8", errors="replace"))
    txt_raw = normalize_text(render_txt.read_text(encoding="utf-8", errors="replace"))
    ansi_stripped = normalize_text(strip_ansi(ansi_raw))
    ansi_equals_txt = ansi_stripped == txt_raw

    parity_path = run_dir / str(last.get("render_parity_summary") or "")
    if not parity_path.exists():
        parity_path = run_dir / "frames" / f"{frame_name}.row_parity.json"

    parity_summary: dict[str, Any] = {}
    if parity_path.exists():
        parity_summary = json.loads(parity_path.read_text(encoding="utf-8"))

    row_clean, missing_count, extra_count, row_span_delta, mismatch_rows = parity_row_clean(parity_summary)
    status = "PASS" if ansi_equals_txt and row_clean else "FAIL"

    return {
        "scenario": scenario,
        "scenario_short": scenario.replace("phase4_replay/", "", 1),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "final_frame": frame_name,
        "render_pane_ansi_path": str(render_ansi),
        "render_pane_txt_path": str(render_txt),
        "render_parity_summary_path": str(parity_path) if parity_path.exists() else None,
        "ansi_equals_txt": ansi_equals_txt,
        "row_parity_clean": row_clean,
        "missing_count": missing_count,
        "extra_count": extra_count,
        "row_span_delta": row_span_delta,
        "mismatch_rows": mismatch_rows,
        "ansi_stripped_sha256": sha256_text(ansi_stripped),
        "txt_sha256": sha256_text(txt_raw),
        "status": status,
    }


def collect_runs(signoff_paths: list[Path]) -> list[tuple[str, str, Path]]:
    runs: list[tuple[str, str, Path]] = []
    seen: set[tuple[str, str]] = set()
    for path in signoff_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lanes = payload.get("lanes", {})
        for lane in lanes.values():
            iterations = lane.get("iterations", []) if isinstance(lane, dict) else []
            for iteration in iterations:
                scenario_runs = iteration.get("scenario_runs", []) if isinstance(iteration, dict) else []
                for row in scenario_runs:
                    scenario = str(row.get("scenario") or "").strip()
                    run_id = str(row.get("run_id") or "").strip()
                    run_dir_raw = str(row.get("run_dir") or "").strip()
                    if not scenario or not run_id or not run_dir_raw:
                        continue
                    key = (scenario, run_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    runs.append((scenario, run_id, Path(run_dir_raw).expanduser().resolve()))
    runs.sort(key=lambda r: r[0])
    return runs


def build_markdown(results: list[dict[str, Any]], signoff_paths: list[Path], overall_pass: bool) -> str:
    lines: list[str] = []
    lines.append("# Phase5 Gemini ANSI-to-Text Ordering/Placement Check")
    lines.append("")
    lines.append("- Method: compare final-frame `render_pane.ansi` (ANSI-stripped) against `render_pane.txt`, and require row parity summary zero mismatches/missing/extra rows and zero span delta.")
    lines.append(f"- Inputs: {', '.join(f'`{p}`' for p in signoff_paths)}")
    lines.append(f"- Overall: {'PASS' if overall_pass else 'FAIL'}")
    lines.append("")
    lines.append("| Scenario | Status | render_pane ANSI==TXT | Row Parity | Missing | Extra | Span Delta | Final Frame |")
    lines.append("|---|---|---|---|---:|---:|---:|---|")
    for item in results:
        lines.append(
            "| {scenario} | {status} | {ansi_eq} | {row_clean} | {missing} | {extra} | {span} | {frame} |".format(
                scenario=item["scenario_short"],
                status=item["status"],
                ansi_eq=item["ansi_equals_txt"],
                row_clean=item["row_parity_clean"],
                missing=item["missing_count"],
                extra=item["extra_count"],
                span=item["row_span_delta"],
                frame=item["final_frame"],
            )
        )

    lines.append("")
    lines.append("## Details")
    lines.append("")
    for item in results:
        lines.append(f"### {item['scenario_short']}")
        lines.append(f"- Run: `{item['run_dir']}`")
        lines.append(f"- Final frame: `{item['final_frame']}`")
        lines.append(f"- ANSI-stripped render pane SHA256: `{item['ansi_stripped_sha256']}`")
        lines.append(f"- TXT render pane SHA256: `{item['txt_sha256']}`")
        lines.append(f"- row_parity_clean: `{item['row_parity_clean']}`")
        lines.append(f"- missing_count: `{item['missing_count']}`")
        lines.append(f"- extra_count: `{item['extra_count']}`")
        lines.append(f"- row_span_delta: `{item['row_span_delta']}`")
        lines.append(f"- mismatch_rows: `{item['mismatch_rows']}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--signoff-json",
        action="append",
        required=True,
        help="Path to signoff JSON (repeatable).",
    )
    p.add_argument("--out-json", required=True, help="Output JSON report path.")
    p.add_argument("--out-md", required=True, help="Output Markdown report path.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    signoff_paths = [Path(p).expanduser().resolve() for p in args.signoff_json]
    out_json = Path(args.out_json).expanduser().resolve()
    out_md = Path(args.out_md).expanduser().resolve()

    runs = collect_runs(signoff_paths)
    results = [load_run_result(scenario, run_id, run_dir) for scenario, run_id, run_dir in runs]
    overall_pass = all(r["status"] == "PASS" for r in results)

    payload = {
        "schema_version": "phase5_gemini_ansi_ordering_check_v1",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "inputs": [str(p) for p in signoff_paths],
        "overall_pass": overall_pass,
        "results": results,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(build_markdown(results, signoff_paths, overall_pass), encoding="utf-8")

    print(f"overall_pass={overall_pass} scenarios={len(results)}")
    print(f"json={out_json}")
    print(f"md={out_md}")


if __name__ == "__main__":
    main()
