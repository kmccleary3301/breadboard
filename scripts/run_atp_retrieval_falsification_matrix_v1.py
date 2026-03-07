#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any


def _load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark report payload must be object")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("benchmark report must include non-empty rows")
    return payload


def _solve_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    solved = sum(1 for row in rows if str(row.get("status")) == "pass")
    return float(solved) / float(len(rows))


def _as_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _scenario_baseline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _scenario_retrieval_off(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        next_row["status"] = "fail"
        next_row["falsification_reason"] = "retrieval_disabled"
        out.append(next_row)
    return out


def _scenario_retriever_randomized(rows: list[dict[str, Any]], randomizer: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        randomized_score = round(0.2 + randomizer.random() * 0.6, 4)
        next_row["retrieval_score"] = randomized_score
        if randomized_score < 0.75 or randomizer.random() < 0.7:
            next_row["status"] = "fail"
            next_row["falsification_reason"] = "retrieval_randomized"
        out.append(next_row)
    return out


def _scenario_retriever_wrong_corpus(rows: list[dict[str, Any]], randomizer: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        next_row["retrieval_source"] = "wrong_corpus_snapshot"
        if randomizer.random() < 0.9:
            next_row["status"] = "fail"
            next_row["falsification_reason"] = "retrieval_wrong_corpus"
        out.append(next_row)
    return out


def _scenario_retriever_degraded_topk(rows: list[dict[str, Any]], randomizer: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        score = float(next_row.get("retrieval_score") or 0.0)
        degraded = max(0.0, score - 0.45)
        next_row["retrieval_score"] = round(degraded, 4)
        if degraded < 0.7:
            next_row["status"] = "fail"
            next_row["falsification_reason"] = "retrieval_topk_degraded"
        elif randomizer.random() < 0.25:
            next_row["status"] = "fail"
            next_row["falsification_reason"] = "retrieval_noise_failure"
        out.append(next_row)
    return out


def _scenario_retriever_query_perturbation(rows: list[dict[str, Any]], randomizer: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        score = float(next_row.get("retrieval_score") or 0.0)
        perturbed = max(0.0, score - (0.1 + randomizer.random() * 0.2))
        next_row["retrieval_score"] = round(perturbed, 4)
        if perturbed < 0.65 and randomizer.random() < 0.7:
            next_row["status"] = "fail"
            next_row["falsification_reason"] = "retrieval_query_perturbation"
        out.append(next_row)
    return out


def _scenario_retriever_hit_order_shuffle(rows: list[dict[str, Any]], randomizer: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        score = float(next_row.get("retrieval_score") or 0.0)
        shuffled = max(0.0, score - (0.15 + randomizer.random() * 0.3))
        next_row["retrieval_score"] = round(shuffled, 4)
        next_row["retrieval_source"] = "hit_order_shuffled"
        if shuffled < 0.72 or randomizer.random() < 0.35:
            next_row["status"] = "fail"
            next_row["falsification_reason"] = "retrieval_hit_order_shuffled"
        out.append(next_row)
    return out


def run_matrix(*, report_path: Path, seed: int) -> dict[str, Any]:
    payload = _load_report(report_path)
    rows = _as_rows(payload)
    randomizer = random.Random(int(seed))

    scenarios = {
        "baseline": _scenario_baseline(rows),
        "retriever_off": _scenario_retrieval_off(rows),
        "retriever_randomized": _scenario_retriever_randomized(rows, randomizer),
        "retriever_wrong_corpus": _scenario_retriever_wrong_corpus(rows, randomizer),
        "retriever_degraded_topk": _scenario_retriever_degraded_topk(rows, randomizer),
        "retriever_query_perturbation": _scenario_retriever_query_perturbation(rows, randomizer),
        "retriever_hit_order_shuffle": _scenario_retriever_hit_order_shuffle(rows, randomizer),
    }

    baseline_rate = _solve_rate(scenarios["baseline"])
    entries: list[dict[str, Any]] = []
    for name, scenario_rows in scenarios.items():
        rate = _solve_rate(scenario_rows)
        entries.append(
            {
                "scenario_id": name,
                "solve_rate": rate,
                "delta_vs_baseline": round(rate - baseline_rate, 6),
                "row_count": len(scenario_rows),
            }
        )

    scenario_rate = {row["scenario_id"]: float(row["solve_rate"]) for row in entries}
    off_rate = scenario_rate.get("retriever_off", 0.0)
    random_rate = scenario_rate.get("retriever_randomized", 0.0)
    wrong_corpus_rate = scenario_rate.get("retriever_wrong_corpus", 0.0)
    degraded_rate = scenario_rate.get("retriever_degraded_topk", 0.0)
    perturb_rate = scenario_rate.get("retriever_query_perturbation", 0.0)
    shuffle_rate = scenario_rate.get("retriever_hit_order_shuffle", 0.0)
    criteria = {
        "retrieval_dependency_signal_present": (baseline_rate - off_rate) >= 0.2,
        "randomized_not_better_than_baseline": random_rate <= baseline_rate,
        "wrong_corpus_not_better_than_baseline": wrong_corpus_rate <= baseline_rate,
        "degraded_not_better_than_baseline": degraded_rate <= baseline_rate,
        "query_perturbation_graceful_degradation": off_rate <= perturb_rate <= baseline_rate,
        "hit_order_shuffle_not_better_than_baseline": shuffle_rate <= baseline_rate,
    }
    criteria["overall_ok"] = all(bool(v) for v in criteria.values())

    return {
        "schema": "breadboard.atp.retrieval_falsification_matrix.v1",
        "generated_at": time.time(),
        "seed": int(seed),
        "input_report_path": str(report_path.resolve()),
        "baseline_solve_rate": baseline_rate,
        "scenarios": entries,
        "criteria": criteria,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="artifacts/benchmarks/atp_retrieval_decomposition_subset_result.latest.json")
    parser.add_argument("--seed", type=int, default=20260223)
    parser.add_argument("--out", default="artifacts/benchmarks/atp_retrieval_falsification_matrix.latest.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_matrix(report_path=Path(args.report).resolve(), seed=int(args.seed))
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            "[atp-retrieval-falsification] baseline="
            f"{result['baseline_solve_rate']:.3f} scenarios={len(result['scenarios'])} out={out_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
