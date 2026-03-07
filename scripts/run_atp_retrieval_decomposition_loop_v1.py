#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentic_coder_prototype.atp_diagnostics import (
    DIAGNOSTIC_TAXONOMY_VERSION,
    build_diagnostic_record,
)
from breadboard.search_loop_v1 import SearchLoop, SearchLoopSpec


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_decomposition_validator(schema_dir: Path) -> Draft202012Validator:
    schema_path = schema_dir / "search_loop_decomposition_node_event_v1.schema.json"
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _load_instrumentation_validator(schema_dir: Path) -> Draft202012Validator:
    schema_path = schema_dir / "atp_runtime_instrumentation_digest_v1.schema.json"
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _load_retrieval_snapshot_validator(schema_dir: Path) -> Draft202012Validator:
    schema_path = schema_dir / "atp_retrieval_snapshot_v1.schema.json"
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _load_repair_policy_validator(schema_dir: Path) -> Draft202012Validator:
    schema_path = schema_dir / "atp_repair_policy_decision_v1.schema.json"
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _load_diagnostic_validator(schema_dir: Path) -> Draft202012Validator:
    schema_path = schema_dir / "atp_diagnostic_record_v1.schema.json"
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _load_decomposition_trace_validator(schema_dir: Path) -> Draft202012Validator:
    schema_path = schema_dir / "search_loop_decomposition_trace_v1.schema.json"
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _build_decomposition_events(loop_id: str, lineage: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for node in lineage.get("nodes", []):
        events.append(
            {
                "schema_version": "search_loop_decomposition_node_event_v1",
                "loop_id": loop_id,
                "node_id": node["node_id"],
                "parent_id": node.get("parent_id"),
                "node_type": node.get("node_type", "candidate"),
                "status": node.get("status", "open"),
                "state_ref": node.get("state_ref", {}),
                "score": node.get("score", {}),
            }
        )
    return events


def _validate_events(validator: Draft202012Validator, events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for index, payload in enumerate(events):
        for err in validator.iter_errors(payload):
            loc = ".".join(str(part) for part in err.path)
            path = f"decomposition_events[{index}]"
            if loc:
                path = f"{path}.{loc}"
            errors.append(f"{path}: {err.message}")
    return errors


def _build_decomposition_trace(
    *,
    loop_id: str,
    lineage: dict[str, Any],
    replay_id: str,
    seed: int,
) -> dict[str, Any]:
    trace_nodes: list[dict[str, Any]] = []
    for node in lineage.get("nodes", []):
        node_type = str(node.get("node_type") or "candidate")
        status = str(node.get("status") or "open")
        node_role = "proposal"
        if node_type == "root":
            node_role = "root"
        elif node_type in {"lemma", "subgoal"}:
            node_role = "decomposition"
        elif node_type == "repair":
            node_role = "repair"
        verifier_verdict = "pending"
        if status == "failed":
            verifier_verdict = "fail"
        elif status in {"closed", "verified"}:
            verifier_verdict = "repaired" if node_type == "repair" else "pass"
        trace_nodes.append(
            {
                "node_id": str(node["node_id"]),
                "parent_id": node.get("parent_id"),
                "node_type": node_type,
                "node_role": node_role,
                "status": status,
                "verifier_verdict": verifier_verdict,
                "repair_from": str(node.get("parent_id")) if node_type == "repair" else None,
                "state_ref": dict(node.get("state_ref") or {}),
            }
        )

    return {
        "schema_version": "search_loop_decomposition_trace_v1",
        "trace_id": f"{loop_id}.trace",
        "loop_id": loop_id,
        "lineage_complete": True,
        "replay_id": replay_id,
        "seed": int(seed),
        "nodes": trace_nodes,
    }


def _validate_decomposition_trace(validator: Draft202012Validator, trace: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for err in validator.iter_errors(trace):
        loc = ".".join(str(part) for part in err.path)
        if loc:
            errors.append(f"decomposition_trace.{loc}: {err.message}")
        else:
            errors.append(f"decomposition_trace: {err.message}")
    return errors


def run_loop(
    *,
    fixture_path: Path,
    schema_dir: Path,
    artifacts_dir: Path,
    repair_off: bool = False,
) -> dict[str, Any]:
    started = time.time()
    fixture = _load_json(fixture_path)
    retrieval_request = dict(fixture["retrieval_request"])
    retrieval_response = dict(fixture["retrieval_response"])
    solver_request = dict(fixture["specialist_solver_request"])
    solver_response = dict(fixture["specialist_solver_response"])

    loop_id = "atp.retrieval_decomposition.v1"
    seed = int(fixture.get("seed", 0))
    replay_id = str(fixture.get("replay_id", "atp-retrieval-decomposition-v1"))

    loop = SearchLoop(
        SearchLoopSpec(
            loop_id=loop_id,
            objective=f"retrieve+decompose: {retrieval_request['query']}",
            budget={"max_iters": 5, "max_time_s": 120},
            policy_id="atp.retrieval_decomposition.v1",
            determinism="replayable",
            run_id=f"run-{replay_id}",
            session_id="atp_retrieval_decomposition_v1",
            turn_id="loop-1",
        )
    )
    root_id = loop.start(state_storage_locator="artifacts/retrieval_decomposition/state_root.json")

    hits = list(retrieval_response.get("hits", []))
    top_hit = hits[0] if hits else {"doc_id": "none", "score": 0.0, "source": "unknown"}
    lemma_id = loop.expand(
        parent_id=root_id,
        state_storage_locator="artifacts/retrieval_decomposition/lemma_from_retrieval.json",
        node_type="lemma",
        score={
            "retrieval_doc_id": str(top_hit.get("doc_id", "none")),
            "retrieval_score": float(top_hit.get("score", 0.0)),
        },
    )
    loop.close(node_id=lemma_id, status="closed", reason="retrieval_selected")

    candidate_id = loop.expand(
        parent_id=lemma_id,
        state_storage_locator="artifacts/retrieval_decomposition/candidate_attempt_1.json",
        node_type="candidate",
        score={"attempt": 1, "solver_type": str(solver_request["solver_type"])},
    )
    loop.close(node_id=candidate_id, status="failed", reason="verifier_rejected")
    repair_policy_log: list[dict[str, Any]] = [
        {
            "schema_version": "atp_repair_policy_decision_v1",
            "loop_id": loop_id,
            "node_id": candidate_id,
            "decision_index": 1,
            "diagnostic_class": "tactic_failure",
            "selected_operator": "repair_off" if repair_off else "repair_local_rewrite",
            "ablation_repair_off": bool(repair_off),
            "justification": "Ablation active: skip repair path."
            if repair_off
            else "Tactic failure on first candidate; attempt local rewrite repair.",
            "features": {
                "attempt_index": 1,
                "budget_remaining_steps": 4,
                "goals_estimate": 1,
                "goal_complexity_estimate": 1.0,
                "prior_failures": 0,
                "fallback_used_previous": False,
            },
        }
    ]
    repair_id = candidate_id
    repair_status = "failed"
    if not repair_off:
        repair_status = "verified" if bool(solver_response.get("ok")) else "failed"
        repair_id = loop.expand(
            parent_id=candidate_id,
            state_storage_locator="artifacts/retrieval_decomposition/repair_attempt_2.json",
            node_type="repair",
            score={"attempt": 2, "fallback_required": bool(solver_response.get("fallback_required", False))},
        )
        loop.close(node_id=repair_id, status=repair_status, reason="solver_response")

    lineage = loop.lineage_artifact()
    events = loop.event_log()
    replay = SearchLoop.from_event_log(spec=loop.spec, event_log=events)
    replay_lineage = replay.lineage_artifact()
    replay_ok = lineage.get("nodes") == replay_lineage.get("nodes")
    verifier_latencies = [82, 119] if repair_status == "verified" else [95, 137]
    instrumentation_digest = {
        "schema_version": "atp_runtime_instrumentation_digest_v1",
        "proof_attempt_count": 2 if not repair_off else 1,
        "verifier_call_count": len(verifier_latencies),
        "verifier_latency_ms": {
            "p50": float(sorted(verifier_latencies)[len(verifier_latencies) // 2]),
            "p95": float(max(verifier_latencies)),
        },
        "repair_attempt_count": 0 if repair_off else 1,
        "repair_success_rate_by_class": {"tactic_failure": 1.0 if repair_status == "verified" else 0.0},
        "retrieval_calls": 1,
        "retrieval_hit_rate": 1.0 if len(hits) > 0 else 0.0,
        "search_node_expansions": max(0, len(lineage.get("nodes", [])) - 1),
        "fallback_invocations": 1 if bool(solver_response.get("fallback_required", False)) else 0,
        "budget_usage": {
            "tokens": 800,
            "cost_usd": 0.0015,
            "time_s": 1.0,
        },
        "replayability_status": "replay_ok" if replay_ok else "replay_failed",
    }
    instrumentation_validator = _load_instrumentation_validator(schema_dir)
    instrumentation_errors: list[str] = []
    for err in instrumentation_validator.iter_errors(instrumentation_digest):
        loc = ".".join(str(part) for part in err.path)
        instrumentation_errors.append(f"instrumentation_digest{'.' + loc if loc else ''}: {err.message}")

    retrieval_snapshot = {
        "schema_version": "atp_retrieval_snapshot_v1",
        "corpus_snapshot_id": str(fixture.get("corpus_snapshot_id") or "mathlib-corpus-snapshot-v1"),
        "index_build_hash": str(fixture.get("index_build_hash") or "mathlib-index-build-hash-v1"),
        "retriever_version": str(fixture.get("retriever_version") or "bm25+rnr-v1"),
        "query_model_version": str(fixture.get("query_model_version") or "query-embed-v1"),
        "topk_docs_with_scores": [
            {
                "rank": idx + 1,
                "doc_id": str(row.get("doc_id") or "unknown"),
                "score": float(row.get("score") or 0.0),
                "source": str(row.get("source") or "unknown"),
            }
            for idx, row in enumerate(hits)
        ]
        or [{"rank": 1, "doc_id": "none", "score": 0.0, "source": "unknown"}],
    }
    retrieval_snapshot_validator = _load_retrieval_snapshot_validator(schema_dir)
    retrieval_snapshot_errors: list[str] = []
    for err in retrieval_snapshot_validator.iter_errors(retrieval_snapshot):
        loc = ".".join(str(part) for part in err.path)
        retrieval_snapshot_errors.append(f"retrieval_snapshot{'.' + loc if loc else ''}: {err.message}")

    diagnostics: list[dict[str, Any]] = [
        build_diagnostic_record(
            loop_id=loop_id,
            node_id=candidate_id,
            raw_code="tactic_failed",
            message="tactic failed to close goal; retry with repair",
            source="verifier",
        )
    ]
    if repair_status != "verified":
        diagnostics.append(
            build_diagnostic_record(
                loop_id=loop_id,
                node_id=repair_id,
                raw_code="proof_incomplete",
                message="repair attempt did not verify",
                source="verifier",
            )
        )
    repair_policy_validator = _load_repair_policy_validator(schema_dir)
    repair_policy_errors: list[str] = []
    for index, decision in enumerate(repair_policy_log):
        for err in repair_policy_validator.iter_errors(decision):
            loc = ".".join(str(part) for part in err.path)
            prefix = f"repair_policy_log[{index}]"
            repair_policy_errors.append(f"{prefix}{'.' + loc if loc else ''}: {err.message}")
    diagnostic_validator = _load_diagnostic_validator(schema_dir)
    diagnostic_errors: list[str] = []
    for index, diagnostic in enumerate(diagnostics):
        for err in diagnostic_validator.iter_errors(diagnostic):
            loc = ".".join(str(part) for part in err.path)
            prefix = f"diagnostics[{index}]"
            diagnostic_errors.append(f"{prefix}{'.' + loc if loc else ''}: {err.message}")

    decomposition_events = _build_decomposition_events(loop_id=loop_id, lineage=lineage)
    validator = _load_decomposition_validator(schema_dir)
    event_errors = _validate_events(validator, decomposition_events)
    decomposition_trace = _build_decomposition_trace(
        loop_id=loop_id,
        lineage=lineage,
        replay_id=replay_id,
        seed=seed,
    )
    trace_validator = _load_decomposition_trace_validator(schema_dir)
    trace_errors = _validate_decomposition_trace(trace_validator, decomposition_trace)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    lineage_path = artifacts_dir / "lineage.json"
    events_path = artifacts_dir / "events.json"
    decomposition_path = artifacts_dir / "decomposition_events.json"
    decomposition_trace_path = artifacts_dir / "decomposition_trace.json"
    diagnostics_path = artifacts_dir / "diagnostics.json"
    repair_policy_log_path = artifacts_dir / "repair_policy_log.json"
    instrumentation_path = artifacts_dir / "instrumentation_digest.json"
    lineage_path.write_text(json.dumps(lineage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    events_path.write_text(json.dumps(events, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    decomposition_path.write_text(json.dumps(decomposition_events, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    decomposition_trace_path.write_text(json.dumps(decomposition_trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    repair_policy_log_path.write_text(json.dumps(repair_policy_log, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    instrumentation_path.write_text(
        json.dumps(instrumentation_digest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "schema": "breadboard.atp.retrieval_decomposition_report.v1",
        "generated_at": time.time(),
        "duration_s": round(time.time() - started, 3),
        "seed": seed,
        "replay_id": replay_id,
        "solved": repair_status == "verified",
        "retrieval": {
            "query": retrieval_request["query"],
            "k": retrieval_request["k"],
            "scope": retrieval_request["scope"],
            "hit_count": len(hits),
            "top_doc_id": str(top_hit.get("doc_id", "none")),
            "top_score": float(top_hit.get("score", 0.0)),
        },
        "retrieval_snapshot": retrieval_snapshot,
        "retrieval_snapshot_validation_ok": len(retrieval_snapshot_errors) == 0,
        "retrieval_snapshot_validation_errors": retrieval_snapshot_errors,
        "solver": {
            "solver_type": solver_request["solver_type"],
            "ok": bool(solver_response["ok"]) and not repair_off,
            "fallback_required": bool(solver_response.get("fallback_required", False)),
            "result_ref": solver_response["result_ref"],
        },
        "repair_off": bool(repair_off),
        "lineage_path": str(lineage_path.resolve()),
        "events_path": str(events_path.resolve()),
        "decomposition_events_path": str(decomposition_path.resolve()),
        "decomposition_event_validation_ok": len(event_errors) == 0,
        "decomposition_event_validation_errors": event_errors,
        "decomposition_trace_path": str(decomposition_trace_path.resolve()),
        "decomposition_trace_validation_ok": len(trace_errors) == 0,
        "decomposition_trace_validation_errors": trace_errors,
        "diagnostic_taxonomy_version": DIAGNOSTIC_TAXONOMY_VERSION,
        "diagnostics_path": str(diagnostics_path.resolve()),
        "diagnostics_validation_ok": len(diagnostic_errors) == 0,
        "diagnostics_validation_errors": diagnostic_errors,
        "diagnostics_unknown_count": sum(
            1 for row in diagnostics if str(row.get("normalized_class")) == "unknown"
        ),
        "repair_policy_log_path": str(repair_policy_log_path.resolve()),
        "repair_policy_validation_ok": len(repair_policy_errors) == 0,
        "repair_policy_validation_errors": repair_policy_errors,
        "repair_attempt_count": 0 if repair_off else 1,
        "repair_success_count": 1 if (not repair_off and repair_status == "verified") else 0,
        "repair_success_rate": 1.0 if (not repair_off and repair_status == "verified") else 0.0,
        "instrumentation_path": str(instrumentation_path.resolve()),
        "instrumentation_validation_ok": len(instrumentation_errors) == 0,
        "instrumentation_validation_errors": instrumentation_errors,
        "instrumentation_digest": instrumentation_digest,
        "replay_ok": replay_ok,
        "node_types": [node.get("node_type") for node in lineage.get("nodes", [])],
        "node_count": len(lineage.get("nodes", [])),
        "event_count": len(events),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default="tests/fixtures/atp_capabilities/retrieval_solver_replay_fixture_v1.json",
    )
    parser.add_argument("--schema-dir", default="docs/contracts/atp/schemas")
    parser.add_argument("--artifacts-dir", default="artifacts/atp_retrieval_decomposition_v1")
    parser.add_argument("--out", required=True)
    parser.add_argument("--repair-off", action="store_true")
    args = parser.parse_args()

    report = run_loop(
        fixture_path=Path(args.fixture),
        schema_dir=Path(args.schema_dir),
        artifacts_dir=Path(args.artifacts_dir),
        repair_off=bool(args.repair_off),
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if (
            report["decomposition_event_validation_ok"]
            and report["decomposition_trace_validation_ok"]
            and report["retrieval_snapshot_validation_ok"]
            and report["diagnostics_validation_ok"]
            and report["repair_policy_validation_ok"]
            and report["instrumentation_validation_ok"]
            and report["replay_ok"]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
