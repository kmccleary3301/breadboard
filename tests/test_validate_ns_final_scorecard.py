from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts import validate_ns_final_scorecard as validator


TRACK_CONTRACT = {
    "SEC": (140, 13),
    "PROV": (50, 5),
    "E4": (120, 10),
    "PRIM": (100, 9),
    "ENG": (60, 6),
    "SRV": (70, 7),
    "RAY": (30, 2),
    "TS": (60, 5),
    "TEST": (50, 4),
    "GOV": (70, 11),
    "PROD": (90, 4),
    "RL": (80, 10),
    "SYNC": (40, 4),
    "AUD": (40, 3),
}
CARD_FIELDS = [
    "item_id",
    "candidate_or_run",
    "claim",
    "seam",
    "artifact_ref",
    "artifact_identity",
    "observation_receipt_ref",
    "observation_receipt_identity",
    "observed_result",
    "support_level",
    "observation_kind",
    "method",
    "declared_scope",
    "test_double_status",
]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _item_ids(track: str, count: int) -> list[str]:
    return [f"{track}-{index}" for index in range(1, count + 1)]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_ready_fixture(tmp_path: Path) -> dict[str, Any]:
    workspace_root = tmp_path
    ledger_path = workspace_root / "docs_tmp" / "bb_north_star_final" / "NS_FINAL_PROGRESS.json"
    crosswalk_path = workspace_root / "docs_tmp" / "bb_north_star_final" / "NS_FINAL_GOAL_CROSSWALK.json"
    plan_path = workspace_root / "docs_tmp" / "bb_north_star_final" / "NS_FINAL_MASTER_PLAN_V1.md"
    ledger_path.parent.mkdir(parents=True)
    candidate = "a" * 40
    tracks: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    plan_rows: list[str] = []

    for track, (points, count) in TRACK_CONTRACT.items():
        ids = _item_ids(track, count)
        base_points, extra = divmod(points, count)
        items: list[dict[str, Any]] = []
        for index, item_id in enumerate(ids):
            artifact = workspace_root / "evidence" / f"{item_id}.txt"
            receipt = workspace_root / "receipts" / f"{item_id}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"observed {item_id}\n", encoding="utf-8")
            _write_json(
                receipt,
                {
                    "schema_version": "fixture.observation.v1",
                    "item_id": item_id,
                    "claim_ids": [item_id],
                    "candidate_commit": candidate,
                    "passed": True,
                    "test_double_status": "claim_permitted_fixture",
                },
            )
            claim = f"Observed claim for {item_id}."
            seam = f"Observable seam for {item_id}."
            item = {
                "id": item_id,
                "points": base_points + (1 if index < extra else 0),
                "kind": "outcome",
                "claim": claim,
                "seam": seam,
                "state": "earned",
                "evidence_card": {
                    "item_id": item_id,
                    "candidate_or_run": candidate,
                    "claim": claim,
                    "seam": seam,
                    "artifact_ref": artifact.relative_to(workspace_root).as_posix(),
                    "artifact_identity": _sha256(artifact),
                    "observation_receipt_ref": receipt.relative_to(workspace_root).as_posix(),
                    "observation_receipt_identity": _sha256(receipt),
                    "observed_result": f"{item_id} passed",
                    "support_level": "observed",
                    "observation_kind": "local",
                    "method": "fixture boundary command",
                    "declared_scope": item_id,
                    "test_double_status": "claim_permitted_fixture",
                },
                "required_support_level": "observed",
                "allowed_observation_kinds": ["local", "target", "external"],
            }
            items.append(item)
            all_items.append(item)
            plan_rows.append(
                f"| {item_id} | {item['points']} | outcome | {claim} | {seam} |"
            )
        tracks.append({"track": track, "points": points, "earned_points": points, "items": items})

    plan_path.write_text("# Fixture canonical plan\n\n" + "\n".join(plan_rows) + "\n", encoding="utf-8")
    item_ids = [item["id"] for item in all_items]
    author_ids = ["fixture-author"]
    author_identity = _canonical_sha256(author_ids)
    aud2_input = validator._aud2_input_fingerprint(
        candidate=candidate,
        item_by_id={item["id"]: item for item in all_items},
        author_ids=author_ids,
    )
    review_output = workspace_root / "reviews" / "aud2.json"
    _write_json(
        review_output,
        {
            "schema_version": "fixture.aud2.v1",
            "review_id": "AUD-2-FIXTURE",
            "reviewer_session_id": "fixture-reviewer",
            "candidate_commit": candidate,
            "decision": "approved",
            "input_fingerprint": aud2_input,
            "checked_item_ids": item_ids,
            "unchecked_item_ids": [],
            "findings": [],
        },
    )
    review_identity = _sha256(review_output)
    aud2_review = {
        "review_id": "AUD-2-FIXTURE",
        "review_type": "final",
        "reviewer_id": "fixture-reviewer",
        "reviewer_harness_session_id": "fixture-reviewer",
        "reviewer_output_ref": review_output.relative_to(workspace_root).as_posix(),
        "reviewer_output_identity": review_identity,
        "candidate_author_session_ids": author_ids,
        "candidate_author_set_identity": author_identity,
        "non_author_check": True,
        "scope_ids": item_ids,
        "candidate_or_run": candidate,
        "input_fingerprint": aud2_input,
        "decision": "approved",
        "finding_ids": [],
        "checked_item_ids": item_ids,
        "unchecked_item_ids": [],
        "item_checks": [
            {
                "item_id": item["id"],
                "assigned_dimension": "fixture",
                "claim": item["claim"],
                "seam": item["seam"],
                "evidence_card_id": validator._canonical_sha256(item["evidence_card"]),
                "artifact_identity": item["evidence_card"]["artifact_identity"],
                "falsification_method": "fixture review",
                "scope_checked": item["id"],
                "test_double_status": "claim_permitted_fixture",
                "falsification_observation_ref": review_output.relative_to(workspace_root).as_posix(),
                "falsification_observation_identity": review_identity,
                "observed_result": "approved",
                "verdict": "approved",
            }
            for item in all_items
        ],
        "rerun_requirements": [],
        "supersedes": None,
    }
    reviewer_payload = json.loads(review_output.read_text(encoding="utf-8"))
    reviewer_payload["item_checks_identity"] = validator._aud2_checks_identity(
        aud2_review["item_checks"]
    )
    _write_json(review_output, reviewer_payload)
    review_identity = _sha256(review_output)
    aud2_review["reviewer_output_identity"] = review_identity
    for item_check in aud2_review["item_checks"]:
        item_check["falsification_observation_identity"] = review_identity

    gates: dict[str, Any] = {}
    for index, gate in enumerate(("G0", "G1", "G2", "G3", "G4"), start=1):
        fingerprint = "sha256:" + str(index) * 64
        gate_artifact = workspace_root / "gates" / f"{gate}.log"
        gate_receipt = workspace_root / "gates" / f"{gate}.json"
        gate_artifact.parent.mkdir(parents=True, exist_ok=True)
        gate_artifact.write_text(f"{gate} passed\n", encoding="utf-8")
        _write_json(
            gate_receipt,
            {
                "schema_version": "fixture.gate.v1",
                "gate_id": gate,
                "candidate_commit": candidate,
                "input_fingerprint": fingerprint,
                "review_ids": ["AUD-2-FIXTURE"],
                "passed": True,
            },
        )
        gates[gate] = {
            "status": "passed",
            "candidate": candidate,
            "input_fingerprint": fingerprint,
            "review_ids": ["AUD-2-FIXTURE"],
            "evidence_card": {
                "artifact_ref": gate_artifact.relative_to(workspace_root).as_posix(),
                "artifact_identity": _sha256(gate_artifact),
                "observation_receipt_ref": gate_receipt.relative_to(workspace_root).as_posix(),
                "observation_receipt_identity": _sha256(gate_receipt),
            },
            "invalidated_by": None,
        }

    broad_runs: list[dict[str, Any]] = []
    for gate in ("G3", "G4"):
        nonce = f"fixture-{gate.lower()}"
        artifact = workspace_root / "broad_runs" / f"{gate}.log"
        start = workspace_root / "broad_runs" / f"{gate}.start.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"{gate} broad run passed\n", encoding="utf-8")
        _write_json(
            start,
            {
                "schema_version": "fixture.broad_start.v1",
                "operation_nonce": nonce,
                "gate": gate,
                "candidate_commit": candidate,
                "passed": True,
            },
        )
        broad_runs.append(
            {
                "operation_nonce": nonce,
                "gate": gate,
                "executor_harness_session_id": "fixture-runner",
                "checkout_head": candidate,
                "input_fingerprint": "sha256:" + gate[-1] * 64,
                "argv": ["pytest", "-q"],
                "state": "passed",
                "exit_status": 0,
                "artifact_ref": artifact.relative_to(workspace_root).as_posix(),
                "artifact_identity": _sha256(artifact),
                "start_receipt_ref": start.relative_to(workspace_root).as_posix(),
                "start_receipt_identity": _sha256(start),
            }
        )

    pre_push_identity = "sha256:" + "b" * 64
    post_push_identity = "sha256:" + "c" * 64
    dolt_receipt = workspace_root / "issue_ops" / "dolt_push.json"
    _write_json(
        dolt_receipt,
        {
            "schema_version": "fixture.dolt_push.v1",
            "operation_nonce": "fixture-dolt-push",
            "candidate_commit": candidate,
            "pre_push_identity": pre_push_identity,
            "post_push_identity": post_push_identity,
            "remote_head": post_push_identity,
            "passed": True,
        },
    )
    ledger = {
        "schema_version": "bb.ns_final_progress.v5",
        "plan": plan_path.relative_to(workspace_root).as_posix(),
        "plan_revision": 2,
        "plan_activated": True,
        "campaign_state": "FINAL_AUDIT",
        "total_points": 1000,
        "earned_points": 1000,
        "control_panel": {
            "current_claim": None,
            "latest_user_visible_behavior": None,
            "current_red_class": None,
            "next_cheapest_discriminator": None,
            "active_external_blocker": None,
            "stop_or_escalation_condition": None,
        },
        "candidate": {
            "current_head": candidate,
            "merged_main_head": candidate,
            "campaign_author_session_ids": author_ids,
        },
        "gates": gates,
        "tracks": tracks,
        "active_packets": [],
        "integration_transactions": [],
        "external_jobs": [],
        "reviews": [aud2_review],
        "reds": [],
        "broad_runs": broad_runs,
        "blocked": [],
        "limitations": [],
        "bd_issue_map": {
            "campaign_epic_id": "bb-fixture",
            "packets": [
                {"packet_id": "fixture", "item_ids": item_ids, "issue_ids": ["bb-fixture.1"], "state": "closed"}
            ],
        },
        "issue_operations": [
            {
                "kind": "dolt_push",
                "state": "observed",
                "outcome": "success",
                "exit_status": 0,
                "candidate": candidate,
                "pre_push_identity": pre_push_identity,
                "post_push_identity": post_push_identity,
                "remote_head": post_push_identity,
                "operation_nonce": "fixture-dolt-push",
                "argv": "bd dolt push",
                "receipt_ref": dolt_receipt.relative_to(workspace_root).as_posix(),
                "receipt_identity": _sha256(dolt_receipt),
            }
        ],
        "deferred_register": [],
        "record_schemas": {"evidence_card": {"required": CARD_FIELDS}},
    }
    rows = [
        {
            "goal_id": f"goal-{index:03d}",
            "source_domain": "fixture",
            "status_at_inventory": "open",
            "statement": f"Fixture goal {index}",
            "disposition": "scored",
            "plan_refs": [item_ids[index % len(item_ids)]],
            "wake_trigger": None,
            "coverage_rule": None,
        }
        for index in range(102)
    ]
    crosswalk = {
        "schema_version": "bb.ns_final_goal_crosswalk.v2",
        "plan_revision": 2,
        "goal_count": 102,
        "allowed_dispositions": ["scored", "conditional", "deferred"],
        "rows": rows,
        "validation": {
            "unique_goal_ids": 102,
            "unmapped": [],
            "invalid_plan_refs": [],
            "unknown_dispositions": [],
        },
    }
    _write_json(ledger_path, ledger)
    _write_json(crosswalk_path, crosswalk)
    plan_inventory, plan_error = validator._plan_inventory(
        ledger=ledger,
        workspace_root=workspace_root,
    )
    assert plan_error is None
    return {
        "workspace_root": workspace_root,
        "ledger_path": ledger_path,
        "crosswalk_path": crosswalk_path,
        "plan_path": plan_path,
        "ledger": ledger,
        "crosswalk": crosswalk,
        "plan_identity": _canonical_sha256(plan_inventory),
        "crosswalk_identity": _canonical_sha256(
            [{field: row.get(field) for field in validator.CROSSWALK_INVENTORY_FIELDS} for row in rows]
        ),
    }


def _validate(fixture: dict[str, Any]) -> dict[str, Any]:
    return validator.validate_scorecard(
        ledger=fixture["ledger"],
        crosswalk=fixture["crosswalk"],
        ledger_path=fixture["ledger_path"],
        expected_plan_inventory_sha256=fixture["plan_identity"],
        expected_crosswalk_inventory_sha256=fixture["crosswalk_identity"],
    )


def test_validator_accepts_complete_exact_scorecard(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)

    report = _validate(fixture)

    assert report["valid"] is True
    assert report["ready"] is True
    assert report["counts"]["earned_points"] == 1000
    assert report["counts"]["item_count"] == 93
    assert report["counts"]["crosswalk_goal_count"] == 102
    assert report["errors"] == []
    assert report["blockers"] == []


def test_incomplete_scorecard_is_blocked_without_being_structurally_invalid(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    ledger = fixture["ledger"]
    ledger["gates"]["G4"]["status"] = "pending"
    item = ledger["tracks"][0]["items"][0]
    ledger["earned_points"] -= item["points"]
    ledger["tracks"][0]["earned_points"] -= item["points"]
    item["state"] = "pending"
    item["evidence_card"] = None

    report = _validate(fixture)

    assert report["valid"] is True
    assert report["ready"] is False
    assert "earned_points must equal 1000 for completion" in report["blockers"]
    assert "gate G4 must have a current receipt bound to exact inputs" in report["blockers"]


def test_validator_rejects_tampered_earned_artifact(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    card = fixture["ledger"]["tracks"][0]["items"][0]["evidence_card"]
    (tmp_path / card["artifact_ref"]).write_text("tampered\n", encoding="utf-8")

    report = _validate(fixture)

    assert report["valid"] is False
    assert any(error.endswith("artifact_identity does not match current bytes") for error in report["errors"])


def test_validator_rejects_hash_valid_but_semantically_empty_receipt(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    card = fixture["ledger"]["tracks"][0]["items"][0]["evidence_card"]
    receipt_path = tmp_path / card["observation_receipt_ref"]
    _write_json(receipt_path, {})
    card["observation_receipt_identity"] = _sha256(receipt_path)

    report = _validate(fixture)

    assert report["valid"] is False
    assert any("observation receipt must name a schema_version" in error for error in report["errors"])


def test_validator_rejects_noncanonical_plan_and_crosswalk_inventory(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    fixture["ledger"]["tracks"][0]["items"][0]["claim"] = "Attacker-selected replacement claim."
    fixture["crosswalk"]["rows"][0]["goal_id"] = "attacker-selected-goal"

    report = _validate(fixture)

    assert report["valid"] is False
    assert "ledger scored-item inventory does not match the canonical plan" in report["errors"]
    assert "crosswalk goal inventory identity is not approved" in report["errors"]


def test_gate_status_without_current_receipt_cannot_report_ready(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    fixture["ledger"]["gates"]["G2"]["evidence_card"] = None

    report = _validate(fixture)

    assert report["valid"] is True
    assert report["ready"] is False
    assert "gate G2 must have a current receipt bound to exact inputs" in report["blockers"]


def test_bare_broad_run_rows_cannot_report_ready(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    candidate = fixture["ledger"]["candidate"]["current_head"]
    fixture["ledger"]["broad_runs"] = [
        {"gate": gate, "checkout_head": candidate, "state": "passed"}
        for gate in ("G3", "G4")
    ]

    report = _validate(fixture)

    assert report["ready"] is False
    assert "a receipt-bound current passed broad run must bind G3" in report["blockers"]
    assert "a receipt-bound current passed broad run must bind G4" in report["blockers"]


def test_aud2_requires_complete_semantic_item_checks(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    review = fixture["ledger"]["reviews"][0]
    review["item_checks"] = [{"item_id": item_id} for item_id in review["checked_item_ids"]]

    report = _validate(fixture)

    assert report["ready"] is False
    assert "AUD-2 must be an exact-candidate independent review covering every scored item" in report["blockers"]

def test_aud2_rejects_presence_only_semantic_fields_even_when_output_rebinds(
    tmp_path: Path,
) -> None:
    fixture = _write_ready_fixture(tmp_path)
    review = fixture["ledger"]["reviews"][0]
    first_check = review["item_checks"][0]
    first_check["assigned_dimension"] = None
    first_check["evidence_card_id"] = "arbitrary"
    first_check["falsification_method"] = ""
    first_check["scope_checked"] = None
    output_path = tmp_path / review["reviewer_output_ref"]
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["item_checks_identity"] = validator._aud2_checks_identity(review["item_checks"])
    _write_json(output_path, output)
    new_identity = _sha256(output_path)
    review["reviewer_output_identity"] = new_identity
    for check in review["item_checks"]:
        check["falsification_observation_identity"] = new_identity

    report = _validate(fixture)

    assert report["ready"] is False
    assert "AUD-2 must be an exact-candidate independent review covering every scored item" in report["blockers"]

def test_aud2_rejects_hash_valid_semantically_empty_falsification_observation(
    tmp_path: Path,
) -> None:
    fixture = _write_ready_fixture(tmp_path)
    review = fixture["ledger"]["reviews"][0]
    first_check = review["item_checks"][0]
    empty_observation = tmp_path / "reviews" / "empty-observation.json"
    _write_json(empty_observation, {})
    first_check["falsification_observation_ref"] = empty_observation.relative_to(tmp_path).as_posix()
    first_check["falsification_observation_identity"] = _sha256(empty_observation)
    output_path = tmp_path / review["reviewer_output_ref"]
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["item_checks_identity"] = validator._aud2_checks_identity(review["item_checks"])
    _write_json(output_path, output)
    new_identity = _sha256(output_path)
    review["reviewer_output_identity"] = new_identity
    for check in review["item_checks"][1:]:
        check["falsification_observation_identity"] = new_identity

    report = _validate(fixture)

    assert report["ready"] is False
    assert "AUD-2 must be an exact-candidate independent review covering every scored item" in report["blockers"]


def test_aud2_parses_reviewer_output_decision(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    review = fixture["ledger"]["reviews"][0]
    output_path = tmp_path / review["reviewer_output_ref"]
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["decision"] = "blocked"
    _write_json(output_path, output)
    new_identity = _sha256(output_path)
    review["reviewer_output_identity"] = new_identity
    for check in review["item_checks"]:
        check["falsification_observation_identity"] = new_identity

    report = _validate(fixture)

    assert report["ready"] is False
    assert "AUD-2 must be an exact-candidate independent review covering every scored item" in report["blockers"]


def test_aud2_author_set_is_derived_from_packet_records(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    ledger = fixture["ledger"]
    ledger["active_packets"] = [
        {"packet_id": "material-author", "state": "completed", "author_session_ids": ["fixture-author"]}
    ]
    ledger["candidate"]["campaign_author_session_ids"] = []
    review = ledger["reviews"][0]
    review["candidate_author_session_ids"] = []
    review["candidate_author_set_identity"] = _canonical_sha256([])

    report = _validate(fixture)

    assert report["ready"] is False
    assert "AUD-2 must be an exact-candidate independent review covering every scored item" in report["blockers"]


def test_historical_dolt_push_cannot_report_ready(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    fixture["ledger"]["issue_operations"][0]["candidate"] = "b" * 40
    fixture["ledger"]["issue_operations"][0]["remote_head"] = "b" * 40

    report = _validate(fixture)

    assert report["ready"] is False
    assert "a receipt-bound successful bd dolt push must match the current candidate" in report["blockers"]


def test_aud3_observed_result_does_not_change_validator_input_fingerprint(tmp_path: Path) -> None:
    fixture = _write_ready_fixture(tmp_path)
    first = _validate(fixture)
    aud3 = next(
        item
        for track in fixture["ledger"]["tracks"]
        for item in track["items"]
        if item["id"] == "AUD-3"
    )
    aud3["evidence_card"]["observed_result"] = "validator wording changed without changing material inputs"

    second = _validate(fixture)

    assert first["input_fingerprint"] == second["input_fingerprint"]
