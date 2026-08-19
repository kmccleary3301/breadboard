#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_WORKSPACE_ROOT = ROOT.parent

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
    from scripts.e4_parity.validators.gate_errors import BlameEntry, apply_gate_error_envelope, gate_exit_code
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils
    from validators.gate_errors import BlameEntry, apply_gate_error_envelope, gate_exit_code
try:
    from scripts.e4_parity import lane_inventory_utils as lane_inventory
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import lane_inventory_utils as lane_inventory
try:
    from scripts.e4_parity import catalog_refs
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import catalog_refs
from agentic_coder_prototype.conformance.catalog_binding import stable_entries



INVENTORY_PATH = ROOT / "docs/conformance/e4_lane_inventory.json"
DEFAULT_CATALOG_PATH = ROOT / "docs/conformance/e4_artifact_catalog.json"
_ARTIFACT_LIST_KEYS = ("artifacts", "current_artifacts", "input_artifacts")
_POINT_FIELDS = ("current_accepted_points", "accepted_point_count", "e4_score_after")
_CLAIM_COUNT_FIELDS = (
    "target_support_claims_accepted",
    "accepted_support_claim_count",
    "target_support_claims_after",
)
_LIVE_VALIDATOR_SCHEMA_VERSION = "bb.e4.c4_chain_validation_report.v1"
_TERMINAL_MANIFEST_SCHEMA_VERSION = "bb.e4.oh_my_pi_p6_terminal_hash_manifest.v1"



def sha256_path(path: Path) -> str:
    return _hash_utils.sha256_path(path)


def load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _try_load_json(path: Path) -> Mapping[str, Any] | None:
    if path.suffix.lower() != ".json":
        return None
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _as_path(value: Path | str) -> Path:
    return Path(value).resolve()


def _first_existing(candidates: Sequence[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def resolve_artifact_ref(workspace_root: Path, implementation_checkout: Path, ref: str) -> Path:
    path = Path(ref)
    if path.is_absolute():
        return path.resolve()

    implementation_name = implementation_checkout.name
    if ref.startswith(f"{implementation_name}/"):
        return (workspace_root / ref).resolve()

    # Production reports keep docs_tmp beside the checkout. Unit fixtures often keep
    # docs_tmp inside the synthetic checkout. Prefer the production layout when it
    # exists, but do not reject portable checkout-relative refs.
    if ref.startswith("docs_tmp/"):
        return _first_existing((workspace_root / ref, implementation_checkout / ref))

    return _first_existing((implementation_checkout / ref, workspace_root / ref))

def _strip_ref_suffix(ref: str) -> str:
    return ref.split("#", 1)[0]


def _ref_hash(ref: str) -> str | None:
    if "#" not in ref:
        return None
    for part in ref.replace("#", " ").replace(";", " ").split():
        if part.startswith("sha256:") and len(part) == 71:
            return part
    return None
def _catalog_entries_by_path(catalog: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if catalog is None:
        return {}
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise ValueError("catalog entries must be a list")
    by_path: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        if isinstance(path, str) and path:
            by_path[path] = entry
    return by_path


def _catalog_entry_for_path(catalog: Mapping[str, Any] | None, path: str) -> Mapping[str, Any] | None:
    return _catalog_entries_by_path(catalog).get(_strip_ref_suffix(path))


def _catalog_hash_for_path(catalog: Mapping[str, Any] | None, path: str) -> str | None:
    entry = _catalog_entry_for_path(catalog, path)
    if entry is None:
        return None
    digest = entry.get("sha256")
    return digest if isinstance(digest, str) and digest.startswith("sha256:") else None

def _catalog_blame_entries(
    catalog: Mapping[str, Any] | None,
    *,
    workspace_root: Path,
    implementation_checkout: Path,
) -> tuple[BlameEntry, ...]:
    if catalog is None:
        return ()
    entries = catalog.get("entries")
    if not isinstance(entries, list):
        return ()
    try:
        stable_catalog_entries = stable_entries([item for item in entries if isinstance(item, Mapping)])
    except Exception:
        return ()
    blame: list[BlameEntry] = []
    for entry in stable_catalog_entries:
        role_id = entry.get("role_id")
        path_value = entry.get("path")
        prev_sha256 = entry.get("sha256")
        if not (isinstance(role_id, str) and isinstance(path_value, str) and isinstance(prev_sha256, str)):
            continue
        resolved = resolve_artifact_ref(workspace_root, implementation_checkout, path_value)
        if not resolved.exists() or not resolved.is_file():
            continue
        cur_sha256 = _hash_utils.sha256_path(resolved)
        if cur_sha256 != prev_sha256:
            blame.append(
                BlameEntry(
                    role_id=role_id,
                    path=path_value,
                    prev_sha256=prev_sha256,
                    cur_sha256=cur_sha256,
                )
            )
    return tuple(blame)


def _catalog_ref_for_lane_role(catalog: Mapping[str, Any] | None, lane: Mapping[str, Any], role: str) -> str | None:
    if catalog is None:
        return None
    lane_id = lane.get("lane_id")
    if not isinstance(lane_id, str) or not lane_id:
        return None
    try:
        return catalog_refs.hash_ref(catalog, f"{lane_id}:{role}")
    except KeyError:
        return None





def _accepted_inventory_lanes() -> tuple[dict[str, Any], ...]:
    inventory = lane_inventory.load_inventory(INVENTORY_PATH)
    lanes = inventory.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError(f"{INVENTORY_PATH} must contain lanes list")
    return tuple(lane for lane in lanes if isinstance(lane, dict) and lane.get("status") == "accepted")


def _accounted_inventory_lanes() -> tuple[dict[str, Any], ...]:
    inventory = lane_inventory.load_inventory(INVENTORY_PATH)
    lanes = inventory.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError(f"{INVENTORY_PATH} must contain lanes list")
    return tuple(
        lane
        for lane in lanes
        if isinstance(lane, dict)
        and (lane.get("status") == "accepted" or lane.get("evidence_status") == "accepted")
    )


def _inventory_non_target_claim_count() -> int:
    return sum(1 for lane in _accounted_inventory_lanes() if lane.get("kind") == "non_target_accounting")


def _lane_ct_output(lane: Mapping[str, Any]) -> str:
    ct = lane.get("ct")
    command = ct.get("command") if isinstance(ct, Mapping) else None
    argv = command.get("argv") if isinstance(command, Mapping) else None
    if not isinstance(argv, list):
        raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing ct.command.argv")
    for index, item in enumerate(argv[:-1]):
        if item == "--json-out" and isinstance(argv[index + 1], str):
            return argv[index + 1]
    raise ValueError(f"inventory lane {lane.get('lane_id')!r} missing --json-out")



def _validator_ref_key(lane: Mapping[str, Any]) -> str:
    lane_id = str(lane.get("lane_id", ""))
    ct_output = _lane_ct_output(lane)
    match = re.search(r"_p3_([1-8])_", lane_id)
    if match is not None:
        return f"p3_{match.group(1)}_helper_runtime_validator"
    match = re.search(r"_p6_0_l([1-6])_", lane_id)
    if match is not None:
        return f"p6_l{match.group(1)}_validator"
    if "p6_6_task_job_subagent" in lane_id:
        return "p6_6_task_job_subagent_validator"
    if "p5_l1" in lane_id:
        return "p5_pi_l1_validator"
    if "p5_l2" in lane_id:
        return "p5_pi_l2_extension_session_residual_validator"
    if "p7" in ct_output or "codex" in lane_id:
        return "p7_validator"
    return f"{lane_id.replace('-', '_')}_validator"

def _required_validator_refs_from_inventory(
    refs: Mapping[str, Any],
    catalog: Mapping[str, Any] | None,
) -> list[tuple[str, str, str | None]]:
    refs_by_path: dict[str, str] = {}
    for key, raw_ref in refs.items():
        if isinstance(key, str) and isinstance(raw_ref, str):
            refs_by_path[_strip_ref_suffix(raw_ref)] = key
    required: list[tuple[str, str, str | None]] = []
    for lane in _accepted_inventory_lanes():
        catalog_ref = _catalog_ref_for_lane_role(catalog, lane, "node_gate")
        output = _strip_ref_suffix(catalog_ref) if isinstance(catalog_ref, str) else _lane_ct_output(lane)
        key = refs_by_path.get(output, _validator_ref_key(lane))
        required.append((key, output, catalog_ref))
    return required

def _require_int(errors: list[str], label: str, value: Any, expected: int) -> None:
    if value != expected:
        errors.append(f"{label} expected {expected}, got {value!r}")


def _first_int(payload: Mapping[str, Any], fields: Iterable[str]) -> int | None:
    for field in fields:
        value = payload.get(field)
        if isinstance(value, int):
            return value
    return None


def _accepted_points(payload: Mapping[str, Any]) -> int | None:
    value = _first_int(payload, _POINT_FIELDS)
    if value is not None:
        return value
    reconstructed_score = payload.get("reconstructed_score")
    if isinstance(reconstructed_score, Mapping):
        total = reconstructed_score.get("total")
        if isinstance(total, int):
            return total
    current_state = payload.get("current_state")
    if isinstance(current_state, Mapping):
        accepted_points = current_state.get("accepted_points")
        if isinstance(accepted_points, int):
            return accepted_points
    return None


def _support_claim_count(payload: Mapping[str, Any]) -> int | None:
    value = _first_int(payload, _CLAIM_COUNT_FIELDS)
    if value is not None:
        return value
    accepted_support_claims = payload.get("accepted_support_claims")
    if isinstance(accepted_support_claims, list):
        return len(accepted_support_claims)
    support_claim_ids = payload.get("support_claim_ids")
    if isinstance(support_claim_ids, list):
        return len(support_claim_ids)
    return None


def _artifact_entries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries: list[Mapping[str, Any]] = []
    for key in _ARTIFACT_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            entries.extend(item for item in value if isinstance(item, Mapping))
    return entries


def _validate_json_current_payload(
    *,
    errors: list[str],
    report_label: str,
    index: int,
    raw_ref: str,
    payload: Mapping[str, Any],
    expected_claims: int,
    expected_points: int,
    workspace_root: Path,
    implementation_checkout: Path,
) -> None:
    if payload.get("superseded_by") is not None:
        errors.append(f"{report_label}.artifacts[{index}] current artifact is superseded and must stay historical: {raw_ref}")

    points = _accepted_points(payload)
    if points is not None and points != expected_points:
        errors.append(f"{report_label}.artifacts[{index}] accepted point count expected {expected_points}, got {points!r}: {raw_ref}")

    claim_count = _support_claim_count(payload)
    if claim_count is not None and claim_count != expected_claims:
        errors.append(
            f"{report_label}.artifacts[{index}] support claim count expected {expected_claims}, "
            f"got {claim_count!r}: {raw_ref}"
        )

    if payload.get("schema_version") == "bb.e4.primitive_family_readiness_report.v1":
        if payload.get("ok") is not True:
            errors.append(f"{report_label}.artifacts[{index}] primitive readiness report must have ok=true: {raw_ref}")
        for ref_key in ("ledger_ref", "accepted_report_ref"):
            embedded_ref = payload.get(ref_key)
            if not isinstance(embedded_ref, str) or not embedded_ref:
                errors.append(f"{report_label}.artifacts[{index}] primitive readiness report missing {ref_key}: {raw_ref}")
                continue
            expected_hash = _ref_hash(embedded_ref)
            if expected_hash is None:
                errors.append(f"{report_label}.artifacts[{index}] primitive readiness report {ref_key} missing sha256: {raw_ref}")
                continue
            embedded_path = resolve_artifact_ref(workspace_root, implementation_checkout, _strip_ref_suffix(embedded_ref))
            if not embedded_path.exists():
                errors.append(f"{report_label}.artifacts[{index}] primitive readiness report {ref_key} path missing: {_strip_ref_suffix(embedded_ref)}")
                continue
            actual_hash = sha256_path(embedded_path)
            if actual_hash != expected_hash:
                errors.append(
                    f"{report_label}.artifacts[{index}] primitive readiness report stale {ref_key}: "
                    f"expected {expected_hash}, got {actual_hash}: {raw_ref}"
                )

    if payload.get("schema_version") == _TERMINAL_MANIFEST_SCHEMA_VERSION:
        support_refs = payload.get("accepted_support_claim_refs")
        if not isinstance(support_refs, list):
            errors.append(f"{report_label}.artifacts[{index}] terminal manifest accepted_support_claim_refs must be a list: {raw_ref}")
        else:
            _require_int(
                errors,
                f"{report_label}.artifacts[{index}] terminal manifest accepted support refs",
                len(support_refs),
                expected_claims,
            )
        if expected_points == 1000:
            _require_int(
                errors,
                f"{report_label}.artifacts[{index}] terminal manifest packet completion points",
                payload.get("packet_completion_points"),
                expected_points,
            )
            if payload.get("terminal_state") != "complete_1000_of_1000":
                errors.append(f"{report_label}.artifacts[{index}] terminal manifest terminal_state is not complete_1000_of_1000: {raw_ref}")
            for list_key in ("remaining_blockers", "blocked_lanes"):
                value = payload.get(list_key)
                if value != []:
                    errors.append(f"{report_label}.artifacts[{index}] terminal manifest {list_key} must be empty at 1000/1000: {raw_ref}")
            non_target_claims = payload.get("accepted_non_target_claims")
            if not isinstance(non_target_claims, list):
                errors.append(f"{report_label}.artifacts[{index}] terminal manifest accepted_non_target_claims must be a list: {raw_ref}")
            else:
                _require_int(
                    errors,
                    f"{report_label}.artifacts[{index}] terminal manifest accepted non-target claims",
                    len(non_target_claims),
                    _inventory_non_target_claim_count(),
                )
            validation_ref = payload.get("accepted_validation_ref")
            if not isinstance(validation_ref, str) or not validation_ref:
                errors.append(f"{report_label}.artifacts[{index}] terminal manifest missing accepted_validation_ref: {raw_ref}")
            else:
                expected_hash = _ref_hash(validation_ref)
                if expected_hash is None:
                    errors.append(f"{report_label}.artifacts[{index}] terminal manifest accepted_validation_ref missing sha256: {raw_ref}")
                else:
                    validation_path = resolve_artifact_ref(workspace_root, implementation_checkout, _strip_ref_suffix(validation_ref))
                    if not validation_path.exists():
                        errors.append(f"{report_label}.artifacts[{index}] terminal manifest accepted_validation_ref path missing: {_strip_ref_suffix(validation_ref)}")
                    else:
                        actual_hash = sha256_path(validation_path)
                        if actual_hash != expected_hash:
                            errors.append(
                                f"{report_label}.artifacts[{index}] terminal manifest stale accepted_validation_ref: "
                                f"expected {expected_hash}, got {actual_hash}: {raw_ref}"
                            )
def _validate_current_artifacts(
    *,
    errors: list[str],
    report_label: str,
    report_path: Path,
    artifacts: Iterable[Mapping[str, Any]],
    workspace_root: Path,
    implementation_checkout: Path,
    expected_claims: int,
    expected_points: int,
    historical_paths: set[Path],
    catalog: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    report_resolved = report_path.resolve()
    for index, artifact in enumerate(artifacts, start=1):
        raw_ref = artifact.get("path")
        if not isinstance(raw_ref, str) or not raw_ref:
            errors.append(f"{report_label}.artifacts[{index}].path missing or not a string")
            continue

        resolved = resolve_artifact_ref(workspace_root, implementation_checkout, raw_ref)
        if resolved == report_resolved:
            errors.append(f"{report_label}.artifacts[{index}] self-references current report {raw_ref!r}")
        if resolved in historical_paths:
            errors.append(f"{report_label}.artifacts[{index}] current artifact is listed as historical input: {raw_ref}")
        if not resolved.exists():
            errors.append(f"{report_label}.artifacts[{index}] path does not exist: {raw_ref}")
            continue

        actual_hash = sha256_path(resolved)
        expected_hash = artifact.get("sha256")
        catalog_hash = _catalog_hash_for_path(catalog, raw_ref)
        if catalog is not None and catalog_hash is not None and actual_hash != catalog_hash:
            errors.append(
                f"{report_label}.artifacts[{index}] catalog hash mismatch for {raw_ref}: "
                f"catalog {catalog_hash}, got {actual_hash}"
            )
        if expected_hash is not None and expected_hash != actual_hash:
            errors.append(
                f"{report_label}.artifacts[{index}] hash mismatch for {raw_ref}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        expected_bytes = artifact.get("bytes")
        actual_bytes = resolved.stat().st_size
        if expected_bytes is not None and expected_bytes != actual_bytes:
            errors.append(
                f"{report_label}.artifacts[{index}] byte mismatch for {raw_ref}: "
                f"expected {expected_bytes}, got {actual_bytes}"
            )

        json_payload = _try_load_json(resolved)
        if json_payload is not None:
            _validate_json_current_payload(
                errors=errors,
                report_label=report_label,
                index=index,
                raw_ref=raw_ref,
                payload=json_payload,
                expected_claims=expected_claims,
                expected_points=expected_points,
                workspace_root=workspace_root,
                implementation_checkout=implementation_checkout,
            )

        checked.append({"path": raw_ref, "resolved": str(resolved), "sha256": actual_hash, "bytes": actual_bytes})
    return checked

def _validate_live_validator_refs(
    *,
    errors: list[str],
    accepted_validation: Mapping[str, Any],
    workspace_root: Path,
    implementation_checkout: Path,
    catalog: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    refs = accepted_validation.get("refs")
    if not isinstance(refs, Mapping):
        errors.append("accepted_validation.refs must be an object with live validator refs")
        return []

    checked: list[dict[str, Any]] = []
    for key, expected_path, catalog_ref in _required_validator_refs_from_inventory(refs, catalog):
        raw_ref = refs.get(key)
        if not isinstance(raw_ref, str) or not raw_ref:
            errors.append(f"accepted_validation.refs.{key}: missing live validator ref for inventory output {expected_path}")
            continue
        raw_path = _strip_ref_suffix(raw_ref)
        if raw_path != expected_path:
            errors.append(f"accepted_validation.refs.{key}: expected inventory output {expected_path}, got {raw_path}")
        if "artifacts/conformance/node_gate/" not in raw_path:
            errors.append(f"accepted_validation.refs.{key}: must point at artifacts/conformance/node_gate: {raw_ref}")
        expected_hash = _ref_hash(raw_ref)
        catalog_hash = _ref_hash(catalog_ref) if isinstance(catalog_ref, str) else None
        if expected_hash is None:
            errors.append(f"accepted_validation.refs.{key}: missing sha256 hash")
        if catalog_hash is not None and expected_hash is not None and expected_hash != catalog_hash:
            errors.append(
                f"accepted_validation.refs.{key}: catalog hash mismatch for {raw_path}: "
                f"catalog {catalog_hash}, ref {expected_hash}"
            )
        resolved = resolve_artifact_ref(workspace_root, implementation_checkout, raw_path)
        if not resolved.exists():
            errors.append(f"accepted_validation.refs.{key}: missing path: {raw_path}")
            continue
        actual_hash = sha256_path(resolved)
        if expected_hash is not None and actual_hash != expected_hash:
            errors.append(
                f"accepted_validation.refs.{key}: hash mismatch for {raw_path}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        payload = _try_load_json(resolved)
        if payload is None:
            errors.append(f"accepted_validation.refs.{key}: live validator output must be JSON")
        else:
            if payload.get("schema_version") != _LIVE_VALIDATOR_SCHEMA_VERSION:
                errors.append(
                    f"accepted_validation.refs.{key}: schema_version must be {_LIVE_VALIDATOR_SCHEMA_VERSION}"
                )
            if payload.get("ok") is not True:
                errors.append(f"accepted_validation.refs.{key}: live validator output must have ok=true")
            embedded_refs = payload.get("refs")
            embedded_hashes = payload.get("hashes")
            if not isinstance(embedded_refs, Mapping):
                errors.append(f"accepted_validation.refs.{key}: live validator output refs must be an object")
                embedded_refs = {}
            if not isinstance(embedded_hashes, Mapping):
                errors.append(f"accepted_validation.refs.{key}: live validator output hashes must be an object")
                embedded_hashes = {}
            for embedded_key in ("support_claim", "evidence_manifest"):
                embedded_ref = payload.get(embedded_key) or embedded_refs.get(embedded_key)
                expected_embedded_hash = embedded_hashes.get(embedded_key)
                if not isinstance(embedded_ref, str) or not embedded_ref:
                    errors.append(f"accepted_validation.refs.{key}: live validator output missing refs.{embedded_key}")
                    continue
                if not isinstance(expected_embedded_hash, str) or not expected_embedded_hash.startswith("sha256:"):
                    errors.append(f"accepted_validation.refs.{key}: live validator output missing hashes.{embedded_key}")
                    continue
                embedded_path = resolve_artifact_ref(workspace_root, implementation_checkout, embedded_ref)
                if not embedded_path.exists():
                    errors.append(
                        f"accepted_validation.refs.{key}: live validator output {embedded_key} path missing: {embedded_ref}"
                    )
                    continue
                actual_embedded_hash = sha256_path(embedded_path)
                if actual_embedded_hash != expected_embedded_hash:
                    errors.append(
                        f"accepted_validation.refs.{key}: live validator output stale {embedded_key} hash: "
                        f"expected {expected_embedded_hash}, got {actual_embedded_hash}"
                    )
        checked.append({"key": key, "path": raw_path, "resolved": str(resolved), "sha256": actual_hash})
    return checked



def _validate_historical_report(
    *,
    errors: list[str],
    path: Path,
    payload: Mapping[str, Any],
    expected_claims: int,
    expected_points: int,
) -> dict[str, Any]:
    # A supplied historical path is explicitly quarantined by the caller. It may be
    # superseded, carry old counts, or be a legacy hash report without score fields.
    return {
        "path": str(path),
        "superseded_by": payload.get("superseded_by"),
        "terminal_state": payload.get("terminal_state"),
        "target_support_claims_accepted": _support_claim_count(payload),
        "score": _accepted_points(payload),
        "historical": True,
    }


def collect_scorecard_artifact_freshness_errors(
    *,
    scorecard_path: Path | str,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    implementation_checkout: Path | str | None = None,
    implementation_checkout_prefix: str | None = None,
    expected_points: int = 615,
    expected_claims: int = 4,
    historical_report_paths: Sequence[Path | str] = (),
) -> tuple[list[str], dict[str, Any]]:
    workspace = _as_path(workspace_root)
    if implementation_checkout is None:
        checkout_name = implementation_checkout_prefix or ROOT.name
        implementation = (workspace / checkout_name).resolve()
    else:
        implementation = _as_path(implementation_checkout)
    scorecard_file = _as_path(scorecard_path)
    scorecard = load_json(scorecard_file)
    if not isinstance(scorecard, Mapping):
        raise ValueError(f"scorecard is not a JSON object: {scorecard_file}")

    errors: list[str] = []
    scorecard_points = _accepted_points(scorecard)
    if scorecard_points is not None:
        _require_int(errors, "scorecard accepted point count", scorecard_points, expected_points)
    scorecard_claims = _support_claim_count(scorecard)
    if scorecard_claims is not None:
        _require_int(errors, "scorecard support claim count", scorecard_claims, expected_claims)

    historical_paths = {_as_path(path) for path in historical_report_paths}
    current_artifacts = _validate_current_artifacts(
        errors=errors,
        report_label="scorecard",
        report_path=scorecard_file,
        artifacts=_artifact_entries(scorecard),
        workspace_root=workspace,
        implementation_checkout=implementation,
        expected_claims=expected_claims,
        expected_points=expected_points,
        historical_paths=historical_paths,
    )

    historical: list[dict[str, Any]] = []
    for historical_path in sorted(historical_paths):
        payload = load_json(historical_path)
        if not isinstance(payload, Mapping):
            errors.append(f"historical report is not a JSON object: {historical_path}")
            continue
        historical.append(
            _validate_historical_report(
                errors=errors,
                path=historical_path,
                payload=payload,
                expected_claims=expected_claims,
                expected_points=expected_points,
            )
        )

    report = {
        "ok": not errors,
        "errors": errors,
        "expected_points": expected_points,
        "expected_claims": expected_claims,
        "current_artifacts": current_artifacts,
        "historical_artifacts_quarantined": historical,
        "workspace_root": str(workspace),
        "implementation_checkout": str(implementation),
    }
    return errors, report


def collect_report_hash_freshness_errors(
    *,
    workspace_root: Path | str = DEFAULT_WORKSPACE_ROOT,
    implementation_checkout: Path | str = ROOT,
    scorecard_path: Path | str,
    baseline_path: Path | str,
    progress_path: Path | str,
    accepted_report_path: Path | str,
    accepted_validation_path: Path | str,
    terminal_manifest_path: Path | str,
    expected_points: int = 615,
    expected_claims: int = 4,
    historical_report_paths: Sequence[Path | str] = (),
    catalog_path: Path | str | None = None,
    blame: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    workspace = _as_path(workspace_root)
    implementation = _as_path(implementation_checkout)
    scorecard_file = _as_path(scorecard_path)
    baseline_file = _as_path(baseline_path)
    progress_file = _as_path(progress_path)
    accepted_report_file = _as_path(accepted_report_path)
    accepted_validation_file = _as_path(accepted_validation_path)
    terminal_manifest_file = _as_path(terminal_manifest_path)
    catalog = catalog_refs.load_catalog(catalog_path) if catalog_path is not None else None

    errors: list[str] = []
    scorecard = load_json(scorecard_file)
    baseline = load_json(baseline_file)
    progress = load_json(progress_file)
    accepted_report = load_json(accepted_report_file)
    accepted_validation = load_json(accepted_validation_file)
    terminal_manifest = load_json(terminal_manifest_file)
    if not all(isinstance(payload, Mapping) for payload in (scorecard, baseline, progress, accepted_report, accepted_validation, terminal_manifest)):
        raise ValueError("all report inputs must be JSON objects")

    _require_int(errors, "scorecard.current_accepted_points", _accepted_points(scorecard), expected_points)
    _require_int(errors, "baseline.reconstructed_score.total", _accepted_points(baseline), expected_points)
    _require_int(errors, "baseline.target_support_claims_accepted", _support_claim_count(baseline), expected_claims)
    _require_int(errors, "progress.current_state.accepted_points", _accepted_points(progress), expected_points)
    _require_int(errors, "progress.target_support_claims_after", _support_claim_count(progress), expected_claims)
    _require_int(errors, "accepted_report.current_accepted_points", _accepted_points(accepted_report), expected_points)
    _require_int(errors, "accepted_report.accepted_support_claims", _support_claim_count(accepted_report), expected_claims)
    _require_int(errors, "terminal_manifest.target_support_claims_accepted", _support_claim_count(terminal_manifest), expected_claims)
    _require_int(errors, "terminal_manifest.current_accepted_points", _accepted_points(terminal_manifest), expected_points)
    if accepted_validation.get("ok") is not True:
        errors.append("accepted validation report must have ok=true")
    live_validator_refs = _validate_live_validator_refs(
        errors=errors,
        accepted_validation=accepted_validation,
        workspace_root=workspace,
        implementation_checkout=implementation,
        catalog=catalog,
    )


    historical_paths = {_as_path(path) for path in historical_report_paths}
    current_artifacts: list[dict[str, Any]] = []
    for label, path, payload in (
        ("scorecard", scorecard_file, scorecard),
        ("accepted_report", accepted_report_file, accepted_report),
        ("terminal_manifest", terminal_manifest_file, terminal_manifest),
    ):
        current_artifacts.extend(
            _validate_current_artifacts(
                errors=errors,
                report_label=label,
                report_path=path,
                artifacts=_artifact_entries(payload),
                workspace_root=workspace,
                implementation_checkout=implementation,
                expected_claims=expected_claims,
                expected_points=expected_points,
                historical_paths=historical_paths,
                catalog=catalog,
            )
        )

    historical: list[dict[str, Any]] = []
    progress_hash_report = progress.get("artifact_hash_report")
    if isinstance(progress_hash_report, str) and progress_hash_report:
        resolved_progress_hash = resolve_artifact_ref(workspace, implementation, progress_hash_report)
        if resolved_progress_hash != terminal_manifest_file and resolved_progress_hash not in historical_paths:
            errors.append(
                "progress.artifact_hash_report points at a non-current report that was not supplied as historical: "
                f"{progress_hash_report}"
            )
    for historical_path in sorted(historical_paths):
        payload = load_json(historical_path)
        if not isinstance(payload, Mapping):
            errors.append(f"historical report is not a JSON object: {historical_path}")
            continue
        historical.append(
            _validate_historical_report(
                errors=errors,
                path=historical_path,
                payload=payload,
                expected_claims=expected_claims,
                expected_points=expected_points,
            )
        )

    report = {
        "ok": not errors,
        "errors": errors,
        "expected_points": expected_points,
        "expected_claims": expected_claims,
        "current_artifacts": current_artifacts,
        "historical_artifacts_quarantined": historical,
        "live_validator_refs": live_validator_refs,
        "workspace_root": str(workspace),
        "implementation_checkout": str(implementation),
        "catalog": str(Path(catalog_path).resolve()) if catalog_path is not None else None,
    }
    return errors, apply_gate_error_envelope(
        report,
        "report_hash_freshness",
        blame=_catalog_blame_entries(catalog, workspace_root=workspace, implementation_checkout=implementation) if blame else (),
    )


def validate_report_hash_freshness(**kwargs: Any) -> dict[str, Any]:
    scorecard_only_keys = {"implementation_checkout_prefix", "expected_accepted_point_count", "expected_accepted_support_claim_count"}
    if scorecard_only_keys.intersection(kwargs) or "baseline_path" not in kwargs:
        expected_points = kwargs.pop("expected_points", kwargs.pop("expected_accepted_point_count", 615))
        expected_claims = kwargs.pop("expected_claims", kwargs.pop("expected_accepted_support_claim_count", 4))
        kwargs.pop("catalog_path", None)
        _, report = collect_scorecard_artifact_freshness_errors(
            scorecard_path=kwargs.pop("scorecard_path"),
            workspace_root=kwargs.pop("workspace_root", DEFAULT_WORKSPACE_ROOT),
            implementation_checkout=kwargs.pop("implementation_checkout", None),
            implementation_checkout_prefix=kwargs.pop("implementation_checkout_prefix", None),
            expected_points=expected_points,
            expected_claims=expected_claims,
            historical_report_paths=kwargs.pop("historical_report_paths", ()),
        )
        if kwargs:
            raise TypeError(f"unexpected validate_report_hash_freshness arguments: {sorted(kwargs)}")
        return report
    return collect_report_hash_freshness_errors(**kwargs)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate current E4 report/hash freshness before new scored lanes.")
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--implementation-checkout", default=str(ROOT))
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--progress", required=True)
    parser.add_argument("--accepted-report", required=True)
    parser.add_argument("--accepted-validation", required=True)
    parser.add_argument("--terminal-manifest", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--historical-report", action="append", default=[])
    parser.add_argument("--expected-points", type=int, default=615)
    parser.add_argument("--expected-claims", type=int, default=4)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--blame", action="store_true", help="Attach drifted stable catalog role_ids to catalog hash errors")
    args = parser.parse_args(argv)

    _, report = collect_report_hash_freshness_errors(
        workspace_root=args.workspace_root,
        implementation_checkout=args.implementation_checkout,
        scorecard_path=args.scorecard,
        baseline_path=args.baseline,
        progress_path=args.progress,
        accepted_report_path=args.accepted_report,
        accepted_validation_path=args.accepted_validation,
        terminal_manifest_path=args.terminal_manifest,
        expected_points=args.expected_points,
        expected_claims=args.expected_claims,
        historical_report_paths=args.historical_report,
        catalog_path=args.catalog,
        blame=args.blame,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print("ok")
    else:
        for error in report["errors"]:
            print(error, file=sys.stderr)
    return gate_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
