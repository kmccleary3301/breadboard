from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity.validate_e4_report_hash_freshness import (
    _accepted_inventory_lanes,
    _lane_ct_output,
    _validator_ref_key,
    validate_report_hash_freshness,
)


CHECKOUT_PREFIX = "breadboard_repo_integration_main_20260326"
REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE15_SCORECARD = REPO_ROOT.parent / "docs_tmp" / "phase_15" / "BB_E4_PRIMITIVE_PARITY_SCORECARD.json"
INVENTORY_LANES = tuple(_accepted_inventory_lanes())
TARGET_SUPPORT_LANES = tuple(lane for lane in INVENTORY_LANES if lane.get("kind") == "target_support")
EXPECTED_ACCEPTED_POINTS = int(json.loads(PHASE15_SCORECARD.read_text(encoding="utf-8"))["total_points"])
EXPECTED_ACCEPTED_SUPPORT_CLAIMS = len(TARGET_SUPPORT_LANES)
LIVE_VALIDATOR_REPORTS = {
    _validator_ref_key(lane): Path(_lane_ct_output(lane)).name
    for lane in INVENTORY_LANES
}
VALIDATOR_REF_KEYS = tuple(LIVE_VALIDATOR_REPORTS)
CATALOG_STALE_LANE = INVENTORY_LANES[0]
CATALOG_STALE_ROLE_ID = f"{CATALOG_STALE_LANE['lane_id']}:node_gate"
CATALOG_STALE_REF_KEY = _validator_ref_key(CATALOG_STALE_LANE)
MISSING_REF_KEY = VALIDATOR_REF_KEYS[len(VALIDATOR_REF_KEYS) // 2]
NOT_OK_REF_KEY = VALIDATOR_REF_KEYS[1]
STALE_REPORT_REF_KEY = VALIDATOR_REF_KEYS[0]
STALE_EMBEDDED_REF_KEY = VALIDATOR_REF_KEYS[2]


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _baseline_payload(
    *,
    accepted_points: int = EXPECTED_ACCEPTED_POINTS,
    accepted_support_claims: int = EXPECTED_ACCEPTED_SUPPORT_CLAIMS,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "bb.e4.baseline_report.v1",
        "report_kind": "baseline",
        "reconstructed_score": {"total": accepted_points},
        "target_support_claims_accepted": accepted_support_claims,
    }
    if superseded_by is not None:
        payload["superseded_by"] = superseded_by
    return payload


def _progress_payload(
    *,
    accepted_points: int = EXPECTED_ACCEPTED_POINTS,
    accepted_support_claims: int = EXPECTED_ACCEPTED_SUPPORT_CLAIMS,
    artifact_hash_report: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "bb.e4.progress_report.v1",
        "report_kind": "progress",
        "current_state": {"accepted_points": accepted_points},
        "target_support_claims_after": accepted_support_claims,
    }
    if artifact_hash_report is not None:
        payload["artifact_hash_report"] = artifact_hash_report
    return payload


def _accepted_report_payload(
    *,
    artifacts: list[dict[str, Any]] | None = None,
    accepted_points: int = EXPECTED_ACCEPTED_POINTS,
    accepted_support_claims: int = EXPECTED_ACCEPTED_SUPPORT_CLAIMS,
) -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.accepted_report.v1",
        "current_accepted_points": accepted_points,
        "accepted_support_claims": [
            {"id": f"support_claim_{index}"} for index in range(1, accepted_support_claims + 1)
        ],
        "artifacts": artifacts or [],
    }


def _terminal_manifest_payload(
    *,
    artifacts: list[dict[str, Any]] | None = None,
    accepted_points: int = EXPECTED_ACCEPTED_POINTS,
    accepted_support_claims: int = EXPECTED_ACCEPTED_SUPPORT_CLAIMS,
) -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.terminal_manifest.v1",
        "current_accepted_points": accepted_points,
        "target_support_claims_accepted": accepted_support_claims,
        "artifacts": artifacts or [],
    }


def _artifact(path: Path, checkout: Path, *, role: str, path_override: str | None = None) -> dict[str, str]:
    return {
        "role": role,
        "path": path_override or _relative(path, checkout),
        "sha256": _sha256_file(path),
    }


def _validator_report_ref(path: Path, checkout: Path) -> str:
    return f"{_relative(path, checkout)}#{_sha256_file(path)}"

def _primitive_ref(
    path: Path,
    checkout: Path,
    *,
    hash_override: str | None = None,
    include_hash: bool = True,
) -> str:
    ref = _relative(path, checkout)
    if not include_hash:
        return ref
    return f"{ref}#{hash_override or _sha256_file(path)}"


def _write_primitive_readiness_artifact(
    paths: dict[str, Any],
    *,
    ok: bool = True,
    ref_hash_overrides: dict[str, str] | None = None,
    refs_without_hash: set[str] | None = None,
) -> Path:
    checkout = paths["checkout"]
    readiness_dir = checkout / "docs" / "conformance" / "primitive_readiness"
    ledger = readiness_dir / "primitive_family_ledger.json"
    accepted_report = readiness_dir / "primitive_family_accepted_report.json"
    readiness_report = readiness_dir / "primitive_family_readiness_report.json"
    overrides = ref_hash_overrides or {}
    unhashed_refs = refs_without_hash or set()

    _write_json(
        ledger,
        {
            "schema_version": "bb.e4.primitive_family_ledger.v1",
            "families": [{"id": "shell", "primitive_count": 1}],
        },
    )
    _write_json(accepted_report, _accepted_report_payload())
    _write_json(
        readiness_report,
        {
            "schema_version": "bb.e4.primitive_family_readiness_report.v1",
            "ok": ok,
            "ledger_ref": _primitive_ref(
                ledger,
                checkout,
                hash_override=overrides.get("ledger_ref"),
                include_hash="ledger_ref" not in unhashed_refs,
            ),
            "accepted_report_ref": _primitive_ref(
                accepted_report,
                checkout,
                hash_override=overrides.get("accepted_report_ref"),
                include_hash="accepted_report_ref" not in unhashed_refs,
            ),
            "families": [{"id": "shell", "ready": ok}],
        },
    )
    return readiness_report


def _append_current_artifact(paths: dict[str, Any], report_key: str, artifact_path: Path) -> None:
    artifact = _artifact(artifact_path, paths["checkout"], role="primitive_readiness_report")
    if report_key == "scorecard":
        _rewrite_scorecard(paths, artifacts=[*paths["scorecard_payload"]["artifacts"], artifact])
        return

    payload = json.loads(paths[report_key].read_text(encoding="utf-8"))
    payload["artifacts"] = [*payload.get("artifacts", []), artifact]
    _write_json(paths[report_key], payload)
    _refresh_scorecard_hash(paths, report_key)


def _support_claim_payload(ref_key: str, evidence_manifest: Path, checkout: Path) -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.support_claim.v1",
        "claim_id": f"{ref_key}_support_claim",
        "accepted": True,
        "evidence_manifest_ref": _relative(evidence_manifest, checkout),
        "scope": {
            "config_id": ref_key,
            "lane_id": ref_key,
            "provider_model": "synthetic-model",
            "run_id": "synthetic-current-run",
            "sandbox_mode": "read-only",
            "target_version": "synthetic-target",
        },
        "ledger_row_refs": [
            "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#synthetic_feature#sha256:" + "1" * 64
        ],
        "validation_refs": [
            f"docs/conformance/e4_target_support/{ref_key}/packet_c4_validation_report.json#sha256:" + "2" * 64
        ],
    }


def _evidence_manifest_payload(ref_key: str) -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.evidence_manifest.v1",
        "claim_id": f"{ref_key}_support_claim",
        "config_id": ref_key,
        "artifacts": [],
    }


def _write_support_artifacts(checkout: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    support_claims: dict[str, Path] = {}
    evidence_manifests: dict[str, Path] = {}
    support_claim_dir = checkout / "docs" / "conformance" / "support_claims"
    for ref_key in LIVE_VALIDATOR_REPORTS:
        evidence_manifest = support_claim_dir / f"{ref_key}_evidence_manifest.json"
        support_claim = support_claim_dir / f"{ref_key}_support_claim.json"
        _write_json(evidence_manifest, _evidence_manifest_payload(ref_key))
        _write_json(support_claim, _support_claim_payload(ref_key, evidence_manifest, checkout))
        support_claims[ref_key] = support_claim
        evidence_manifests[ref_key] = evidence_manifest
    return support_claims, evidence_manifests


def _validator_report_payload(
    ref_key: str,
    *,
    support_claim: Path,
    evidence_manifest: Path,
    checkout: Path,
    ok: bool = True,
    hash_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    hashes = {
        "support_claim": _sha256_file(support_claim),
        "evidence_manifest": _sha256_file(evidence_manifest),
    }
    hashes.update(hash_overrides or {})
    return {
        "schema_version": "bb.e4.c4_chain_validation_report.v1",
        "ok": ok,
        "accepted": ok,
        "errors": [] if ok else [f"{ref_key} failed"],
        "config_id": ref_key,
        "refs": {
            "support_claim": _relative(support_claim, checkout),
            "evidence_manifest": _relative(evidence_manifest, checkout),
        },
        "hashes": hashes,
    }


def _write_live_validator_reports(
    paths: dict[str, Any],
    *,
    ok_overrides: dict[str, bool] | None = None,
    hash_overrides: dict[str, dict[str, str]] | None = None,
) -> None:
    refs: dict[str, str] = {}
    node_gate = paths["checkout"] / "artifacts" / "conformance" / "node_gate"
    ok_by_ref = ok_overrides or {}
    hash_by_ref = hash_overrides or {}
    for ref_key, filename in LIVE_VALIDATOR_REPORTS.items():
        report = node_gate / filename
        _write_json(
            report,
            _validator_report_payload(
                ref_key,
                support_claim=paths["support_claims"][ref_key],
                evidence_manifest=paths["evidence_manifests"][ref_key],
                checkout=paths["checkout"],
                ok=ok_by_ref.get(ref_key, True),
                hash_overrides=hash_by_ref.get(ref_key),
            ),
        )
        refs[ref_key] = _validator_report_ref(report, paths["checkout"])
    _write_json(paths["accepted_validation"], {"schema_version": "bb.e4.accepted_validation.v1", "ok": True, "refs": refs})
    _refresh_scorecard_hash(paths, "accepted_validation")

def _build_current_report_set(tmp_path: Path) -> dict[str, Any]:
    workspace_root = tmp_path
    checkout = workspace_root / CHECKOUT_PREFIX
    artifact_dir = checkout / "artifacts" / "e4_hash_freshness"
    manifest = checkout / "config" / "e4_target_freeze_manifest.yaml"
    baseline = artifact_dir / "current_baseline_report.json"
    progress = artifact_dir / "current_progress_report.json"
    accepted_report = artifact_dir / "current_accepted_report.json"
    accepted_validation = artifact_dir / "current_accepted_validation.json"
    terminal_manifest = artifact_dir / "current_terminal_manifest.json"
    scorecard = artifact_dir / "current_scorecard.json"

    _write_text(
        manifest,
        "e4_targets:\n"
        "  - id: codex_cli_gpt55_e4_capture_probe_v1\n"
        "    implementation_checkout: breadboard_repo_integration_main_20260326\n",
    )
    _write_json(baseline, _baseline_payload())
    _write_json(progress, _progress_payload())
    _write_json(accepted_validation, {"schema_version": "bb.e4.accepted_validation.v1", "ok": True})
    _write_json(accepted_report, _accepted_report_payload())
    _write_json(terminal_manifest, _terminal_manifest_payload())
    support_claims, evidence_manifests = _write_support_artifacts(checkout)
    scorecard_payload = {
        "schema_version": "bb.e4.report_hash_freshness_scorecard.v1",
        "current_accepted_points": EXPECTED_ACCEPTED_POINTS,
        "target_support_claims_accepted": EXPECTED_ACCEPTED_SUPPORT_CLAIMS,
        "artifacts": [
            _artifact(manifest, checkout, role="freeze_manifest"),
            _artifact(baseline, checkout, role="baseline_report"),
            _artifact(progress, checkout, role="progress_report"),
            _artifact(accepted_report, checkout, role="accepted_report"),
            _artifact(accepted_validation, checkout, role="accepted_validation"),
            _artifact(terminal_manifest, checkout, role="terminal_manifest"),
        ],
    }
    _write_json(scorecard, scorecard_payload)

    paths = {
        "workspace_root": workspace_root,
        "checkout": checkout,
        "manifest": manifest,
        "baseline": baseline,
        "progress": progress,
        "accepted_report": accepted_report,
        "accepted_validation": accepted_validation,
        "terminal_manifest": terminal_manifest,
        "scorecard": scorecard,
        "scorecard_payload": scorecard_payload,
        "support_claims": support_claims,
        "evidence_manifests": evidence_manifests,
    }
    _write_live_validator_reports(paths)
    return paths


def _rewrite_scorecard(paths: dict[str, Any], **updates: Any) -> None:
    payload = dict(paths["scorecard_payload"])
    payload.update(updates)
    paths["scorecard_payload"] = payload
    _write_json(paths["scorecard"], payload)


def _refresh_scorecard_hash(paths: dict[str, Any], artifact_key: str) -> None:
    role = f"{artifact_key}_report" if artifact_key in {"baseline", "progress"} else artifact_key
    artifacts = []
    for artifact in paths["scorecard_payload"]["artifacts"]:
        replacement = dict(artifact)
        if replacement["role"] == role:
            replacement["sha256"] = _sha256_file(paths[artifact_key])
        artifacts.append(replacement)
    _rewrite_scorecard(paths, artifacts=artifacts)


def _write_node_gate_catalog(paths: dict[str, Any], *, stale_role_id: str | None = None) -> Path:
    inventory = json.loads((REPO_ROOT / "docs/conformance/e4_lane_inventory.json").read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    for lane in inventory["lanes"]:
        if lane.get("status") != "accepted":
            continue
        argv = lane["ct"]["command"]["argv"]
        output = argv[argv.index("--json-out") + 1]
        role_id = f"{lane['lane_id']}:node_gate"
        artifact_path = paths["checkout"] / output
        digest = _sha256_file(artifact_path)
        if role_id == stale_role_id:
            digest = "sha256:" + "0" * 64
        entries.append(
            {
                "role_id": role_id,
                "path": output,
                "sha256": digest,
                "bytes": artifact_path.stat().st_size,
                "exists": artifact_path.exists(),
                "artifact_kind": "node_gate",
            }
        )
    catalog = paths["checkout"] / "docs" / "conformance" / "e4_artifact_catalog.json"
    _write_json(catalog, {"schema_version": "bb.e4.artifact_catalog.v1", "entries": entries})
    return catalog


def _call_validator(
    paths: dict[str, Any],
    *,
    historical_report_paths: list[Path] | None = None,
    catalog_path: Path | None = None,
    blame: bool = False,
) -> Any:
    return validate_report_hash_freshness(
        scorecard_path=paths["scorecard"],
        baseline_path=paths["baseline"],
        progress_path=paths["progress"],
        accepted_report_path=paths["accepted_report"],
        accepted_validation_path=paths["accepted_validation"],
        terminal_manifest_path=paths["terminal_manifest"],
        workspace_root=paths["workspace_root"],
        implementation_checkout=paths["checkout"],
        expected_points=EXPECTED_ACCEPTED_POINTS,
        expected_claims=EXPECTED_ACCEPTED_SUPPORT_CLAIMS,
        historical_report_paths=historical_report_paths or [],
        catalog_path=catalog_path,
        blame=blame,
    )


def _errors(result: Any) -> list[str]:
    if isinstance(result, dict):
        errors = result.get("errors", [])
        assert isinstance(errors, list)
        return [str(error) for error in errors]

    errors = getattr(result, "errors", [])
    assert isinstance(errors, list)
    return [str(error) for error in errors]


def _assert_valid(result: Any) -> None:
    if isinstance(result, dict) and "ok" in result:
        assert result["ok"] is True
    elif hasattr(result, "ok"):
        assert result.ok is True
    assert _errors(result) == []


def _assert_invalid(result: Any, *needles: str) -> None:
    errors = _errors(result)
    assert errors != []
    joined = "\n".join(errors).lower()
    for needle in needles:
        assert needle.lower() in joined


def test_live_node_gate_validator_refs_in_accepted_validation_pass(tmp_path: Path) -> None:
    """Accepted validation refs may point at current node_gate C4 reports when their report and embedded artifact hashes are fresh."""
    paths = _build_current_report_set(tmp_path)
    assert paths["scorecard_payload"]["artifacts"][0]["path"] == "config/e4_target_freeze_manifest.yaml"
    accepted_validation = json.loads(paths["accepted_validation"].read_text(encoding="utf-8"))
    assert set(accepted_validation["refs"]) == set(LIVE_VALIDATOR_REPORTS)
    for ref_key, ref in accepted_validation["refs"].items():
        report = paths["checkout"] / "artifacts" / "conformance" / "node_gate" / LIVE_VALIDATOR_REPORTS[ref_key]
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        support_claim = paths["support_claims"][ref_key]
        evidence_manifest = paths["evidence_manifests"][ref_key]
        support_claim_payload = json.loads(support_claim.read_text(encoding="utf-8"))
        assert ref == _validator_report_ref(report, paths["checkout"])
        assert report_payload["refs"] == {
            "support_claim": _relative(support_claim, paths["checkout"]),
            "evidence_manifest": _relative(evidence_manifest, paths["checkout"]),
        }
        assert report_payload["hashes"] == {
            "support_claim": _sha256_file(support_claim),
            "evidence_manifest": _sha256_file(evidence_manifest),
        }
        assert all("artifacts/conformance/node_gate/" not in ref for ref in support_claim_payload["validation_refs"])

    result = _call_validator(paths)

    _assert_valid(result)

def test_catalog_backed_live_validator_refs_pass(tmp_path: Path) -> None:
    """C.7: live validator refs may be checked against inventory-derived node_gate catalog roles."""
    paths = _build_current_report_set(tmp_path)
    catalog = _write_node_gate_catalog(paths)

    result = _call_validator(paths, catalog_path=catalog)

    _assert_valid(result)
    assert result["catalog"] == str(catalog.resolve())


def test_catalog_backed_live_validator_stale_hash_fails(tmp_path: Path) -> None:
    """C.7: stale catalog hash refs fail with a named catalog mismatch instead of relying on static registries."""
    paths = _build_current_report_set(tmp_path)
    catalog = _write_node_gate_catalog(paths, stale_role_id=CATALOG_STALE_ROLE_ID)

    result = _call_validator(paths, catalog_path=catalog)

    _assert_invalid(result, "catalog hash mismatch", CATALOG_STALE_REF_KEY)


def test_catalog_drift_blame_names_only_mutated_stable_artifact(tmp_path: Path) -> None:
    """A catalog hash mismatch attributes drift to the stable artifact whose pinned bytes changed."""
    paths = _build_current_report_set(tmp_path)
    capture_path = paths["checkout"] / "docs" / "conformance" / "e4_target_support" / "lane_alpha" / "raw_capture_manifest.json"
    replay_path = paths["checkout"] / "docs" / "conformance" / "e4_target_support" / "lane_alpha" / "bb_replay_result.json"
    _write_json(capture_path, {"schema_version": "bb.e4.raw_capture_manifest.v1", "capture": "before"})
    _write_json(replay_path, {"schema_version": "bb.e4.replay_result.v1", "replay": "unchanged"})
    capture_ref = _relative(capture_path, paths["checkout"])
    replay_ref = _relative(replay_path, paths["checkout"])
    previous_capture_sha = _sha256_file(capture_path)
    replay_sha = _sha256_file(replay_path)
    catalog = paths["checkout"] / "docs" / "conformance" / "e4_artifact_catalog.json"
    _write_json(
        catalog,
        {
            "schema_version": "bb.e4.artifact_catalog.v1",
            "entries": [
                {
                    "role_id": "lane_alpha:capture",
                    "path": capture_ref,
                    "sha256": previous_capture_sha,
                    "bytes": capture_path.stat().st_size,
                    "exists": True,
                    "artifact_kind": "capture",
                },
                {
                    "role_id": "lane_alpha:replay",
                    "path": replay_ref,
                    "sha256": replay_sha,
                    "bytes": replay_path.stat().st_size,
                    "exists": True,
                    "artifact_kind": "replay",
                },
            ],
        },
    )
    _write_json(capture_path, {"schema_version": "bb.e4.raw_capture_manifest.v1", "capture": "after"})
    current_capture_sha = _sha256_file(capture_path)
    _rewrite_scorecard(
        paths,
        artifacts=[
            *paths["scorecard_payload"]["artifacts"],
            {"role": "capture", "path": capture_ref, "sha256": current_capture_sha},
            {"role": "replay", "path": replay_ref, "sha256": replay_sha},
        ],
    )

    result = _call_validator(paths, catalog_path=catalog, blame=True)

    assert result["ok"] is False
    assert len(result["gate_errors"]) == 1
    [catalog_error] = result["gate_errors"]
    assert "catalog hash mismatch" in catalog_error["message"]
    assert catalog_error["blame"] == [
        {
            "role_id": "lane_alpha:capture",
            "path": capture_ref,
            "prev_sha256": previous_capture_sha,
            "cur_sha256": current_capture_sha,
        }
    ]

def test_missing_live_validator_ref_in_accepted_validation_fails(tmp_path: Path) -> None:
    """Every accepted validation report must retain the live C4 validator refs required for terminal acceptance."""
    paths = _build_current_report_set(tmp_path)
    accepted_validation = json.loads(paths["accepted_validation"].read_text(encoding="utf-8"))
    del accepted_validation["refs"][MISSING_REF_KEY]
    _write_json(paths["accepted_validation"], accepted_validation)
    _refresh_scorecard_hash(paths, "accepted_validation")

    result = _call_validator(paths)

    _assert_invalid(result, MISSING_REF_KEY)


def test_live_validator_ref_with_not_ok_report_fails(tmp_path: Path) -> None:
    """A current node_gate C4 report referenced by accepted validation must itself have ok=true."""
    paths = _build_current_report_set(tmp_path)
    _write_live_validator_reports(paths, ok_overrides={NOT_OK_REF_KEY: False})

    result = _call_validator(paths)

    _assert_invalid(result, NOT_OK_REF_KEY, "ok")


def test_live_validator_ref_with_stale_hash_fails(tmp_path: Path) -> None:
    """Accepted validation refs must bind each live node_gate C4 report to the bytes currently on disk."""
    paths = _build_current_report_set(tmp_path)
    stale_ref_key = STALE_REPORT_REF_KEY
    report = paths["checkout"] / "artifacts" / "conformance" / "node_gate" / LIVE_VALIDATOR_REPORTS[stale_ref_key]
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    _write_json(report, {**report_payload, "rerun": "newer-output"})

    result = _call_validator(paths)

    _assert_invalid(result, stale_ref_key, "hash")


@pytest.mark.parametrize("embedded_key", ["support_claim", "evidence_manifest"])
def test_live_validator_ref_with_stale_embedded_artifact_hash_fails(tmp_path: Path, embedded_key: str) -> None:
    """A fresh node_gate report ref is still invalid when it embeds a stale support-claim or evidence-manifest hash."""
    paths = _build_current_report_set(tmp_path)
    stale_ref_key = STALE_EMBEDDED_REF_KEY
    report = paths["checkout"] / "artifacts" / "conformance" / "node_gate" / LIVE_VALIDATOR_REPORTS[stale_ref_key]
    _write_live_validator_reports(
        paths,
        hash_overrides={stale_ref_key: {embedded_key: "sha256:" + "f" * 64}},
    )
    accepted_validation = json.loads(paths["accepted_validation"].read_text(encoding="utf-8"))
    assert accepted_validation["refs"][stale_ref_key] == _validator_report_ref(report, paths["checkout"])

    result = _call_validator(paths)

    _assert_invalid(result, stale_ref_key, embedded_key, "stale")


def test_ordering_regression_mutating_claim_after_validator_render_fails_then_regenerates(
    tmp_path: Path,
) -> None:
    """Regression for 2026-07-03: C4 gates catch claims changed after validator refs were rendered."""
    paths = _build_current_report_set(tmp_path)
    stale_ref_key = STALE_EMBEDDED_REF_KEY
    support_claim = paths["support_claims"][stale_ref_key]
    support_claim_payload = json.loads(support_claim.read_text(encoding="utf-8"))
    support_claim_payload["scope"]["run_id"] = "mutated-after-validator-render"
    _write_json(support_claim, support_claim_payload)

    stale_result = _call_validator(paths)

    _assert_invalid(stale_result, stale_ref_key, "support_claim", "stale")

    _write_live_validator_reports(paths)
    regenerated_result = _call_validator(paths)

    _assert_valid(regenerated_result)


@pytest.mark.parametrize("report_key", ["scorecard", "accepted_report", "terminal_manifest"])
@pytest.mark.parametrize(
    ("ok", "ref_hash_overrides", "refs_without_hash", "needles"),
    [
        (False, None, None, ("primitive readiness", "ok=true")),
        (True, {"accepted_report_ref": "sha256:" + "a" * 64}, None, ("accepted_report_ref", "stale")),
        (True, {"ledger_ref": "sha256:" + "b" * 64}, None, ("ledger_ref", "stale")),
        (True, None, {"accepted_report_ref"}, ("accepted_report_ref", "missing sha256")),
        (True, None, {"ledger_ref"}, ("ledger_ref", "missing sha256")),
    ],
)
def test_primitive_readiness_current_artifact_semantics_fail(
    tmp_path: Path,
    report_key: str,
    ok: bool,
    ref_hash_overrides: dict[str, str] | None,
    refs_without_hash: set[str] | None,
    needles: tuple[str, ...],
) -> None:
    """Primitive-readiness artifacts in any current list must be ok and bind embedded refs to fresh hashes."""
    paths = _build_current_report_set(tmp_path)
    readiness_report = _write_primitive_readiness_artifact(
        paths,
        ok=ok,
        ref_hash_overrides=ref_hash_overrides,
        refs_without_hash=refs_without_hash,
    )
    _append_current_artifact(paths, report_key, readiness_report)

    result = _call_validator(paths)

    _assert_invalid(result, report_key, *needles)


def test_current_artifact_list_rejects_scorecard_self_reference(tmp_path: Path) -> None:
    """The scorecard cannot be a current artifact because that makes its own hash recursive."""
    paths = _build_current_report_set(tmp_path)
    artifacts = [*paths["scorecard_payload"]["artifacts"]]
    artifacts.append(
        {
            "role": "scorecard",
            "path": _relative(paths["scorecard"], paths["checkout"]),
            "sha256": "sha256:" + "0" * 64,
        }
    )
    _rewrite_scorecard(paths, artifacts=artifacts)

    result = _call_validator(paths)

    _assert_invalid(result, "self", "scorecard")


@pytest.mark.parametrize("artifact_key, role", [("baseline", "baseline_report"), ("progress", "progress_report")])
def test_stale_current_baseline_or_progress_hash_fails(tmp_path: Path, artifact_key: str, role: str) -> None:
    """Baseline and progress current artifacts must carry the hash of the bytes on disk now."""
    paths = _build_current_report_set(tmp_path)
    artifacts = []
    for artifact in paths["scorecard_payload"]["artifacts"]:
        replacement = dict(artifact)
        if replacement["role"] == role:
            replacement["sha256"] = "sha256:" + "f" * 64
        artifacts.append(replacement)
    _rewrite_scorecard(paths, artifacts=artifacts)

    result = _call_validator(paths)

    _assert_invalid(result, artifact_key, "hash")


def test_missing_manifest_current_artifact_fails(tmp_path: Path) -> None:
    """Repo-relative current artifact refs are allowed, but they must resolve under the checkout and exist."""
    paths = _build_current_report_set(tmp_path)
    artifacts = []
    for artifact in paths["scorecard_payload"]["artifacts"]:
        replacement = dict(artifact)
        if replacement["role"] == "freeze_manifest":
            replacement["path"] = "config/missing_e4_target_freeze_manifest.yaml"
        artifacts.append(replacement)
    _rewrite_scorecard(paths, artifacts=artifacts)

    result = _call_validator(paths)

    _assert_invalid(result, "config/missing_e4_target_freeze_manifest.yaml")


def test_superseded_or_old_count_reports_are_allowed_only_as_historical_inputs(tmp_path: Path) -> None:
    """Superseded reports and old accepted counts are quarantine inputs, never current artifacts."""
    paths = _build_current_report_set(tmp_path)
    historical = paths["checkout"] / "artifacts" / "e4_hash_freshness" / "historical_baseline_report.json"
    _write_json(
        historical,
        _baseline_payload(
            accepted_points=EXPECTED_ACCEPTED_POINTS - 1,
            accepted_support_claims=EXPECTED_ACCEPTED_SUPPORT_CLAIMS - 1,
            superseded_by=_relative(paths["scorecard"], paths["checkout"]),
        ),
    )

    _assert_valid(_call_validator(paths, historical_report_paths=[historical]))

    artifacts = [
        *paths["scorecard_payload"]["artifacts"],
        _artifact(historical, paths["checkout"], role="baseline_report"),
    ]
    _rewrite_scorecard(paths, artifacts=artifacts)

    result = _call_validator(paths, historical_report_paths=[historical])

    _assert_invalid(result, "superseded", "current")


@pytest.mark.parametrize(
    ("report_key", "payload", "expected_error"),
    [
        ("baseline", _baseline_payload(accepted_points=EXPECTED_ACCEPTED_POINTS - 1), "reconstructed_score.total"),
        ("progress", _progress_payload(accepted_support_claims=EXPECTED_ACCEPTED_SUPPORT_CLAIMS - 1), "target_support_claims_after"),
    ],
)
def test_accepted_point_or_support_claim_count_mismatch_fails(
    tmp_path: Path,
    report_key: str,
    payload: dict[str, Any],
    expected_error: str,
) -> None:
    """Fresh hashes are not enough: current reports must still claim the inventory-derived point and support-claim totals."""
    paths = _build_current_report_set(tmp_path)
    _write_json(paths[report_key], payload)
    _refresh_scorecard_hash(paths, report_key)

    result = _call_validator(paths)

    _assert_invalid(result, expected_error)
