#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.search_loop_v1 import SearchLoop, SearchLoopSpec


TOY_PROBLEMS: list[dict[str, str]] = [
    {
        "id": "toy_add_zero",
        "statement": "For all natural numbers n, n + 0 = n.",
        "expected_proof": "intro n; exact Nat.add_zero n",
    },
    {
        "id": "toy_mul_one",
        "statement": "For all natural numbers n, n * 1 = n.",
        "expected_proof": "intro n; exact Nat.mul_one n",
    },
    {
        "id": "toy_zero_add",
        "statement": "For all natural numbers n, 0 + n = n.",
        "expected_proof": "intro n; exact Nat.zero_add n",
    },
]


def normalize_verifier_diagnostic(
    *,
    problem_id: str,
    attempt: int,
    expected: str,
    actual: str,
) -> dict[str, Any]:
    return {
        "schema": "breadboard.atp.verifier_diagnostic.v1",
        "problem_id": problem_id,
        "attempt": int(attempt),
        "code": "proof_mismatch",
        "message": "candidate proof does not match expected proof",
        "expected": expected,
        "actual": actual,
    }


def _verify(problem: dict[str, str], candidate: str, attempt: int) -> tuple[bool, dict[str, Any] | None]:
    expected = str(problem["expected_proof"])
    if candidate == expected:
        return True, None
    return False, normalize_verifier_diagnostic(
        problem_id=str(problem["id"]),
        attempt=attempt,
        expected=expected,
        actual=candidate,
    )


def _propose(problem: dict[str, str], attempt: int, previous_diagnostic: dict[str, Any] | None) -> str:
    if attempt <= 1:
        return "intro n; sorry"
    if previous_diagnostic and previous_diagnostic.get("code") == "proof_mismatch":
        return str(problem["expected_proof"])
    return "intro n; simp"


def run_problem(problem: dict[str, str], artifacts_dir: Path) -> dict[str, Any]:
    problem_id = str(problem["id"])
    problem_dir = artifacts_dir / problem_id
    problem_dir.mkdir(parents=True, exist_ok=True)

    loop = SearchLoop(
        SearchLoopSpec(
            loop_id=f"atp.{problem_id}",
            objective=f"prove: {problem['statement']}",
            budget={"max_iters": 3, "max_time_s": 30},
            policy_id="atp.toy.propose_verify_repair.v1",
            determinism="replayable",
            run_id=f"run-{problem_id}",
            session_id="atp_toy_loop_v1",
            turn_id=problem_id,
        )
    )
    root_id = loop.start(state_storage_locator=f"artifacts/{problem_id}/state_root.json")
    (problem_dir / "state_root.json").write_text(json.dumps({"problem": problem}, indent=2) + "\n", encoding="utf-8")

    attempts: list[dict[str, Any]] = []
    diagnostic: dict[str, Any] | None = None
    solved = False
    parent_id = root_id
    for attempt in [1, 2]:
        candidate = _propose(problem, attempt=attempt, previous_diagnostic=diagnostic)
        candidate_path = problem_dir / f"candidate_attempt_{attempt}.txt"
        candidate_path.write_text(candidate + "\n", encoding="utf-8")
        node_id = loop.expand(
            parent_id=parent_id,
            state_storage_locator=f"artifacts/{problem_id}/{candidate_path.name}",
            score={"attempt": attempt},
        )
        ok, diagnostic = _verify(problem, candidate, attempt)
        if ok:
            solved = True
            loop.close(node_id=node_id, status="closed", reason="verified")
            attempts.append({"attempt": attempt, "candidate": candidate, "verified": True, "diagnostic": None})
            break
        loop.close(node_id=node_id, status="failed", reason="verification_failed")
        attempts.append({"attempt": attempt, "candidate": candidate, "verified": False, "diagnostic": diagnostic})
        parent_id = node_id

    lineage = loop.lineage_artifact()
    events = loop.event_log()
    lineage_path = problem_dir / "lineage.json"
    events_path = problem_dir / "events.json"
    lineage_path.write_text(json.dumps(lineage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    events_path.write_text(json.dumps(events, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    replay = SearchLoop.from_event_log(spec=loop.spec, event_log=events)
    replay_lineage = replay.lineage_artifact()
    replay_ok = len(lineage.get("nodes", [])) == len(replay_lineage.get("nodes", []))

    return {
        "problem_id": problem_id,
        "statement": problem["statement"],
        "solved": solved,
        "attempts": attempts,
        "lineage_path": str(lineage_path.resolve()),
        "events_path": str(events_path.resolve()),
        "replay_ok": replay_ok,
    }


def run_all(problems: list[dict[str, str]], artifacts_dir: Path) -> dict[str, Any]:
    started = time.time()
    rows = [run_problem(problem=problem, artifacts_dir=artifacts_dir) for problem in problems]
    solved_count = sum(1 for row in rows if row["solved"])
    replay_ok_count = sum(1 for row in rows if row["replay_ok"])
    return {
        "schema": "breadboard.atp.toy_loop_report.v1",
        "generated_at": time.time(),
        "duration_s": round(time.time() - started, 3),
        "problem_count": len(rows),
        "solved_count": solved_count,
        "solve_rate": (solved_count / len(rows)) if rows else 0.0,
        "replay_ok_count": replay_ok_count,
        "problems": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--artifacts-dir", default="artifacts/atp_toy_loop_v1")
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report = run_all(problems=TOY_PROBLEMS, artifacts_dir=artifacts_dir)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
