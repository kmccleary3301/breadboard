#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


DEFAULT_FIXTURE = "tests/fixtures/benchmarks/atp_retrieval_decomposition_subset_v1.json"
DEFAULT_OUT = "artifacts/benchmarks/atp_retrieval_decomposition_subset_result.latest.json"


def _load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture must be JSON object")
    problems = payload.get("problems")
    if not isinstance(problems, list) or not problems:
        raise ValueError("fixture.problems must be non-empty array")
    return payload


def _build_manifest(*, benchmark: str, dataset: str, subset_id: str, seed: int, max_iterations: int, max_seconds: int, max_cost_usd: float) -> dict[str, Any]:
    manifest = {
        "schema_version": "benchmark_run_manifest_v1",
        "manifest_id": f"{benchmark}-{uuid.uuid4().hex[:12]}",
        "benchmark": benchmark,
        "dataset": dataset,
        "subset_id": subset_id,
        "seed": int(seed),
        "replay_id": f"{benchmark}-seed-{int(seed)}",
        "budget": {
            "max_iterations": int(max_iterations),
            "max_seconds": int(max_seconds),
            "max_cost_usd": float(max_cost_usd),
        },
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["manifest_hash_sha256"] = manifest_hash
    return manifest


def _load_decomposition_trace_validator() -> Draft202012Validator:
    repo_root = Path(__file__).resolve().parents[1]
    schema_path = repo_root / "docs" / "contracts" / "atp" / "schemas" / "search_loop_decomposition_trace_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def run_subset(
    *,
    fixture_path: Path,
    seed: int,
    max_iterations: int,
    max_seconds: int,
    max_cost_usd: float,
    artifacts_dir: Path,
) -> dict[str, Any]:
    payload = _load_fixture(fixture_path)
    fixture_sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        fixture_relpath = str(fixture_path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        fixture_relpath = str(fixture_path.resolve())
    dataset_snapshot_sha256_raw = payload.get("dataset_snapshot_sha256")
    dataset_snapshot_sha256 = (
        str(dataset_snapshot_sha256_raw).strip() if isinstance(dataset_snapshot_sha256_raw, str) else fixture_sha256
    )
    benchmark = str(payload.get("benchmark") or "atp_retrieval_decomposition_subset_v1")
    dataset = str(payload.get("dataset") or benchmark)
    subset_id = str(payload.get("subset_id") or "subset")
    claim_id = str(payload.get("claim_id") or "").strip()
    manifest = _build_manifest(
        benchmark=benchmark,
        dataset=dataset,
        subset_id=subset_id,
        seed=seed,
        max_iterations=max_iterations,
        max_seconds=max_seconds,
        max_cost_usd=max_cost_usd,
    )
    manifest["fixture_relpath"] = fixture_relpath
    manifest["fixture_sha256"] = fixture_sha256
    manifest["dataset_snapshot_sha256"] = dataset_snapshot_sha256
    manifest_hash = hashlib.sha256(
        json.dumps({k: v for k, v in manifest.items() if k != "manifest_hash_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["manifest_hash_sha256"] = manifest_hash

    randomizer = random.Random(int(seed))
    trace_validator = _load_decomposition_trace_validator()
    rows: list[dict[str, Any]] = []
    trace_errors: list[str] = []
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for problem in payload["problems"]:
        if not isinstance(problem, dict):
            continue
        problem_id = str(problem.get("id") or "").strip()
        retrieval_doc_id = str(problem.get("retrieval_doc_id") or "").strip()
        solver_type = str(problem.get("solver_type") or "generic_stub").strip()
        retrieval_source = str(problem.get("retrieval_source") or "lean.mathlib").strip()
        retrieval_index_id = str(problem.get("retrieval_index_id") or "mathlib-index-v1").strip()
        retrieval_rank = int(problem.get("retrieval_rank") or 1)
        retrieval_score = float(problem.get("retrieval_score") or round(0.8 + randomizer.random() * 0.19, 4))
        if not problem_id or not retrieval_doc_id:
            continue
        primary_ok = bool(problem.get("primary_ok"))
        fallback_ok = bool(problem.get("fallback_ok"))
        fallback_used = not primary_ok
        final_ok = primary_ok or (fallback_used and fallback_ok)
        status = "pass" if final_ok else "fail"

        latency_ms = int(80 + randomizer.randint(0, 280))
        cost_usd = round(0.0008 + randomizer.random() * 0.004, 6)
        retrieval_doc_hash = hashlib.sha256(retrieval_doc_id.encode("utf-8")).hexdigest()[:16]
        decomposition_trace_payload = {
            "schema_version": "search_loop_decomposition_trace_v1",
            "trace_id": f"{manifest['replay_id']}.{problem_id}.trace",
            "loop_id": f"atp.retrieval_decomposition_subset.{problem_id}",
            "lineage_complete": True,
            "replay_id": manifest["replay_id"],
            "seed": manifest["seed"],
            "nodes": [
                {
                    "node_id": "n000001",
                    "parent_id": None,
                    "node_type": "root",
                    "node_role": "root",
                    "status": "open",
                    "verifier_verdict": "pending",
                    "repair_from": None,
                    "state_ref": {"storage_locator": f"artifacts/{problem_id}/root.json"},
                },
                {
                    "node_id": "n000002",
                    "parent_id": "n000001",
                    "node_type": "lemma",
                    "node_role": "decomposition",
                    "status": "closed",
                    "verifier_verdict": "pass",
                    "repair_from": None,
                    "state_ref": {"storage_locator": f"artifacts/{problem_id}/lemma.json"},
                },
                {
                    "node_id": "n000003",
                    "parent_id": "n000002",
                    "node_type": "candidate",
                    "node_role": "proposal",
                    "status": "failed" if fallback_used else "verified",
                    "verifier_verdict": "fail" if fallback_used else "pass",
                    "repair_from": None,
                    "state_ref": {"storage_locator": f"artifacts/{problem_id}/candidate.json"},
                },
                {
                    "node_id": "n000004",
                    "parent_id": "n000003",
                    "node_type": "repair",
                    "node_role": "repair",
                    "status": "verified" if final_ok else "failed",
                    "verifier_verdict": "repaired" if final_ok and fallback_used else ("pass" if final_ok else "fail"),
                    "repair_from": "n000003",
                    "state_ref": {"storage_locator": f"artifacts/{problem_id}/repair.json"},
                },
            ],
        }
        for err in trace_validator.iter_errors(decomposition_trace_payload):
            loc = ".".join(str(part) for part in err.path)
            key = f"{problem_id}{'.' + loc if loc else ''}"
            trace_errors.append(f"{key}: {err.message}")
        trace_path = artifacts_dir / f"{problem_id}_decomposition_trace.json"
        trace_path.write_text(json.dumps(decomposition_trace_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        rows.append(
            {
                "problem_id": problem_id,
                "status": status,
                "score": 1.0 if final_ok else 0.0,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "seed": manifest["seed"],
                "replay_id": manifest["replay_id"],
                "retrieval_doc_id": retrieval_doc_id,
                "retrieval_source": retrieval_source,
                "retrieval_index_id": retrieval_index_id,
                "retrieval_doc_hash": retrieval_doc_hash,
                "retrieval_rank": retrieval_rank,
                "retrieval_score": retrieval_score,
                "solver_type": solver_type,
                "fallback_used": fallback_used,
                "trace_calls": 2 if fallback_used else 1,
                "node_type_path": ["root", "lemma", "candidate", "repair"],
                "decomposition_trace_ref": str(trace_path.resolve()),
            }
        )

    total = len(rows)
    solved = sum(1 for row in rows if row["status"] == "pass")
    fallback_used_count = sum(1 for row in rows if row["fallback_used"])
    provenance_coverage = sum(1 for row in rows if str(row.get("retrieval_doc_id") or "").strip()) / float(total) if total else 0.0
    report = {
        "schema": "breadboard.benchmark_subset_result.v1",
        "benchmark": benchmark,
        "dataset": dataset,
        "subset_id": subset_id,
        "generated_at": time.time(),
        "fixture_path": str(fixture_path.resolve()),
        "run_manifest": manifest,
        "metrics": {
            "problems_total": total,
            "problems_solved": solved,
            "solve_rate": (float(solved) / float(total)) if total else 0.0,
            "fallback_cases": fallback_used_count,
            "fallback_rate": (float(fallback_used_count) / float(total)) if total else 0.0,
            "retrieval_provenance_coverage": provenance_coverage,
            "latency_p50_ms": sorted([int(row["latency_ms"]) for row in rows])[max(0, total // 2)] if rows else 0,
            "cost_total_usd": round(sum(float(row["cost_usd"]) for row in rows), 6),
        },
        "rows": rows,
        "trace_validation": {
            "ok": len(trace_errors) == 0,
            "error_count": len(trace_errors),
            "errors": trace_errors
        }
    }
    if claim_id:
        report["claim_id"] = claim_id
    else:
        report["no_claim_mode"] = {
            "enabled": True,
            "reason": "exploratory benchmark subset run",
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE)
    parser.add_argument("--seed", type=int, default=20260223)
    parser.add_argument("--max-iterations", type=int, default=256)
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument("--max-cost-usd", type=float, default=5.0)
    parser.add_argument("--artifacts-dir", default="artifacts/benchmarks/atp_retrieval_decomposition_subset")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = run_subset(
        fixture_path=Path(args.fixture).resolve(),
        seed=int(args.seed),
        max_iterations=int(args.max_iterations),
        max_seconds=int(args.max_seconds),
        max_cost_usd=float(args.max_cost_usd),
        artifacts_dir=Path(args.artifacts_dir).resolve(),
    )
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        solved = report["metrics"]["problems_solved"]
        total = report["metrics"]["problems_total"]
        print(f"[atp-retrieval-decomposition-subset-v1] solved={solved}/{total} out={out_path}")
    return 0 if report.get("trace_validation", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
