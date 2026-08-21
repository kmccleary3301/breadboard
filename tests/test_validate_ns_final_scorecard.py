from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validate_ns_final_scorecard.py"
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


def _item_ids(track: str, count: int) -> list[str]:
    required = {
        "SEC": ["SEC-12"],
        "SYNC": ["SYNC-2", "SYNC-3"],
        "AUD": ["AUD-1", "AUD-2", "AUD-3"],
    }.get(track, [])
    return required + [f"{track}-FIXTURE-{index}" for index in range(1, count - len(required) + 1)]


def _write_ready_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    workspace_root = tmp_path
    ledger_path = workspace_root / "docs_tmp" / "bb_north_star_final" / "NS_FINAL_PROGRESS.json"
    crosswalk_path = workspace_root / "docs_tmp" / "bb_north_star_final" / "NS_FINAL_GOAL_CROSSWALK.json"
    ledger_path.parent.mkdir(parents=True)
    candidate = "a" * 40
    tracks: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []

    for track, (points, count) in TRACK_CONTRACT.items():
        ids = _item_ids(track, count)
        base_points, extra = divmod(points, count)
        items: list[dict[str, Any]] = []
        for index, item_id in enumerate(ids):
            artifact = workspace_root / "evidence" / f"{item_id}.txt"
            receipt = workspace_root / "receipts" / f"{item_id}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(f"observed {item_id}\n", encoding="utf-8")
            receipt.write_text(json.dumps({"item_id": item_id, "candidate": candidate}) + "\n", encoding="utf-8")
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
        tracks.append({"track": track, "points": points, "earned_points": points, "items": items})

    item_ids = [item["id"] for item in all_items]
    review_output = workspace_root / "reviews" / "aud2.json"
    review_output.parent.mkdir(parents=True)
    review_output.write_text(json.dumps({"decision": "approved", "candidate": candidate}) + "\n", encoding="utf-8")
    review_identity = _sha256(review_output)
    author_ids = ["fixture-author"]
    author_identity = "sha256:" + hashlib.sha256(
        json.dumps(author_ids, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
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
        "input_fingerprint": "sha256:" + "b" * 64,
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
                "evidence_card_id": f"{item['id']}:fixture",
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
    ledger = {
        "schema_version": "bb.ns_final_progress.v5",
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
        "gates": {
            gate: {
                "status": "passed",
                "candidate": candidate,
                "input_fingerprint": "sha256:" + str(index) * 64,
                "review_ids": [],
                "evidence_card": None,
                "invalidated_by": None,
            }
            for index, gate in enumerate(("G0", "G1", "G2", "G3", "G4"), start=1)
        },
        "tracks": tracks,
        "active_packets": [],
        "integration_transactions": [],
        "external_jobs": [],
        "reviews": [aud2_review],
        "reds": [],
        "broad_runs": [
            {"gate": gate, "checkout_head": candidate, "state": "passed"}
            for gate in ("G3", "G4")
        ],
        "blocked": [],
        "limitations": [],
        "bd_issue_map": {
            "campaign_epic_id": "bb-fixture",
            "packets": [
                {"packet_id": "fixture", "item_ids": item_ids, "issue_ids": ["bb-fixture.1"], "state": "closed"}
            ],
        },
        "issue_operations": [
            {"kind": "dolt_push", "state": "observed", "outcome": "success", "exit_status": 0}
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
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    crosswalk_path.write_text(json.dumps(crosswalk, indent=2) + "\n", encoding="utf-8")
    return ledger_path, crosswalk_path, ledger


def _run(ledger_path: Path, crosswalk_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--ledger",
            str(ledger_path),
            "--crosswalk",
            str(crosswalk_path),
            "--json",
            *extra,
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_validator_accepts_complete_exact_scorecard(tmp_path: Path) -> None:
    ledger_path, crosswalk_path, _ = _write_ready_fixture(tmp_path)

    result = _run(ledger_path, crosswalk_path, "--require-ready")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["valid"] is True
    assert report["ready"] is True
    assert report["counts"]["earned_points"] == 1000
    assert report["counts"]["item_count"] == 93
    assert report["counts"]["crosswalk_goal_count"] == 102
    assert report["errors"] == []
    assert report["blockers"] == []
    assert report["input_fingerprint"].startswith("sha256:")


def test_require_ready_blocks_incomplete_scorecard_without_calling_it_invalid(tmp_path: Path) -> None:
    ledger_path, crosswalk_path, ledger = _write_ready_fixture(tmp_path)
    ledger["gates"]["G4"]["status"] = "pending"
    ledger["earned_points"] -= ledger["tracks"][0]["items"][0]["points"]
    ledger["tracks"][0]["earned_points"] -= ledger["tracks"][0]["items"][0]["points"]
    ledger["tracks"][0]["items"][0]["state"] = "pending"
    ledger["tracks"][0]["items"][0]["evidence_card"] = None
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    result = _run(ledger_path, crosswalk_path, "--require-ready")

    assert result.returncode == 4
    report = json.loads(result.stdout)
    assert report["valid"] is True
    assert report["ready"] is False
    assert "earned_points must equal 1000 for completion" in report["blockers"]
    assert "gate G4 must be passed on the current candidate" in report["blockers"]


def test_validator_rejects_tampered_earned_artifact(tmp_path: Path) -> None:
    ledger_path, crosswalk_path, ledger = _write_ready_fixture(tmp_path)
    artifact_ref = ledger["tracks"][0]["items"][0]["evidence_card"]["artifact_ref"]
    (tmp_path / artifact_ref).write_text("tampered\n", encoding="utf-8")

    result = _run(ledger_path, crosswalk_path)

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["valid"] is False
    assert any(error.endswith("artifact_identity does not match current bytes") for error in report["errors"])


def test_aud3_observed_result_does_not_change_validator_input_fingerprint(tmp_path: Path) -> None:
    ledger_path, crosswalk_path, ledger = _write_ready_fixture(tmp_path)
    first = _run(ledger_path, crosswalk_path)
    first_report = json.loads(first.stdout)
    aud3 = next(item for track in ledger["tracks"] for item in track["items"] if item["id"] == "AUD-3")
    aud3["evidence_card"]["observed_result"] = "validator wording changed without changing material inputs"
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    second = _run(ledger_path, crosswalk_path)

    assert first.returncode == 0
    assert second.returncode == 0
    assert json.loads(second.stdout)["input_fingerprint"] == first_report["input_fingerprint"]
