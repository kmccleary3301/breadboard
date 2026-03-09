#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v1"
FROZEN_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "cross_system_frozen_slices_v1_n100"


TASK_INPUT_OVERRIDES: dict[str, str] = {
    "mathd_numbertheory_780": (
        "import Mathlib\n"
        "import Aesop\n\n"
        "set_option maxHeartbeats 0\n\n"
        "open BigOperators Real Nat Topology Rat\n\n"
        "theorem mathd_numbertheory_780 (m x : ℤ) "
        "(h₀ : 10 ≤ m) (h₁ : m ≤ 99) "
        "(h₂ : (6 * x) % m = 1) (h₃ : (x - 6 ^ 2) % m = 0) :\n"
        "  m = 43 := by\n"
        "  sorry\n"
    ),
}


PACK_SPECS: dict[str, dict[str, Any]] = {
    "pack_a_seedproof_sanity_minif2f_v1": {
        "benchmark_slug": "minif2f_v2_s1_seed1337_n100",
        "description": "Published-proof sanity pack from Seed-Prover MiniF2F overlap.",
        "task_entries": [
            {
                "task_id": "imo_1977_p6",
                "tier": "gold",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_clean",
            },
            {
                "task_id": "mathd_numbertheory_780",
                "tier": "gold",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_clean",
            },
            {
                "task_id": "mathd_algebra_282",
                "tier": "silver",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_with_placeholders",
            },
            {
                "task_id": "mathd_numbertheory_530",
                "tier": "silver",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_with_placeholders",
            },
            {
                "task_id": "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
                "tier": "silver",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_with_placeholders",
            },
        ],
    },
    "pack_b_hilbert_comparator_minif2f_v1": {
        "benchmark_slug": "minif2f_v2_s1_seed1337_n100",
        "description": "First BreadBoard vs Hilbert comparator pack: anchor problems plus representative frozen-slice tasks.",
        "task_entries": [
            {
                "task_id": "imo_1977_p6",
                "tier": "anchor_gold",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_clean",
            },
            {
                "task_id": "mathd_numbertheory_780",
                "tier": "anchor_gold",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_clean",
            },
            {
                "task_id": "mathd_algebra_282",
                "tier": "anchor_silver",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_with_placeholders",
            },
            {
                "task_id": "mathd_numbertheory_530",
                "tier": "anchor_silver",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_with_placeholders",
            },
            {
                "task_id": "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
                "tier": "anchor_silver",
                "reference_source": "Seed-Prover MiniF2F.zip",
                "reference_quality": "published_with_placeholders",
            },
            {
                "task_id": "mathd_algebra_156",
                "tier": "representative",
                "reference_source": "Frozen MiniF2F slice",
                "reference_quality": "no_published_proof_reference",
            },
            {
                "task_id": "mathd_algebra_171",
                "tier": "representative",
                "reference_source": "Frozen MiniF2F slice",
                "reference_quality": "no_published_proof_reference",
            },
            {
                "task_id": "aime_1984_p5",
                "tier": "representative",
                "reference_source": "Frozen MiniF2F slice",
                "reference_quality": "no_published_proof_reference",
            },
            {
                "task_id": "amc12a_2019_p12",
                "tier": "representative",
                "reference_source": "Frozen MiniF2F slice",
                "reference_quality": "no_published_proof_reference",
            },
            {
                "task_id": "numbertheory_2dvd4expn",
                "tier": "representative",
                "reference_source": "Frozen MiniF2F slice",
                "reference_quality": "no_published_proof_reference",
            },
            {
                "task_id": "mathd_algebra_107",
                "tier": "representative",
                "reference_source": "Frozen MiniF2F slice",
                "reference_quality": "no_published_proof_reference",
            },
        ],
    },
    "pack_c_putnam_frontier_v1": {
        "benchmark_slug": "putnambench_lean_s1_seed1337_n100",
        "description": "Hard frontier pack from the frozen PutnamBench slice. Use only after Pack B is healthy.",
        "task_entries": [
            {"task_id": "putnam_2013_a5", "tier": "frontier"},
            {"task_id": "putnam_1999_b6", "tier": "frontier"},
            {"task_id": "putnam_1978_b4", "tier": "frontier"},
            {"task_id": "putnam_2011_b5", "tier": "frontier"},
            {"task_id": "putnam_1989_a3", "tier": "frontier"},
            {"task_id": "putnam_2004_a5", "tier": "frontier"},
            {"task_id": "putnam_2007_b2", "tier": "frontier"},
            {"task_id": "putnam_2001_b1", "tier": "frontier"},
            {"task_id": "putnam_1986_a5", "tier": "frontier"},
        ],
    },
}


@dataclass(frozen=True)
class HilbertDatasetRow:
    name: str
    header: str
    formal_statement: str
    split: str
    informal_prefix: str
    benchmark: str
    source_pack: str
    reference_tier: str
    reference_source: str
    reference_quality: str


def _load_task_map(benchmark_slug: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    input_path = FROZEN_ROOT / benchmark_slug / "aristotle_task_inputs.json"
    payload = json.loads(input_path.read_text())
    tasks = {task["task_id"]: task for task in payload["tasks"]}
    return tasks, payload


def split_header_and_formal_statement(input_text: str) -> tuple[str, str]:
    theorem_match = re.search(r"(?m)^(theorem|lemma|example)\s", input_text)
    if theorem_match is None:
        raise ValueError("could not locate theorem/lemma/example declaration")
    header = input_text[: theorem_match.start()]
    if re.search(r"\b(sorry|admit)\b", header):
        raise ValueError("header contains sorry/admit and is not valid for Hilbert comparison")
    formal = input_text[theorem_match.start() :]
    formal = re.sub(r"\n\s*sorry\s*$", "\n", formal)
    formal = re.sub(r":=\s*sorry\s*$", ":= by\n", formal)
    if "sorry" in formal:
        raise ValueError("formal statement still contains sorry")
    if not formal.endswith("\n"):
        formal += "\n"
    return header, formal


def canonicalize_task_input_text(*, task_id: str, input_text: str) -> str:
    override = TASK_INPUT_OVERRIDES.get(str(task_id).strip())
    return override if override else input_text


def build_pack(pack_name: str, spec: dict[str, Any]) -> dict[str, Any]:
    benchmark_slug = spec["benchmark_slug"]
    tasks, source_payload = _load_task_map(benchmark_slug)
    pack_dir = ARTIFACT_ROOT / pack_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    rows: list[HilbertDatasetRow] = []
    manifest_entries: list[dict[str, Any]] = []
    for entry in spec["task_entries"]:
        task_id = entry["task_id"]
        task = tasks[task_id]
        input_text = canonicalize_task_input_text(task_id=task_id, input_text=task["input_text"])
        header, formal_statement = split_header_and_formal_statement(input_text)
        rows.append(
            HilbertDatasetRow(
                name=task_id,
                header=header,
                formal_statement=formal_statement,
                split="test",
                informal_prefix="",
                benchmark=source_payload["benchmark"],
                source_pack=pack_name,
                reference_tier=entry["tier"],
                reference_source=entry.get("reference_source", "Frozen slice"),
                reference_quality=entry.get("reference_quality", "unspecified"),
            )
        )
        manifest_entries.append(
            {
                "task_id": task_id,
                "input_hash": task["input_hash"],
                "input_mode": task["input_mode"],
                "reference_tier": entry["tier"],
                "reference_source": entry.get("reference_source", "Frozen slice"),
                "reference_quality": entry.get("reference_quality", "unspecified"),
            }
        )

    dataset_path = pack_dir / "hilbert_dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    manifest = {
        "schema": "breadboard.hilbert_comparison_pack.v1",
        "pack_name": pack_name,
        "description": spec["description"],
        "benchmark_slug": benchmark_slug,
        "source_task_inputs": str(FROZEN_ROOT / benchmark_slug / "aristotle_task_inputs.json"),
        "task_count": len(rows),
        "tasks": manifest_entries,
    }
    manifest_path = pack_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"pack_dir": str(pack_dir), "dataset_path": str(dataset_path), "manifest_path": str(manifest_path)}


def main() -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "schema": "breadboard.hilbert_comparison_pack_summary.v1",
        "artifact_root": str(ARTIFACT_ROOT),
        "packs": {},
    }
    for pack_name, spec in PACK_SPECS.items():
        summary["packs"][pack_name] = build_pack(pack_name, spec)
    summary_path = ARTIFACT_ROOT / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
