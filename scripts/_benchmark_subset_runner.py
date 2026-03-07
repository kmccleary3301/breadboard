from __future__ import annotations

import json
import random
import time
import uuid
import hashlib
from pathlib import Path
from typing import Any


def _load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture must be a JSON object")
    problems = payload.get("problems")
    if not isinstance(problems, list) or not problems:
        raise ValueError("fixture.problems must be a non-empty list")
    return payload


def _build_manifest(
    *,
    benchmark: str,
    dataset: str,
    subset_id: str,
    seed: int,
    max_iterations: int,
    max_seconds: int,
    max_cost_usd: float,
    fixture_relpath: str,
    fixture_sha256: str,
    dataset_snapshot_sha256: str,
) -> dict[str, Any]:
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
        "fixture_relpath": fixture_relpath,
        "fixture_sha256": fixture_sha256,
        "dataset_snapshot_sha256": dataset_snapshot_sha256,
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["manifest_hash_sha256"] = manifest_hash
    return manifest


def run_subset_fixture(
    *,
    fixture_path: Path,
    seed: int,
    max_iterations: int,
    max_seconds: int,
    max_cost_usd: float,
) -> dict[str, Any]:
    payload = _load_fixture(fixture_path)
    fixture_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        fixture_relpath = str(fixture_path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        fixture_relpath = str(fixture_path.resolve())
    dataset_snapshot_sha256_raw = payload.get("dataset_snapshot_sha256")
    dataset_snapshot_sha256 = (
        str(dataset_snapshot_sha256_raw).strip() if isinstance(dataset_snapshot_sha256_raw, str) else fixture_hash
    )
    benchmark = str(payload.get("benchmark") or "benchmark_subset_v1")
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
        fixture_relpath=fixture_relpath,
        fixture_sha256=fixture_hash,
        dataset_snapshot_sha256=dataset_snapshot_sha256,
    )

    randomizer = random.Random(int(seed))
    rows: list[dict[str, Any]] = []
    for problem in payload["problems"]:
        if not isinstance(problem, dict):
            continue
        problem_id = str(problem.get("id") or "").strip()
        if not problem_id:
            continue
        target_status = str(problem.get("target_status") or "pass").strip().lower()
        if target_status not in {"pass", "fail"}:
            target_status = "pass"
        latency_ms = int(60 + randomizer.randint(0, 250))
        cost_usd = round(0.0005 + randomizer.random() * 0.003, 6)
        rows.append(
            {
                "problem_id": problem_id,
                "status": target_status,
                "score": 1.0 if target_status == "pass" else 0.0,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "seed": manifest["seed"],
                "replay_id": manifest["replay_id"],
            }
        )

    solved_count = sum(1 for row in rows if row["status"] == "pass")
    total = len(rows)
    solve_rate = float(solved_count) / float(total) if total else 0.0
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
            "problems_solved": solved_count,
            "solve_rate": solve_rate,
            "latency_p50_ms": sorted([row["latency_ms"] for row in rows])[max(0, total // 2)] if rows else 0,
            "cost_total_usd": round(sum(float(row["cost_usd"]) for row in rows), 6),
        },
        "rows": rows,
    }
    if claim_id:
        report["claim_id"] = claim_id
    else:
        report["no_claim_mode"] = {
            "enabled": True,
            "reason": "exploratory benchmark subset run",
        }
    return report

