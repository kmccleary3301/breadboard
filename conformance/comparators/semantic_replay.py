from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from conformance.comparators.protocol import ComparatorInput

COMPARATOR_ID = "semantic_replay_v1"
LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
REPORT_SCHEMA_VERSION = "bb.e4.comparator_report.v1"
OUTPUT_ROOT = Path("artifacts/conformance/comparators/semantic_replay")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(raw_path: str | Path, *, repo_root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    workspace_root = repo_root.parent
    text = path.as_posix()
    if text.startswith(f"{repo_root.name}/"):
        return workspace_root / path
    if text.startswith("docs_tmp/"):
        return workspace_root / path
    return repo_root / path


def _is_accepted_comparator_report(path: Path, *, repo_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) >= 5
        and parts[0] == "docs"
        and parts[1] == "conformance"
        and parts[2] == "e4_target_support"
        and relative.name == "comparator_report.json"
    )


def _artifact_path(artifacts: Mapping[str, Path], *names: str) -> Path | None:
    for name in names:
        path = artifacts.get(name)
        if path is not None:
            return Path(path)
    return None


def _lane_dir(repo_root: Path, lane_id: str) -> Path:
    return repo_root / "docs" / "conformance" / "e4_target_support" / lane_id


def _load_capture(inp: ComparatorInput, *, repo_root: Path, lane_id: str) -> tuple[dict[str, Any], Path | None]:
    artifacts = inp["artifacts"]
    capture_path = _artifact_path(artifacts, "capture_ref", "capture", "raw_capture_manifest")
    if capture_path is not None:
        return _load_json(_resolve_path(capture_path, repo_root=repo_root)), capture_path
    capture = inp.get("capture")
    if isinstance(capture, dict) and capture:
        return dict(capture), None
    default_path = _lane_dir(repo_root, lane_id) / "raw_capture_manifest.json"
    if default_path.exists():
        return _load_json(default_path), default_path
    return {}, None


def _load_replay(inp: ComparatorInput, *, repo_root: Path, lane_id: str) -> tuple[dict[str, Any], Path | None]:
    artifacts = inp["artifacts"]
    replay_path = _artifact_path(artifacts, "replay_ref", "replay", "work_item_replay")
    if replay_path is not None:
        return _load_json(_resolve_path(replay_path, repo_root=repo_root)), replay_path
    replay = inp.get("replay")
    if isinstance(replay, dict) and replay:
        return dict(replay), None
    default_path = _lane_dir(repo_root, lane_id) / "bb_replay_result.json"
    if default_path.exists():
        return _load_json(default_path), default_path
    return {}, None


def _load_probe(repo_root: Path, lane_id: str) -> dict[str, Any]:
    path = _lane_dir(repo_root, lane_id) / "target_probe_output.json"
    return _load_json(path) if path.exists() else {}


def _assertion(assertion_id: str, expected: Any, observed: Any, detail: str) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "name": assertion_id,
        "status": "passed" if observed == expected else "failed",
        "expected": expected,
        "observed": observed,
        "detail": detail,
    }


def _hash_claims_match(
    input_hashes: Mapping[str, Any], *, repo_root: Path
) -> list[dict[str, str]]:
    mismatches: list[dict[str, str]] = []
    for raw_path, expected_hash in sorted(input_hashes.items()):
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
            mismatches.append({"path": str(raw_path), "expected": str(expected_hash), "observed": "invalid input_hash entry"})
            continue
        path = _resolve_path(raw_path, repo_root=repo_root)
        if not path.exists():
            mismatches.append({"path": raw_path, "expected": expected_hash, "observed": "missing"})
            continue
        observed_hash = _sha256(path)
        if observed_hash != expected_hash:
            mismatches.append({"path": raw_path, "expected": expected_hash, "observed": observed_hash})
    return mismatches


def _iter_refs(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"ref", "checkpoint_ref", "resume_from_ref"} and isinstance(child, str):
                yield child
            else:
                yield from _iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_refs(child)


def _check_ref(ref: str, *, repo_root: Path) -> dict[str, str] | None:
    path_text, marker, expected = ref.partition("#sha256:")
    if not marker:
        return {"ref": ref, "expected": "#sha256:<digest>", "observed": "missing digest marker"}
    path = _resolve_path(path_text, repo_root=repo_root)
    if not path.exists():
        return {"ref": ref, "expected": f"sha256:{expected}", "observed": "missing"}
    observed = _sha256(path)
    expected_hash = f"sha256:{expected}"
    if observed != expected_hash:
        return {"ref": ref, "expected": expected_hash, "observed": observed}
    return None


def _capture_lineage_assertion(capture: Mapping[str, Any], probe: Mapping[str, Any], replay: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    captured_by_role = {
        item.get("role"): item
        for item in capture.get("captured_artifacts", [])
        if isinstance(item, Mapping) and isinstance(item.get("role"), str)
    }
    replay_hashes = replay.get("input_hashes") if isinstance(replay.get("input_hashes"), Mapping) else {}
    observed: dict[str, Any] = {}
    for role in ("joined_subagent_target_capture", "detached_subagent_target_capture", "target_probe_output"):
        item = captured_by_role.get(role)
        path_text = item.get("path") if isinstance(item, Mapping) else None
        expected_hash = item.get("sha256") if isinstance(item, Mapping) else None
        actual_hash = _sha256(_resolve_path(path_text, repo_root=repo_root)) if isinstance(path_text, str) else None
        observed[role] = {
            "manifest_hash_matches_file": actual_hash == expected_hash,
            "replay_claims_manifest_hash": replay_hashes.get(path_text) == expected_hash if isinstance(path_text, str) else False,
        }

    observed["probe_capture_kinds"] = sorted(
        capture_item.get("capture_kind")
        for capture_item in probe.get("target_captures", [])
        if isinstance(capture_item, Mapping) and isinstance(capture_item.get("capture_kind"), str)
    )
    expected = {
        "joined_subagent_target_capture": {"manifest_hash_matches_file": True, "replay_claims_manifest_hash": True},
        "detached_subagent_target_capture": {"manifest_hash_matches_file": True, "replay_claims_manifest_hash": True},
        "target_probe_output": {"manifest_hash_matches_file": True, "replay_claims_manifest_hash": True},
        "probe_capture_kinds": ["detached_subagent", "joined_subagent"],
    }
    return _assertion(
        "capture_lineage_hashes_match_replay",
        expected,
        observed,
        "Recomputes capture manifest hashes and target capture kinds, then checks replay input_hash claims.",
    )


def _lifecycle_assertion(probe: Mapping[str, Any], replay: Mapping[str, Any]) -> dict[str, Any]:
    observations = [item for item in probe.get("work_item_observations", []) if isinstance(item, Mapping)]
    records = [item for item in replay.get("normalized_records", []) if isinstance(item, Mapping)]

    observed = {
        "joined_subagent_count": sum(1 for item in observations if item.get("lifecycle_mode") == "joined"),
        "detached_subagent_count": sum(
            1
            for item in observations
            if item.get("lifecycle_mode") == "detached" and item.get("evidence_source") == "target_subagent_lifecycle" and item.get("task_kind") == "subagent"
        ),
        "target_background_count": sum(
            1
            for item in observations
            if item.get("task_kind") == "background" and item.get("evidence_source") == "target_subagent_lifecycle"
        ),
        "cancelled_work_item_count": sum(1 for item in observations if item.get("status") == "cancelled"),
        "job_manager_only_evidence": bool(probe.get("job_manager_only_evidence")),
    }
    expected = {
        "joined_subagent_count": sum(
            1
            for item in records
            if isinstance(item.get("identity"), Mapping)
            and item["identity"].get("task_kind") == "subagent"
            and item["identity"].get("subagent_id") == "joined-subagent"
        ),
        "detached_subagent_count": sum(
            1
            for item in records
            if isinstance(item.get("identity"), Mapping)
            and item["identity"].get("task_kind") == "subagent"
            and item["identity"].get("subagent_id") == "detached-subagent"
        ),
        "target_background_count": sum(
            1
            for item in records
            if isinstance(item.get("identity"), Mapping)
            and isinstance(item.get("delegation"), Mapping)
            and item["identity"].get("task_kind") == "background"
            and item["delegation"].get("delegation_ref") != "job-manager-only-list-poll-cancel"
        ),
        "cancelled_work_item_count": sum(
            1
            for item in records
            if isinstance(item.get("state"), Mapping) and item["state"].get("status") == "cancelled"
        ),
        "job_manager_only_evidence": bool(replay.get("replay_summary", {}).get("job_manager_only_evidence"))
        if isinstance(replay.get("replay_summary"), Mapping)
        else None,
    }
    return _assertion(
        "work_item_lifecycle_counts_match_replay_rows",
        expected,
        observed,
        "Recounts target probe work-item lifecycles and compares them with normalized replay rows.",
    )


def _replay_refs_assertion(replay: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    mismatches = []
    seen: set[str] = set()
    for ref in _iter_refs(replay.get("normalized_records", [])):
        if ref in seen:
            continue
        seen.add(ref)
        mismatch = _check_ref(ref, repo_root=repo_root)
        if mismatch is not None:
            mismatches.append(mismatch)
    return _assertion(
        "replay_artifact_refs_resolve_and_hash_match",
        [],
        mismatches,
        "Verifies every #sha256 artifact ref embedded in normalized replay rows resolves to matching bytes.",
    )


def _input_hash_assertion(replay: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    input_hashes = replay.get("input_hashes") if isinstance(replay.get("input_hashes"), Mapping) else {}
    return _assertion(
        "replay_input_hashes_resolve_and_hash_match",
        [],
        _hash_claims_match(input_hashes, repo_root=repo_root),
        "Recomputes replay input_hashes from current artifact bytes.",
    )


def _provider_scope_assertion(probe: Mapping[str, Any], replay: Mapping[str, Any]) -> dict[str, Any]:
    summary = replay.get("replay_summary") if isinstance(replay.get("replay_summary"), Mapping) else {}
    observed = {
        "fetch_event_count": len(probe.get("fetch_events", [])) if isinstance(probe.get("fetch_events"), list) else None,
        "network_observed": probe.get("network_observed"),
        "provider_authenticated_capture": probe.get("provider_authenticated_capture"),
        "provider_dispatch_observed": probe.get("provider_dispatch_observed"),
        "provider_parity_claimed": probe.get("provider_parity_claimed"),
    }
    expected = {
        "fetch_event_count": summary.get("fetch_event_count"),
        "network_observed": summary.get("network_observed"),
        "provider_authenticated_capture": summary.get("provider_authenticated_capture"),
        "provider_dispatch_observed": summary.get("provider_dispatch_observed"),
        "provider_parity_claimed": summary.get("provider_parity_claimed"),
    }
    return _assertion(
        "provider_network_scope_matches_replay_summary",
        expected,
        observed,
        "Recomputes provider/network scope from probe output and compares it with replay_summary.",
    )


def compare(inp: ComparatorInput, *, output_dir: Path | str | None = None) -> dict[str, Any]:
    repo_root = _repo_root()
    artifacts = inp["artifacts"]
    comparator_path = _artifact_path(artifacts, "comparator_ref", "comparator")
    if comparator_path is not None and _is_accepted_comparator_report(_resolve_path(comparator_path, repo_root=repo_root), repo_root=repo_root):
        raise ValueError("semantic_replay_v1 refuses accepted comparator_report.json paths; write scratch reports under artifacts/conformance/comparators/semantic_replay")

    lane_id = str(inp.get("scope", {}).get("lane_id") or inp.get("replay", {}).get("lane_id") or LANE_ID)
    capture, _ = _load_capture(inp, repo_root=repo_root, lane_id=lane_id)
    replay, replay_path = _load_replay(inp, repo_root=repo_root, lane_id=lane_id)
    if isinstance(inp.get("replay"), dict) and inp["replay"]:
        replay = dict(inp["replay"])
    probe = _load_probe(repo_root, lane_id)

    assertions = [
        _capture_lineage_assertion(capture, probe, replay, repo_root=repo_root),
        _lifecycle_assertion(probe, replay),
        _replay_refs_assertion(replay, repo_root=repo_root),
        _input_hash_assertion(replay, repo_root=repo_root),
        _provider_scope_assertion(probe, replay),
    ]
    failed = sum(1 for assertion in assertions if assertion["status"] == "failed")
    warned = sum(1 for assertion in assertions if assertion["status"] == "warned")
    passed = len(assertions) - failed - warned
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "comparator_id": COMPARATOR_ID,
        "lane_id": lane_id,
        "config_id": replay.get("config_id") or capture.get("config_id"),
        "run_id": replay.get("run_id") or capture.get("run_id"),
        "ok": failed == 0 and warned == 0,
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "details": [
            "Semantic replay pilot recomputes invariants from raw capture, target probe, and replay artifacts.",
            "Scratch report output is outside accepted promotion manifests.",
        ],
        "input_hashes": replay.get("input_hashes", {}) if isinstance(replay.get("input_hashes"), Mapping) else {},
        "assertions": assertions,
    }
    if replay_path is not None:
        report["semantic_inputs"] = {"replay_ref": str(replay_path)}

    output_base = Path(output_dir) if output_dir is not None else repo_root / OUTPUT_ROOT
    output_path = output_base / lane_id / "report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
