#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
MINIF2F_FILES = [
    REPO_ROOT.parent / "other_harness_refs" / "miniF2F" / "lean" / "src" / "test.lean",
    REPO_ROOT.parent / "other_harness_refs" / "miniF2F" / "lean" / "src" / "valid.lean",
]
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"
PACKS = {
    "pack_b_hilbert_comparator_minif2f_v2": [
        "imo_1977_p6",
        "mathd_numbertheory_780",
        "mathd_numbertheory_530",
        "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
        "mathd_algebra_156",
        "mathd_algebra_171",
        "aime_1984_p5",
        "amc12a_2019_p12",
        "numbertheory_2dvd4expn",
        "mathd_algebra_107",
    ],
    "pack_b_core_noimo_minif2f_v1": [
        "mathd_numbertheory_780",
        "mathd_numbertheory_530",
        "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
        "mathd_algebra_156",
        "mathd_algebra_171",
        "aime_1984_p5",
        "amc12a_2019_p12",
        "numbertheory_2dvd4expn",
        "mathd_algebra_107",
    ],
    "pack_b_medium_noimo530_minif2f_v1": [
        "mathd_numbertheory_780",
        "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
        "mathd_algebra_156",
        "mathd_algebra_171",
        "aime_1984_p5",
        "amc12a_2019_p12",
        "numbertheory_2dvd4expn",
        "mathd_algebra_107",
    ],
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_theorem_block(text: str, theorem_name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^theorem\s+{re.escape(theorem_name)}\b.*?^end\s*$"
    )
    match = pattern.search(text)
    if not match:
        raise KeyError(theorem_name)
    return match.group(0).strip() + "\n"


def _extract_header_and_statement(block: str) -> tuple[str, str]:
    block = block.strip()
    if ":=\n" in block:
        head, _ = block.split(":=\n", 1)
        return "import Mathlib\n\n", head + " := by\n"
    if ":=\r\n" in block:
        head, _ = block.split(":=\r\n", 1)
        return "import Mathlib\n\n", head + " := by\n"
    if ":=" in block:
        head, _ = block.split(":=", 1)
        return "import Mathlib\n\n", head.rstrip() + " := by\n"
    if "begin" in block:
        head, _ = block.split("begin", 1)
        return "import Mathlib\n\n", head.rstrip() + ":= by\n"
    raise ValueError("unable to split theorem statement")


def _canonical_starter_file(header: str, formal_statement: str) -> str:
    return header + formal_statement + "  sorry\n"


def _canonicalize_formal_statement(task_id: str, formal_statement: str) -> str:
    if task_id == "mathd_numbertheory_530":
        return (
            "theorem mathd_numbertheory_530\n"
            "  (n k : ℕ)\n"
            "  (hpos : 0 < n ∧ 0 < k)\n"
            "  (h_lt : (n : ℝ) / k < 6)\n"
            "  (h_gt : (5 : ℝ) < n / k) :\n"
            "  22 ≤ (Nat.lcm n k) / (Nat.gcd n k) := by\n"
        )
    return formal_statement


def _load_tasks(task_ids: List[str]) -> List[Dict[str, Any]]:
    texts = [path.read_text(encoding="utf-8") for path in MINIF2F_FILES]
    merged = "\n\n".join(texts)
    tasks: List[Dict[str, Any]] = []
    for task_id in task_ids:
        block = _extract_theorem_block(merged, task_id)
        header, formal_statement = _extract_header_and_statement(block)
        formal_statement = _canonicalize_formal_statement(task_id, formal_statement)
        tasks.append(
            {
                "task_id": task_id,
                "header": header,
                "formal_statement": formal_statement,
                "full_file": _canonical_starter_file(header, formal_statement),
                "input_hash": _sha256(header + formal_statement),
            }
        )
    return tasks


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _build_manifest(pack_name: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "run_id": f"hilbert-compare-{pack_name}",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "owner": "atp-team",
        "purpose": "hilbert_comparison",
        "benchmark": {
            "name": "miniF2F",
            "version": {"benchmark_git_sha": "local", "dataset_sha256": _sha256(pack_name + ''.join(t['input_hash'] for t in tasks))},
            "slice": {
                "method": "manual_curated",
                "seed": 1337,
                "n_tasks": len(tasks),
                "task_ids": [task["task_id"] for task in tasks],
            },
        },
        "toolchain": {
            "lean_version": "4.16.0",
            "mathlib_commit": "unknown",
            "docker_image_digest": "sha256:local",
        },
        "budget": {
            "class": "B",
            "max_candidates": 4,
            "max_repair_rounds": 2,
            "wall_clock_cap_s": 480,
            "cost_cap_usd": 10.0,
        },
        "systems": [
            {"system_id": "bb_hilbert_like", "config_ref": "agent_configs/atp_hilbert_like_gpt54_v2.yaml"},
            {"system_id": "hilbert_roselab", "config_ref": "other_harness_refs/ml-hilbert"},
        ],
        "acceptance": {
            "determinism_reruns": 1,
            "required_fields": ["verification_log_digest"],
        },
    }


def build_pack(pack_name: str, out_root: Path) -> Dict[str, Any]:
    tasks = _load_tasks(PACKS[pack_name])
    pack_dir = out_root / pack_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    hilbert_rows = []
    bb_tasks = []
    for task in tasks:
        hilbert_rows.append(
            {
                "name": task["task_id"],
                "header": task["header"],
                "formal_statement": task["formal_statement"],
                "split": "test",
                "informal_prefix": f"/-- Solve {task['task_id']} --/",
            }
        )
        bb_tasks.append(
            {
                "task_id": task["task_id"],
                "input_mode": "formal_lean",
                "input_text": task["full_file"],
                "input_hash": task["input_hash"],
            }
        )

    manifest = _build_manifest(pack_name, tasks)
    dump_json(pack_dir / "cross_system_manifest.json", manifest)
    dump_json(pack_dir / "bb_task_inputs.json", {"schema": "breadboard.bb_task_inputs.v2", "tasks": bb_tasks})
    _write_jsonl(pack_dir / "hilbert_dataset.jsonl", hilbert_rows)

    return {
        "pack_name": pack_name,
        "task_count": len(tasks),
        "task_ids": [task["task_id"] for task in tasks],
        "dir": str(pack_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--pack", action="append")
    args = parser.parse_args()

    out_root = Path(args.out_root).resolve()
    packs = args.pack or list(PACKS)
    summaries = [build_pack(pack_name, out_root) for pack_name in packs]
    dump_json(out_root / "summary.json", {"packs": summaries})
    print(json.dumps({"packs": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
