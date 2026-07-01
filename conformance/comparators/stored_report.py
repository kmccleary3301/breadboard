from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from conformance.comparators.protocol import ComparatorInput


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _identity(assertion: Mapping[str, Any]) -> str:
    value = assertion.get("assertion_id") or assertion.get("name")
    return str(value) if value is not None else ""


def _canonical_assertion(assertion: Mapping[str, Any], *, failed: bool, mismatch_detail: str | None) -> dict[str, Any]:
    identity = _identity(assertion)
    result = copy.deepcopy(dict(assertion))
    result["assertion_id"] = identity
    result.setdefault("name", identity)
    if failed:
        result["status"] = "failed"
        result["observed"] = {"input_hash_mismatch": mismatch_detail}
    return result


def compare(inp: ComparatorInput) -> dict[str, Any]:
    """Replay a governed stored comparator report with live input-hash checks.

    Existing E4 evidence stores comparator reports but not all original comparator
    source. This comparator is deterministic over the stored report plus current
    role paths: it reproduces the stored assertion identity set when every
    declared input hash still matches, and flips those same identities to failed
    when any input artifact diverges. The validator then diffs status/values
    against the accepted report and names the diverging assertion ids.
    """

    comparator_path = inp["artifacts"].get("comparator_ref") or inp["artifacts"].get("comparator")
    if comparator_path is None:
        raise ValueError("comparator_ref artifact is required")
    stored = json.loads(Path(comparator_path).read_text(encoding="utf-8"))
    input_hashes = stored.get("input_hashes") if isinstance(stored.get("input_hashes"), Mapping) else {}

    repo_root_value = inp.get("repo_root")
    repo_root = Path(repo_root_value) if repo_root_value is not None else Path(__file__).resolve().parents[2]
    workspace_root = repo_root.parent
    mismatches: list[str] = []
    for raw_path, expected_hash in sorted(input_hashes.items()):
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = workspace_root / raw_path if raw_path.startswith("docs_tmp/") else repo_root / raw_path
        if not path.exists():
            mismatches.append(f"{raw_path}: missing")
            continue
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            mismatches.append(f"{raw_path}: expected {expected_hash}, got {actual_hash}")

    mismatch_detail = "; ".join(mismatches) if mismatches else None
    assertions = [
        _canonical_assertion(assertion, failed=bool(mismatches), mismatch_detail=mismatch_detail)
        for assertion in stored.get("assertions", [])
        if isinstance(assertion, Mapping)
    ]
    failed = sum(1 for assertion in assertions if assertion.get("status") == "failed")
    warned = sum(1 for assertion in assertions if assertion.get("status") == "warned")
    report = copy.deepcopy(stored)
    report["schema_version"] = "bb.e4.comparator_report.v1"
    report["comparator_id"] = stored.get("comparator_id") or f"{stored.get('lane_id', 'unknown_lane')}_stored_report_replay"
    report["assertions"] = assertions
    report["failed"] = failed
    report["warned"] = warned
    report["passed"] = len(assertions) - failed - warned
    report.setdefault("details", [])
    return report
