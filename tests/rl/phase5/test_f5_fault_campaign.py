from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5.f5_fault_campaign import (
    ENUMERATED_FAULT_NON_CLAIM,
    FAULT_CLASSES,
    F5CampaignInput,
    F5CampaignManifest,
    F5FaultCampaignError,
    F5ValidationReport,
    author_f5_fault_campaign,
)

_EXPECTATIONS = {
    "timeout": ("TIMEOUT", "post-allocation"),
    "cancel": ("CANCELLED", "post-allocation"),
    "revocation": ("REVOKED", "pre-allocation"),
    "egress": ("EGRESS_DENIED", "post-allocation"),
    "resource": ("RESOURCE_EXHAUSTED", "post-allocation"),
    "verifier": ("VERIFIER_FAILED", "post-allocation"),
    "artifact": ("ARTIFACT_FAILED", "post-allocation"),
    "transport": ("TRANSPORT_FAILED", "pre-allocation"),
}


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _identity(kind: str, suffix: str) -> dict[str, str]:
    digest = _digest(f"{kind}:{suffix}")
    return {
        "identity_id": f"{kind}-{suffix}",
        "digest": digest,
        "immutable_ref": f"cas://phase5-f5/{kind}/{suffix}@{digest}",
    }


def _identities(suffix: str) -> dict[str, dict[str, str]]:
    return {
        kind: _identity(kind, suffix)
        for kind in (
            "authority",
            "effective_plan",
            "config",
            "task",
            "model",
            "image",
            "runtime",
            "verifier",
        )
    }


def _success_outcome() -> dict[str, Any]:
    return {
        "lifecycle": "succeeded",
        "error_class": None,
        "failure_boundary": "post-allocation",
        "reward": 1,
        "reward_quarantined": False,
        "lease_opened": True,
    }


def _failure_outcome(fault_class: str) -> dict[str, Any]:
    error_class, boundary = _EXPECTATIONS[fault_class]
    return {
        "lifecycle": "failed",
        "error_class": error_class,
        "failure_boundary": boundary,
        "reward": None,
        "reward_quarantined": True,
        "lease_opened": boundary == "post-allocation",
    }


def _row(
    row_id: str,
    identity_suffix: str,
    *,
    fault_class: str | None,
) -> dict[str, Any]:
    identities = _identities(identity_suffix)
    outcome = (
        _success_outcome()
        if fault_class is None
        else _failure_outcome(fault_class)
    )
    allocated = outcome["failure_boundary"] == "post-allocation"
    job_id = f"job-{row_id}"
    node_id = f"node-{row_id}"
    canary = f"canary-{row_id}"
    return {
        "row_id": row_id,
        "identities": identities,
        "workspace_id": f"workspace-{row_id}",
        "container_id": f"container-{row_id}",
        "canary": canary,
        "canary_reads": [canary],
        "target": {
            "attempt": {
                "attempt_id": f"attempt-{row_id}",
                "attempt_manifest": _identity("attempt-manifest", row_id),
                "status": "succeeded",
                "current": True,
                "superseded_by_attempt_id": None,
                "job_id": job_id,
                "node_id": node_id,
            },
            "episode": {
                "episode_id": f"episode-{row_id}",
                "output": _identity("episode-output", row_id),
                "state": "closed",
            },
            "evidence": _identity("evidence", row_id),
            "join": {
                "authority_digest": identities["authority"]["digest"],
                "effective_plan_digest": identities["effective_plan"]["digest"],
                "config_digest": identities["config"]["digest"],
                "task_digest": identities["task"]["digest"],
                "model_digest": identities["model"]["digest"],
                "image_digest": identities["image"]["digest"],
                "runtime_digest": identities["runtime"]["digest"],
                "verifier_digest": identities["verifier"]["digest"],
                "job_id": job_id,
                "node_id": node_id,
                "runtime_id": identities["runtime"]["identity_id"],
            },
            "fallback_used": False,
        },
        "expected": copy.deepcopy(outcome),
        "observed": {**copy.deepcopy(outcome), "unexpected_outcomes": []},
        "cleanup": {
            "authority": "breadboard_episode_service",
            "envelope_state": "closed",
            "cleanup_required": allocated,
            "cleanup_attempts": 1 if allocated else 0,
            "remaining_actors": 0,
            "remaining_processes": 0,
            "remaining_containers": 0,
            "remaining_cgroups": 0,
            "remaining_mounts": 0,
            "remaining_workspaces": 0,
            "remaining_secret_files": 0,
            "remaining_orphan_ids": [],
            "cleanup_error_classes": [],
        },
        "fault_injection": (
            None
            if fault_class is None
            else {
                "fault_class": fault_class,
                "injection_spec": _identity("fault-injection", fault_class),
            }
        ),
    }


def _payload() -> dict[str, Any]:
    pairs = []
    for fault_class in FAULT_CLASSES:
        identity_suffix = f"pair-{fault_class}"
        pairs.append(
            {
                "pair_id": f"pair-{fault_class}",
                "fault_class": fault_class,
                "fault": _row(
                    f"fault-{fault_class}",
                    identity_suffix,
                    fault_class=fault_class,
                ),
                "twin": _row(
                    f"twin-{fault_class}",
                    identity_suffix,
                    fault_class=None,
                ),
            }
        )
    return {
        "schema_version": "bb.rl.phase5-f5-fault-campaign-input.v1",
        "campaign_id": "f5-campaign-001",
        "fault_pairs": pairs,
        "concurrent_rows": [
            _row("concurrent-a", "concurrent-a", fault_class=None),
            _row("concurrent-b", "concurrent-b", fault_class=None),
            _row("concurrent-c", "concurrent-c", fault_class=None),
        ],
    }


def _validate(payload: dict[str, Any]) -> F5CampaignInput:
    return F5CampaignInput.model_validate_json(
        canonical_json_bytes(payload), strict=True
    )


def test_fault_twins_preserve_campaign_identity_with_distinct_episode_plans() -> None:
    payload = _payload()
    for pair in payload["fault_pairs"]:
        for role in ("fault", "twin"):
            row = pair[role]
            effective_plan = _identity("effective-plan", row["row_id"])
            row["identities"]["effective_plan"] = effective_plan
            row["target"]["join"]["effective_plan_digest"] = effective_plan["digest"]

    campaign = _validate(payload)

    assert len(campaign.fault_pairs) == len(FAULT_CLASSES) == 8
    assert all(
        pair.fault.identities.effective_plan
        != pair.twin.identities.effective_plan
        for pair in campaign.fault_pairs
    )


def test_same_config_is_valid_across_pairs_and_concurrent_controls() -> None:
    payload = _payload()
    shared_config = _identity("config", "shared-production-composition")
    rows = [
        row
        for pair in payload["fault_pairs"]
        for row in (pair["fault"], pair["twin"])
    ] + payload["concurrent_rows"]
    for row in rows:
        row["identities"]["config"] = copy.deepcopy(shared_config)
        row["target"]["join"]["config_digest"] = shared_config["digest"]

    campaign = _validate(payload)

    assert {
        row.identities.config.digest
        for pair in campaign.fault_pairs
        for row in (pair.fault, pair.twin)
    } == {shared_config["digest"]}
    assert {
        row.identities.config.digest for row in campaign.concurrent_rows
    } == {shared_config["digest"]}


def test_cross_pair_config_swap_rejects_broken_twin_join() -> None:
    payload = _payload()
    first_twin = payload["fault_pairs"][0]["twin"]
    second_config = payload["fault_pairs"][1]["twin"]["identities"]["config"]
    first_twin["identities"]["config"] = copy.deepcopy(second_config)
    first_twin["target"]["join"]["config_digest"] = second_config["digest"]

    with pytest.raises(
        ValidationError,
        match="no-fault twin must preserve every invariant campaign identity",
    ):
        _validate(payload)


@pytest.mark.parametrize(
    "identity_kind",
    ["authority", "config", "task", "model", "image", "runtime", "verifier"],
)
def test_fault_twin_rejects_invariant_campaign_identity_drift(
    identity_kind: str,
) -> None:
    payload = _payload()
    twin = payload["fault_pairs"][0]["twin"]
    changed = _identity(identity_kind, "cross-pair-stale")
    twin["identities"][identity_kind] = changed
    twin["target"]["join"][f"{identity_kind}_digest"] = changed["digest"]
    if identity_kind == "runtime":
        twin["target"]["join"]["runtime_id"] = changed["identity_id"]

    with pytest.raises(
        ValidationError,
        match="no-fault twin must preserve every invariant campaign identity",
    ):
        _validate(payload)


def test_complete_campaign_publishes_canonical_manifest_and_report_atomically(
    tmp_path: Path,
) -> None:
    payload = _payload()
    spec = _validate(payload)
    input_path = tmp_path / "f5-input.json"
    input_path.write_bytes(canonical_json_bytes(payload))
    output = tmp_path / "published-f5"

    artifacts = author_f5_fault_campaign(
        str(input_path.resolve()), str(output.resolve())
    )

    manifest_path = Path(artifacts.manifest_path)
    report_path = Path(artifacts.validation_report_path)
    assert sorted(path.name for path in output.iterdir()) == [
        "f5-campaign-manifest.json",
        "f5-validation-report.json",
    ]
    manifest_bytes = manifest_path.read_bytes()
    report_bytes = report_path.read_bytes()
    assert canonical_json_bytes(json.loads(manifest_bytes)) == manifest_bytes
    assert canonical_json_bytes(json.loads(report_bytes)) == report_bytes
    assert artifacts.manifest_digest == "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    assert artifacts.validation_report_digest == "sha256:" + hashlib.sha256(report_bytes).hexdigest()

    manifest = F5CampaignManifest.model_validate_json(manifest_bytes, strict=True)
    report = F5ValidationReport.model_validate_json(report_bytes, strict=True)
    assert manifest.campaign_id == spec.campaign_id
    assert manifest.enumerated_fault_classes == FAULT_CLASSES
    assert manifest.non_claim == ENUMERATED_FAULT_NON_CLAIM
    assert report.manifest_digest == artifacts.manifest_digest
    assert report.valid is True
    assert report.fault_pair_count == report.fault_row_count == report.twin_row_count == 8
    assert report.concurrent_row_count == 3
    assert report.unexpected_outcomes == ()
    assert report.non_claim == ENUMERATED_FAULT_NON_CLAIM
    assert all(pair.fault.observed.reward is None for pair in manifest.fault_pairs)
    assert all(pair.fault.observed.reward_quarantined for pair in manifest.fault_pairs)
    assert all(pair.twin.observed.reward == 1 for pair in manifest.fault_pairs)

    with pytest.raises(F5FaultCampaignError, match="already exists"):
        author_f5_fault_campaign(str(input_path.resolve()), str(output.resolve()))


def test_schema_print_cli_emits_closed_canonical_schema(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[3] / "scripts/rl_phase5/build_f5_fault_campaign.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--print-schema"],
        check=True,
        capture_output=True,
    )
    schema = json.loads(completed.stdout)
    assert completed.stderr == b""
    assert completed.stdout == canonical_json_bytes(schema) + b"\n"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == (
        "bb.rl.phase5-f5-fault-campaign-input.v1"
    )
    assert set(schema["properties"]) == {
        "schema_version",
        "campaign_id",
        "fault_pairs",
        "concurrent_rows",
    }


@pytest.mark.parametrize(
    "case",
    [
        "missing-fault",
        "duplicate-fault",
        "missing-twin",
        "wrong-error-class",
        "false-reward",
        "cross-canary-read",
        "shared-workspace",
        "shared-container",
        "shared-evidence",
        "shared-plan",
        "fallback",
        "stale-attempt",
        "failed-target-attempt",
        "superseded-attempt",
        "local-only-cleanup",
        "local-cleanup-stamp",
        "orphan",
        "missing-cleanup-proof",
        "transport-mislabeled-success",
        "silent-zero-reward-success",
        "target-identity-drift",
        "twin-identity-drift",
        "wrong-preallocation-boundary",
        "unexpected-outcome",
    ],
)
def test_adversarial_campaigns_fail_closed(case: str) -> None:
    payload = _payload()
    pairs = payload["fault_pairs"]
    concurrent = payload["concurrent_rows"]

    if case == "missing-fault":
        pairs.pop()
    elif case == "duplicate-fault":
        duplicate = copy.deepcopy(pairs[0])
        duplicate["pair_id"] = "pair-timeout-duplicate"
        duplicate["fault"]["row_id"] = "fault-timeout-duplicate"
        duplicate["twin"]["row_id"] = "twin-timeout-duplicate"
        pairs[-1] = duplicate
    elif case == "missing-twin":
        del pairs[0]["twin"]
    elif case == "wrong-error-class":
        pairs[0]["fault"]["expected"]["error_class"] = "CANCELLED"
        pairs[0]["fault"]["observed"]["error_class"] = "CANCELLED"
    elif case == "false-reward":
        pairs[0]["fault"]["observed"]["reward"] = 1
    elif case == "cross-canary-read":
        concurrent[0]["canary_reads"] = [concurrent[1]["canary"]]
    elif case == "shared-workspace":
        concurrent[1]["workspace_id"] = concurrent[0]["workspace_id"]
    elif case == "shared-container":
        concurrent[1]["container_id"] = concurrent[0]["container_id"]
    elif case == "shared-evidence":
        concurrent[1]["target"]["evidence"] = copy.deepcopy(
            concurrent[0]["target"]["evidence"]
        )
    elif case == "shared-plan":
        concurrent[1]["identities"]["effective_plan"] = copy.deepcopy(
            concurrent[0]["identities"]["effective_plan"]
        )
        concurrent[1]["target"]["join"]["effective_plan_digest"] = concurrent[0][
            "target"
        ]["join"]["effective_plan_digest"]
    elif case == "fallback":
        concurrent[0]["target"]["fallback_used"] = True
    elif case == "stale-attempt":
        concurrent[0]["target"]["attempt"]["current"] = False
    elif case == "failed-target-attempt":
        concurrent[0]["target"]["attempt"]["status"] = "failed"
    elif case == "superseded-attempt":
        concurrent[0]["target"]["attempt"]["superseded_by_attempt_id"] = "attempt-newer"
    elif case == "local-only-cleanup":
        pairs[0]["fault"]["cleanup"]["authority"] = "local_launcher"
    elif case == "local-cleanup-stamp":
        pairs[0]["fault"]["cleanup"]["local_cleanup_stamp"] = "closed"
    elif case == "orphan":
        pairs[0]["fault"]["cleanup"]["remaining_orphan_ids"] = [
            "container-orphan-1"
        ]
    elif case == "missing-cleanup-proof":
        del pairs[0]["fault"]["cleanup"]["remaining_secret_files"]
    elif case == "transport-mislabeled-success":
        transport = pairs[-1]["fault"]
        transport["expected"] = _success_outcome()
        transport["observed"] = {**_success_outcome(), "unexpected_outcomes": []}
        transport["cleanup"]["cleanup_required"] = True
        transport["cleanup"]["cleanup_attempts"] = 1
    elif case == "silent-zero-reward-success":
        concurrent[0]["expected"]["reward"] = 0
        concurrent[0]["observed"]["reward"] = 0
    elif case == "target-identity-drift":
        concurrent[0]["target"]["join"]["verifier_digest"] = _digest(
            "other-verifier"
        )
    elif case == "twin-identity-drift":
        pairs[0]["twin"]["identities"]["model"] = _identity(
            "model", "wrong-twin"
        )
        pairs[0]["twin"]["target"]["join"]["model_digest"] = pairs[0][
            "twin"
        ]["identities"]["model"]["digest"]
    elif case == "wrong-preallocation-boundary":
        revocation = pairs[2]["fault"]
        revocation["expected"]["failure_boundary"] = "post-allocation"
        revocation["expected"]["lease_opened"] = True
        revocation["observed"]["failure_boundary"] = "post-allocation"
        revocation["observed"]["lease_opened"] = True
        revocation["cleanup"]["cleanup_required"] = True
        revocation["cleanup"]["cleanup_attempts"] = 1
    elif case == "unexpected-outcome":
        concurrent[0]["observed"]["unexpected_outcomes"] = ["unclassified-event"]
    else:  # pragma: no cover - keeps the mutation table exhaustive
        raise AssertionError(case)

    with pytest.raises(ValidationError):
        _validate(payload)


def test_authoring_rejects_noncanonical_input_without_publishing(tmp_path: Path) -> None:
    input_path = tmp_path / "f5-input.json"
    input_path.write_text(json.dumps(_payload(), indent=2), encoding="utf-8")
    output = tmp_path / "must-not-exist"

    with pytest.raises(F5FaultCampaignError, match="canonical JSON"):
        author_f5_fault_campaign(str(input_path.resolve()), str(output.resolve()))

    assert not output.exists()
