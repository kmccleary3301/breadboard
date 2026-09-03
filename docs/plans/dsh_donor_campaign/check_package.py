#!/usr/bin/env python3
"""Validate the committed, self-contained DSH donor campaign handoff."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
HEADS = (
    "b3cacc7356244253305f8a6f84308a993485bfe2",
    "73d6e6f55a238fc9ff0486bbcc9ecffe85705715",
)
REQUIRED = (
    ROOT / "DSH_DONOR_CAMPAIGN_SPEC.md",
    ROOT / "COMPLETION_AUDIT.md",
    EVIDENCE / "03_EVIDENCE_AND_APPROVAL_CONTRACT.md",
    EVIDENCE / "05_FIRST_TRANCHE_PACKET_SET.md",
    EVIDENCE / "05_FIRST_TRANCHE_PACKET_SET_REVIEW.md",
    EVIDENCE / "07_CAMPAIGN_SPEC_PROTOTYPE_REVIEW.md",
    EVIDENCE / "raw/bb-inj5.2/surface-inventory.json",
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

    spec = REQUIRED[0].read_text(encoding="utf-8")
    packets = REQUIRED[3].read_text(encoding="utf-8")
    packet_review = REQUIRED[4].read_text(encoding="utf-8")
    prototype_review = REQUIRED[5].read_text(encoding="utf-8")
    surface = json.loads(REQUIRED[6].read_text(encoding="utf-8"))

    for head in HEADS:
        if head not in spec:
            findings.append(f"pinned head absent from specification: {head}")
    if len(re.findall(r"^### FT-0[1-6] — ", spec, re.MULTILINE)) != 6:
        findings.append("specification does not contain exactly six packet sections")
    for packet in (f"FT-0{index}" for index in range(1, 7)):
        if packet not in packets:
            findings.append(f"reviewed packet contract missing: {packet}")
    for label, review in (
        ("G-F", packet_review),
        ("G-H", prototype_review),
    ):
        escalation = review.rpartition("Post-contract escalation review")[2]
        if "APPROVED" not in escalation or "P0/P1/P2/P3" not in escalation:
            findings.append(f"{label} exact-artifact escalation review is not approved")
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

    for document in (spec, packets):
        for target in set(re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", document)):
            if not (ROOT / target).is_file() and not (EVIDENCE / target).is_file():
                findings.append(f"broken committed campaign link: {target}")

    result = {"status": "PASS" if not findings else "FAIL", "findings": findings}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
