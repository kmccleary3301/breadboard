#!/usr/bin/env python3
"""Validate the committed, self-contained DSH donor campaign handoff."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
SPEC = ROOT / "DSH_DONOR_CAMPAIGN_SPEC.md"
AUDIT = ROOT / "COMPLETION_AUDIT.md"
PACKETS = EVIDENCE / "05_FIRST_TRANCHE_PACKET_SET.md"
DONOR_INVENTORY = EVIDENCE / "DONOR_ITEMS.yaml"
PACKET_REVIEW = EVIDENCE / "05_FIRST_TRANCHE_PACKET_SET_REVIEW.md"
PROTOTYPE = EVIDENCE / "07_CAMPAIGN_SPEC_PROTOTYPE.md"
PROTOTYPE_REVIEW = EVIDENCE / "07_CAMPAIGN_SPEC_PROTOTYPE_REVIEW.md"
REVIEWED_GATES = (
    ("G-A", EVIDENCE / "00_PROVENANCE_AND_REUSE.md", EVIDENCE / "00_PROVENANCE_AND_REUSE_REVIEW.md", "Final exact-artifact review binding"),
    ("G-B", EVIDENCE / "01_CURRENT_STATE_DONOR_LEDGER.md", EVIDENCE / "01_CURRENT_STATE_DONOR_REVIEW.md", "Final exact-artifact review binding"),
    ("G-C", EVIDENCE / "02_FAULT_BOUNDARY_EVIDENCE.md", EVIDENCE / "02_FAULT_BOUNDARY_REVIEW.md", "Final exact-artifact review binding"),
    ("G-D", EVIDENCE / "03_EVIDENCE_AND_APPROVAL_CONTRACT.md", EVIDENCE / "reviews/bb-inj5.4-consistency-read.txt", "Final exact-artifact review binding"),
    ("G-E", EVIDENCE / "04_NORMATIVE_SEMANTIC_LAWS.md", EVIDENCE / "reviews/bb-inj5.5-consistency-read.txt", "Final exact-artifact review binding"),
    ("G-F", PACKETS, PACKET_REVIEW, "Post-contract escalation review"),
    ("G-G", EVIDENCE / "06_PROMOTED_WORKSTREAM_DAG.md", EVIDENCE / "reviews/bb-inj5.7-consistency-read.txt", "Final exact-artifact review binding"),
    ("G-H", PROTOTYPE, PROTOTYPE_REVIEW, "Post-contract escalation review"),
)
SURFACE = EVIDENCE / "raw/bb-inj5.2/surface-inventory.json"
EXTRACTED_SURFACE = (
    EVIDENCE / "raw/bb-inj5.2/surface-inventory-extracted-v1.json"
)
DAG_ROOT = EVIDENCE / "raw/bb-inj5.7"
DAG_VALIDATOR = DAG_ROOT / "validate_dag_prototype.py"
DAG_GRAPH = DAG_ROOT / "dag-prototype.json"
DAG_ROUTING = DAG_ROOT / "row-routing.json"
DAG_INPUT_DIGESTS = {
    DAG_VALIDATOR: "f9735440c53b197d1f421148d6cd0d05c5595377a695c40b8cf949c7c69ada89",
    DAG_GRAPH: "b475dba4b961f6eba610554f1f4a8312f9227448941b596a1cf2e3be81a50867",
    DAG_ROUTING: "b43ded4b1b69dc4c77857af00c951697f550b7d3005f64c7319dab1607df5fb1",
}
FIXTURES = {
    EVIDENCE / "fixtures/ft03-request-fixture-v1.json": "a817d3b243f0f9c0e67d51dddf8dfe04ae3b04dffc13afc03f484dc8299c4af8",
    EVIDENCE / "fixtures/ft03_openai_responses_capture_v1.py": "67346f2db2906107cde1684c9eec920bad43e471f1001825d080c9776682fbab",
    EVIDENCE / "fixtures/ft06_surface_inventory_v1.py": "d3025ad346b13b699dd315ea71375888a79c905dd31cd01d24ea6a3dd1037445",
}
HEADS = (
    "b3cacc7356244253305f8a6f84308a993485bfe2",
    "73d6e6f55a238fc9ff0486bbcc9ecffe85705715",
)
REQUIRED = (
    SPEC,
    AUDIT,
    DONOR_INVENTORY,
    *(path for _, candidate, review, _ in REVIEWED_GATES for path in (candidate, review)),
    SURFACE,
    EXTRACTED_SURFACE,
    DAG_VALIDATOR,
    DAG_GRAPH,
    DAG_ROUTING,
    *FIXTURES,
)


def main() -> int:
    findings = [
        f"missing committed control input: {path.relative_to(ROOT)}"
        for path in REQUIRED
        if not path.is_file()
    ]
    if findings:
        print(json.dumps({"status": "FAIL", "findings": findings}, indent=2))
        return 1

    spec = SPEC.read_text(encoding="utf-8")
    audit = AUDIT.read_text(encoding="utf-8")
    packets = PACKETS.read_text(encoding="utf-8")

    dag_validation = subprocess.run(
        [sys.executable, str(DAG_VALIDATOR)],
        cwd=DAG_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if dag_validation.returncode != 0:
        findings.append(
            "committed DAG validation failed: "
            + (dag_validation.stderr.strip() or dag_validation.stdout.strip())
        )
    else:
        try:
            dag_result = json.loads(dag_validation.stdout)
        except json.JSONDecodeError as exc:
            findings.append(f"committed DAG validator emitted invalid JSON: {exc}")
        else:
            expected_dag_result = {
                "verdict": "PASS",
                "nodes": 50,
                "acyclic_order_count": 50,
                "first_tranche_packets": 6,
                "routed_rows": 623,
                "unique_rows": 623,
                "decision_counts": {
                    "deferred-behind-named-evidence": 398,
                    "promoted-with-first-tranche-packet": 5,
                    "rejected": 10,
                    "research-only-with-metric-and-kill": 29,
                    "satisfied": 177,
                    "superseded": 4,
                },
                "dsh_phases": list(range(13)),
                "unconditionally_promoted_new_seams": 0,
            }
            for key, expected in expected_dag_result.items():
                if dag_result.get(key) != expected:
                    findings.append(
                        f"committed DAG {key} differs: "
                        f"{dag_result.get(key)!r} != {expected!r}"
                    )
    for path, expected_digest in DAG_INPUT_DIGESTS.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_digest:
            findings.append(
                f"committed DAG input digest differs for "
                f"{path.relative_to(ROOT)}: {digest}"
            )
    surface = json.loads(SURFACE.read_text(encoding="utf-8"))

    for head in HEADS:
        if head not in spec:
            findings.append(f"pinned head absent from specification: {head}")
    if len(re.findall(r"^### FT-0[1-6] — ", spec, re.MULTILINE)) != 6:
        findings.append("specification does not contain exactly six packet sections")
    for packet in (f"FT-0{index}" for index in range(1, 7)):
        if packet not in packets:
            findings.append(f"reviewed packet contract missing: {packet}")
    for label, candidate, review_path, marker in REVIEWED_GATES:
        review = review_path.read_text(encoding="utf-8")
        binding = review.rpartition(marker)[2]
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if (
            not any(verdict in binding for verdict in ("APPROVED", "PASS"))
            or "P0/P1/P2/P3" not in binding
            or "0/0/0/0" not in binding
            or digest not in binding
        ):
            findings.append(
                f"{label} exact-artifact review does not approve candidate "
                f"SHA-256 {digest}"
            )
        if label == "G-A":
            inventory_digest = hashlib.sha256(
                DONOR_INVENTORY.read_bytes()
            ).hexdigest()
            if inventory_digest not in binding:
                findings.append(
                    "G-A exact-artifact review does not bind donor inventory "
                    f"SHA-256 {inventory_digest}"
                )
    packet_digest = hashlib.sha256(PACKETS.read_bytes()).hexdigest()
    if packet_digest not in audit:
        findings.append(
            "completion audit does not bind current first-tranche packet "
            f"SHA-256 {packet_digest}"
        )
    for required_text in (
        "effective adapter payload bytes",
        "started:false",
        "check_package.py",
        "no external goal prompt or uncommitted execution plan is required",
    ):
        if required_text not in f"{spec}\n{packets}":
            findings.append(f"campaign control missing: {required_text}")

    profile_anchors = surface["inventories"]["profiles_and_lanes"]["anchors"]
    expected_anchors = ["agent_configs/", "implementations/profiles/", "config/e4_lanes/"]
    if profile_anchors != expected_anchors:
        findings.append(f"FT-06 profile anchors differ: {profile_anchors!r}")
    surface_digest = hashlib.sha256(SURFACE.read_bytes()).hexdigest()
    if surface_digest != "c384bca85cb83d66246e0aa9fa9c00ca6294daabb03f64bc55a25bcaffcfea4d":
        findings.append(f"FT-06 baseline digest differs: {surface_digest}")
    if surface_digest not in packets or surface_digest not in audit:
        findings.append("FT-06 baseline digest is not bound by packet and completion audit")
    for fixture, expected_digest in FIXTURES.items():
        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        if digest != expected_digest:
            findings.append(
                f"fixture digest differs for {fixture.relative_to(ROOT)}: {digest}"
            )
        if expected_digest not in packets:
            findings.append(
                f"fixture digest is not bound by packet: {fixture.relative_to(ROOT)}"
            )
    extracted_surface_digest = hashlib.sha256(
        EXTRACTED_SURFACE.read_bytes()
    ).hexdigest()
    if extracted_surface_digest != "7777d69a95236bdf57fdeb746dd4c2d3d89f6f96fc446dc97ce9f215284351d6":
        findings.append(
            f"FT-06 extracted baseline digest differs: {extracted_surface_digest}"
        )
    if extracted_surface_digest not in packets or extracted_surface_digest not in audit:
        findings.append(
            "FT-06 extracted baseline digest is not bound by packet and completion audit"
        )

    for document in (spec, packets):
        for target in set(re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", document)):
            if not (ROOT / target).is_file() and not (EVIDENCE / target).is_file():
                findings.append(f"broken committed campaign link: {target}")

    result = {"status": "PASS" if not findings else "FAIL", "findings": findings}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
