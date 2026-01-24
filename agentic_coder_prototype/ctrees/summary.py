from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from .schema import CTREE_SCHEMA_VERSION


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_ctree_hash_summary(
    *,
    snapshot: Optional[Dict[str, Any]],
    compiler: Optional[Dict[str, Any]],
    collapse: Optional[Dict[str, Any]],
    runner: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a compact, deterministic summary for parity and UX.

    Intentionally avoids embedding large payloads; favors stable hashes and counts.
    """

    summary: Dict[str, Any] = {
        "schema_version": CTREE_SCHEMA_VERSION,
    }

    if isinstance(snapshot, dict):
        backfilled = bool(snapshot.get("backfilled_from_eventlog"))
        if backfilled:
            summary["backfilled_from_eventlog"] = True
        summary["snapshot"] = {
            "node_count": snapshot.get("node_count"),
            "event_count": snapshot.get("event_count"),
            "last_id": snapshot.get("last_id"),
            "node_hash": None if backfilled else snapshot.get("node_hash"),
        }

    if isinstance(compiler, dict):
        hashes = compiler.get("hashes") if isinstance(compiler.get("hashes"), dict) else {}
        summary["compiler"] = {
            "z1": hashes.get("z1"),
            "z2": hashes.get("z2"),
            "z3": hashes.get("z3"),
        }
        stages = compiler.get("stages") if isinstance(compiler.get("stages"), dict) else {}
        spec = stages.get("SPEC") if isinstance(stages, dict) else None
        selection = spec.get("selection") if isinstance(spec, dict) else None
        if isinstance(selection, dict):
            summary["selection"] = {
                "selection_sha256": selection.get("selection_sha256"),
                "candidate_count": selection.get("candidate_count"),
                "kept_count": selection.get("kept_count"),
                "dropped_count": selection.get("dropped_count"),
                "collapsed_count": selection.get("collapsed_count"),
                "selected_count": selection.get("selected_count"),
            }

    if isinstance(collapse, dict):
        policy = collapse.get("policy") if isinstance(collapse.get("policy"), dict) else {}
        summary["collapse"] = {
            "stage": collapse.get("stage"),
            "policy_kind": policy.get("kind"),
            "target": policy.get("target"),
            "dropped": policy.get("dropped"),
            "collapsed": policy.get("collapsed"),
            "ordering": policy.get("ordering"),
        }

    if isinstance(runner, dict):
        summary["runner"] = {
            "enabled": runner.get("enabled"),
            "branches": runner.get("branches"),
        }

    summary["summary_sha256"] = _sha256({k: v for k, v in summary.items() if k != "summary_sha256"})
    return summary


def format_ctrees_context_note(hash_summary: Dict[str, Any] | None) -> Optional[str]:
    """Format a compact, deterministic note suitable for ephemeral context injection."""

    if not isinstance(hash_summary, dict) or not hash_summary:
        return None

    snapshot = hash_summary.get("snapshot") if isinstance(hash_summary.get("snapshot"), dict) else {}
    compiler = hash_summary.get("compiler") if isinstance(hash_summary.get("compiler"), dict) else {}
    summary_sha256 = hash_summary.get("summary_sha256")
    parts = [
        "C-TREES:",
        f"sha256={summary_sha256}" if summary_sha256 else None,
        f"node_hash={snapshot.get('node_hash')}" if snapshot.get("node_hash") else None,
        f"z1={compiler.get('z1')}" if compiler.get("z1") else None,
        f"z2={compiler.get('z2')}" if compiler.get("z2") else None,
        f"z3={compiler.get('z3')}" if compiler.get("z3") else None,
        f"nodes={snapshot.get('node_count')}" if snapshot.get("node_count") is not None else None,
        f"events={snapshot.get('event_count')}" if snapshot.get("event_count") is not None else None,
    ]
    return " ".join([p for p in parts if p])
