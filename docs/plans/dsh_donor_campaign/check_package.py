#!/usr/bin/env python3
"""Validate the committed, self-contained DSH donor campaign handoff."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
SPEC = ROOT / "DSH_DONOR_CAMPAIGN_SPEC.md"
AUDIT = ROOT / "COMPLETION_AUDIT.md"
PACKETS = EVIDENCE / "05_FIRST_TRANCHE_PACKET_SET.md"
PACKET_REVIEW = EVIDENCE / "05_FIRST_TRANCHE_PACKET_SET_REVIEW.md"
PROTOTYPE = EVIDENCE / "07_CAMPAIGN_SPEC_PROTOTYPE.md"
PROTOTYPE_REVIEW = EVIDENCE / "07_CAMPAIGN_SPEC_PROTOTYPE_REVIEW.md"
SURFACE = EVIDENCE / "raw/bb-inj5.2/surface-inventory.json"
FIXTURES = {
    EVIDENCE / "fixtures/ft03-request-fixture-v1.json": "76e69938aa132dd4f5fd2d35f8d966c7209f4231eb1b9d8fbb27be285b882ce3",
    EVIDENCE / "fixtures/ft03_openai_responses_capture_v1.py": "67346f2db2906107cde1684c9eec920bad43e471f1001825d080c9776682fbab",
    EVIDENCE / "fixtures/ft06_surface_inventory_v1.py": "4e893118da1638949d937747bc15829424dd32f923fb2920ffdb4b85c815029b",
}
HEADS = (
    "b3cacc7356244253305f8a6f84308a993485bfe2",
    "73d6e6f55a238fc9ff0486bbcc9ecffe85705715",
)
REQUIRED = (
    SPEC,
    AUDIT,
    EVIDENCE / "03_EVIDENCE_AND_APPROVAL_CONTRACT.md",
    PACKETS,
    PACKET_REVIEW,
    PROTOTYPE,
    PROTOTYPE_REVIEW,
    SURFACE,
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
    packet_review = PACKET_REVIEW.read_text(encoding="utf-8")
    prototype_review = PROTOTYPE_REVIEW.read_text(encoding="utf-8")
    surface = json.loads(SURFACE.read_text(encoding="utf-8"))

    for head in HEADS:
        if head not in spec:
            findings.append(f"pinned head absent from specification: {head}")
    if len(re.findall(r"^### FT-0[1-6] — ", spec, re.MULTILINE)) != 6:
        findings.append("specification does not contain exactly six packet sections")
    for packet in (f"FT-0{index}" for index in range(1, 7)):
        if packet not in packets:
            findings.append(f"reviewed packet contract missing: {packet}")
    for label, candidate, review in (
        ("G-F", PACKETS, packet_review),
        ("G-H", PROTOTYPE, prototype_review),
    ):
        escalation = review.rpartition("Post-contract escalation review")[2]
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if (
            "APPROVED" not in escalation
            or "P0/P1/P2/P3" not in escalation
            or digest not in escalation
        ):
            findings.append(
                f"{label} exact-artifact escalation review does not approve candidate SHA-256 {digest}"
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

    for document in (spec, packets):
        for target in set(re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", document)):
            if not (ROOT / target).is_file() and not (EVIDENCE / target).is_file():
                findings.append(f"broken committed campaign link: {target}")

    result = {"status": "PASS" if not findings else "FAIL", "findings": findings}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
