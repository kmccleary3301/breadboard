#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-evaluate protofs sample workspaces and regenerate summary.")
    parser.add_argument("--out-dir", required=True, help="Path to artifacts/protofs_samples/<ts> directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    if not out_dir.exists():
        raise SystemExit(f"out dir not found: {out_dir}")

    from scripts.sample_protofs_live_distribution import _eval_workspace, _summarize  # type: ignore

    samples_opencode: List[Dict[str, Any]] = []
    samples_kylecode: List[Dict[str, Any]] = []

    for sample_path in sorted(out_dir.glob("*/sample_summary.json")):
        sample_dir = sample_path.parent
        payload = _load_json(sample_path)
        workspace = payload.get("workspace")
        if not isinstance(workspace, str):
            continue
        ws_path = Path(workspace)
        eval_dir = sample_dir / "eval_v2"
        eval_dir.mkdir(parents=True, exist_ok=True)
        new_eval = _eval_workspace(ws_path, out_dir=eval_dir)
        payload_v2 = dict(payload)
        payload_v2["eval"] = new_eval
        # Keep the original around, but write an updated copy next to it.
        _write_json(sample_dir / "sample_summary_v2.json", payload_v2)

        if payload.get("system") == "opencode":
            samples_opencode.append(payload_v2)
        elif payload.get("system") == "kylecode":
            samples_kylecode.append(payload_v2)

    report_v2 = {
        "meta": _load_json(out_dir / "experiment_meta.json") if (out_dir / "experiment_meta.json").exists() else {},
        "opencode": {"samples": samples_opencode, "summary": _summarize(samples_opencode)},
        "kylecode": {"samples": samples_kylecode, "summary": _summarize(samples_kylecode)},
    }
    _write_json(out_dir / "distribution_report_v2.json", report_v2)

    lines: List[str] = []
    lines.append(f"Output: {out_dir}")
    if samples_opencode:
        s = report_v2["opencode"]["summary"]
        lines.append(f"OpenCode: {s['tests_passed']}/{s['total_runs']} tests passed (exit0={s['exit_code_zero']})")
    if samples_kylecode:
        s = report_v2["kylecode"]["summary"]
        lines.append(f"KyleCode: {s['tests_passed']}/{s['total_runs']} tests passed (exit0={s['exit_code_zero']})")
    (out_dir / "SUMMARY_v2.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

