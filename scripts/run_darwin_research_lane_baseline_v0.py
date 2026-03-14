from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = ROOT / "docs" / "contracts" / "darwin" / "fixtures" / "research_baseline_pack_v0.json"
DEFAULT_OUT = ROOT / "artifacts" / "darwin" / "live_baselines" / "lane.research" / "research_baseline.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_refs(query: dict, strategy: str) -> list[str]:
    rows = list(query["evidence_pool"])
    if strategy == "precision_first":
        return [row["ref_id"] for row in rows if row["kind"] == "core" or row["weight"] >= 0.82]
    if strategy == "coverage_first":
        return [row["ref_id"] for row in rows if row["weight"] >= 0.56]
    if strategy == "bridge_synthesis":
        return [row["ref_id"] for row in rows if row["kind"] != "noise"]
    raise ValueError(f"unknown strategy: {strategy}")


def _score_query(query: dict, strategy: str) -> dict:
    selected = _selected_refs(query, strategy)
    pool = {row["ref_id"]: row for row in query["evidence_pool"]}
    relevant = set(query["required_refs"]) | set(query["bridge_refs"]) | set(query.get("support_refs") or [])
    relevant_selected = [ref_id for ref_id in selected if ref_id in relevant]
    selected_weight = sum(float(pool[ref_id]["weight"]) for ref_id in selected)
    relevant_selected_weight = sum(float(pool[ref_id]["weight"]) for ref_id in relevant_selected)
    relevant_total_weight = sum(float(pool[ref_id]["weight"]) for ref_id in relevant)
    precision = relevant_selected_weight / selected_weight if selected_weight else 0.0
    recall = relevant_selected_weight / relevant_total_weight if relevant_total_weight else 1.0
    bridge_refs = query["bridge_refs"]
    bridge_cov = sum(1 for ref_id in bridge_refs if ref_id in selected) / len(bridge_refs) if bridge_refs else 1.0
    score = min(1.0, 0.55 * recall + 0.35 * precision + 0.10 * bridge_cov)
    return {
        "query_id": query["query_id"],
        "selected_refs": selected,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "bridge_coverage": round(bridge_cov, 6),
        "normalized_score": round(score, 6),
    }


def run_research_baseline(*, strategy: str, pack_path: Path = DEFAULT_PACK) -> dict:
    payload = _load_json(pack_path)
    rows = [_score_query(query, strategy) for query in payload.get("queries") or []]
    mean_score = sum(row["normalized_score"] for row in rows) / len(rows) if rows else 0.0
    return {
        "schema": "breadboard.darwin.research_baseline_summary.v0",
        "strategy": strategy,
        "query_count": len(rows),
        "overall_ok": True,
        "primary_score": round(mean_score, 6),
        "decision_state": "ready" if rows else "empty",
        "rows": rows,
    }


def write_summary(*, strategy: str, out_path: Path = DEFAULT_OUT, pack_path: Path = DEFAULT_PACK) -> dict:
    summary = run_research_baseline(strategy=strategy, pack_path=pack_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": str(out_path), "primary_score": summary["primary_score"], "query_count": summary["query_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the DARWIN research lane baseline or mutation strategy.")
    parser.add_argument("--strategy", default="precision_first", choices=["precision_first", "coverage_first", "bridge_synthesis"])
    parser.add_argument("--pack", default=str(DEFAULT_PACK))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_summary(strategy=args.strategy, out_path=Path(args.out), pack_path=Path(args.pack))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"research_summary={summary['out_path']}")
        print(f"primary_score={summary['primary_score']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
