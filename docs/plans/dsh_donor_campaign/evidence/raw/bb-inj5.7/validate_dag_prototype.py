#!/opt/homebrew/bin/python3.11
"""PROTOTYPE — validate the campaign DAG and row-routing invariants; delete after spec absorption."""
from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GRAPH = json.loads((ROOT / "dag-prototype.json").read_text(encoding="utf-8"))
ROUTING = json.loads((ROOT / "row-routing.json").read_text(encoding="utf-8"))
NODES = GRAPH["nodes"]

required_metadata = {
    "size",
    "repos",
    "risk",
    "parallelism",
    "attempts",
    "review_rounds",
    "stale_rule",
    "freeze",
    "approval",
    "rollback",
    "kill",
    "failure_branch",
}

for node_id, node in NODES.items():
    missing = sorted(field for field in required_metadata if field not in node)
    if missing:
        raise SystemExit(f"{node_id}: missing metadata {missing}")
    for dependency in node["deps"]:
        if dependency not in NODES:
            raise SystemExit(f"{node_id}: unknown dependency {dependency}")

indegree = {node_id: 0 for node_id in NODES}
outgoing = {node_id: [] for node_id in NODES}
for node_id, node in NODES.items():
    for dependency in node["deps"]:
        outgoing[dependency].append(node_id)
        indegree[node_id] += 1
ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
order: list[str] = []
while ready:
    current = ready.popleft()
    order.append(current)
    for target in sorted(outgoing[current]):
        indegree[target] -= 1
        if indegree[target] == 0:
            ready.append(target)
if len(order) != len(NODES):
    raise SystemExit("graph contains a cycle")

first_tranche = GRAPH["first_tranche"]
if first_tranche != [f"FT-0{index}" for index in range(1, 7)]:
    raise SystemExit(f"unexpected first tranche: {first_tranche}")
for node_id in first_tranche:
    node = NODES[node_id]
    if node["status"] != "first-tranche" or node["freeze"] != "none until explicit amendment node":
        raise SystemExit(f"{node_id}: invalid first-tranche scope")
    if node["kind"] in {"public-contract", "new-seam-implementation", "new-seam-internal-pilot"}:
        raise SystemExit(f"{node_id}: prohibited first-tranche kind")
expected_risks = {
    "FT-01": "high",
    "FT-02": "medium",
    "FT-03": "medium",
    "FT-04": "low",
    "FT-05": "low",
    "FT-06": "medium",
}
for node_id, expected_risk in expected_risks.items():
    if NODES[node_id]["risk"] != expected_risk:
        raise SystemExit(f"{node_id}: risk drift {NODES[node_id]['risk']} != {expected_risk}")

expected_rt_repair_condition = (
    "FT-01 primary_classification is KNOWN_DIVERGENCE; repair only the "
    "reproduced logical-journal divergence at the existing Session event sink"
)
if NODES["RT-REPAIR"]["deps"] != ["EVIDENCE-GATE"]:
    raise SystemExit("RT-REPAIR must depend only on EVIDENCE-GATE")
if NODES["RT-REPAIR"]["condition"] != expected_rt_repair_condition:
    raise SystemExit("RT-REPAIR must open only for KNOWN_DIVERGENCE")
expected_rt_replay_condition = (
    "FT-01 is COHERENT, or FT-01 was KNOWN_DIVERGENCE and RT-REPAIR is "
    "terminal; PRODUCT_RED and every other terminal defect close replay"
)
if NODES["RT-REPLAY"]["deps"] != ["EVIDENCE-GATE", "FT-04"]:
    raise SystemExit("RT-REPLAY must join EVIDENCE-GATE and FT-04")
if NODES["RT-REPLAY"]["condition"] != expected_rt_replay_condition:
    raise SystemExit("RT-REPLAY must exclude PRODUCT_RED and terminal defects")

if GRAPH["unconditionally_promoted_new_seams"] != 0:
    raise SystemExit("a new seam was promoted without evidence")
design_gate_terms = ("common caller", ">=2 real", "deletion payoff", "three interface designs")
for node_id, node in NODES.items():
    if node_id.startswith("DIT-") and any(term not in node["condition"] for term in design_gate_terms):
        raise SystemExit(f"{node_id}: incomplete design-it-twice admission predicate")
for node_id, node in NODES.items():
    if node["kind"].startswith("new-seam"):
        if not any(dependency.startswith("DIT-") for dependency in node["deps"]):
            raise SystemExit(f"{node_id}: missing design-it-twice dependency")
        if "Kyle" not in node["approval"]:
            raise SystemExit(f"{node_id}: missing Kyle seam approval")

public_order = GRAPH["public_generation_order"]
if public_order[:3] != ["PUBLIC-AMEND", "CONTRACT-SCHEMA", "GENERATE-BINDINGS"]:
    raise SystemExit("public generation order does not begin at amendment/contract/generator")
if public_order[-2:] != ["INSTALLED-COMPOSITION", "E4-CONFORMANCE"]:
    raise SystemExit("public generation order does not end at installed/E4 consumers")

rows = ROUTING["rows"]
row_ids = [row["id"] for row in rows]
if ROUTING["denominator"] != 623 or len(rows) != 623 or len(set(row_ids)) != 623:
    raise SystemExit("row routing is not 623/623 unique")
if {row["dsh_phase"] for row in rows} != set(range(13)):
    raise SystemExit("not every DSH phase 0-12 is represented")
if any(not row["workstream"] or not row["decision"] or not row["gate"] for row in rows):
    raise SystemExit("orphan routed row")
selected = [row for row in rows if row["decision"] == "promoted-with-first-tranche-packet"]
if {row["id"] for row in selected} != {"S1", "S2", "S4", "S7", "S8"}:
    raise SystemExit("unexpected promoted ledger rows")

print(json.dumps({
    "verdict": "PASS",
    "nodes": len(NODES),
    "acyclic_order_count": len(order),
    "first_tranche_packets": len(first_tranche),
    "routed_rows": len(rows),
    "unique_rows": len(set(row_ids)),
    "dsh_phases": sorted({row["dsh_phase"] for row in rows}),
    "decision_counts": dict(sorted(Counter(row["decision"] for row in rows).items())),
    "unconditionally_promoted_new_seams": GRAPH["unconditionally_promoted_new_seams"],
}, indent=2, sort_keys=True))
