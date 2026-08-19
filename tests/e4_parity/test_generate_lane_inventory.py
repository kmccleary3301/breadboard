from __future__ import annotations
from pathlib import Path

import pytest

from scripts.e4_parity import generate_lane_inventory
from scripts.e4_parity.generate_lane_inventory import build_inventory, compare_with_canonical, lane_inventory_row


def _lane_def(lane_id: str = "oh_my_pi_p9_demo") -> dict:
    return {
        "lane_id": lane_id,
        "config_id": f"{lane_id}_v1",
        "target_family": "oh_my_pi",
        "target_version": "@oh-my-pi/pi-coding-agent@16.2.13",
        "kind": "target_support",
        "status": "accepted",
        "points": 7,
        "capture": {"argv": [".venv/bin/python", "scripts/e4_parity/build_demo.py"]},
        "compare": {"comparator": "stored_report"},
        "claim": {"scope": {"behaviors": ["bb.demo.v1"]}},
        "artifacts_root": f"docs/conformance/e4_target_support/{lane_id}",
        "ct": {"test_id": "CT-DEMO-C4"},
        "run": {
            "run_id": "demo-run",
            "provider_model": "demo/model",
            "sandbox_mode": "read-only demo",
        },
        "reverify_command": {
            "argv": [
                ".venv/bin/python",
                "scripts/validate_e4_c4_chain.py",
                "--config-id",
                f"{lane_id}_v1",
                "--support-claim",
                f"docs/conformance/support_claims/{lane_id}_v1_c4_support_claim.json",
                "--evidence-manifest",
                f"docs/conformance/support_claims/{lane_id}_v1_c4_evidence_manifest.json",
                "--json-out",
                f"artifacts/conformance/node_gate/{lane_id}.json",
                "--check-only",
            ],
            "cwd": ".",
        },
    }


def test_lane_inventory_row_derives_claim_ct_and_builder() -> None:
    row = lane_inventory_row(_lane_def())

    assert row["claim_id"] == "oh_my_pi_p9_demo_v1_c4_support_claim"
    assert row["builder"]["argv"] == [".venv/bin/python", "scripts/e4_parity/build_demo.py"]
    assert row["ct"]["test_id"] == "CT-DEMO-C4"
    assert "--check-only" not in row["ct"]["command"]["argv"]
    assert row["reverify_command"]["argv"][-1] == "--check-only"


def test_probe_argv_capture_does_not_become_builder() -> None:
    lane_def = _lane_def("codex_cli_e4_capture_probe_v1")
    lane_def["capture"]["strategy"] = "probe_argv"

    row = lane_inventory_row(lane_def)

    assert row["builder"] is None


def test_lane_inventory_row_uses_packet_feature_id_without_claim_artifacts() -> None:
    lane_def = _lane_def()
    lane_def["normalize"] = {
        "config": {"packet_constants": {"feature_id": "feat_demo_packet"}}
    }

    row = lane_inventory_row(lane_def)

    assert row["ledger_feature_ids"] == ["feat_demo_packet"]


def test_lane_inventory_row_reports_retired_producer_and_uses_frozen_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lane_def = _lane_def()
    lane_def["status"] = "superseded"
    lane_def["claim"]["status"] = "accepted"
    digest = "sha256:" + "1" * 64
    report = "frozen/report.json"
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "report.json").write_text(
        '{"support_claim":"frozen/support.json",'
        '"evidence_manifest":"frozen/manifest.json"}',
        encoding="utf-8",
    )
    (frozen / "support.json").write_text(
        '{"ledger_row_refs":["ledger.json#feat_retired#sha256:digest"]}',
        encoding="utf-8",
    )
    (frozen / "manifest.json").write_text(
        '{"artifacts":[{"role":"capture_ref"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_lane_inventory, "ROOT", tmp_path)
    monkeypatch.setattr(
        generate_lane_inventory,
        "_RETIRED_EVIDENCE_PINS_CACHE",
        {
            "oh_my_pi_p9_demo": {
                "validation_report": report,
                "sha256": digest,
            }
        },
    )

    row = lane_inventory_row(lane_def)

    expected_argv = [
        ".venv/bin/python",
        "scripts/e4_parity/validate_frozen_e4_evidence.py",
        "--validation-report",
        report,
        "--retired-evidence-pins",
        "config/e4_retired_evidence_pins.json",
        "--json-out",
        "artifacts/conformance/node_gate/ct_frozen_oh_my_pi_p9_demo.json",
    ]
    assert row["status"] == "superseded"
    assert row["evidence_status"] == "accepted"
    assert row["ct"]["command"]["argv"] == expected_argv
    assert row["reverify_command"]["argv"] == expected_argv
    assert row["ledger_feature_ids"] == ["feat_retired"]
    assert row["artifact_roles"]["capture"] == "oh_my_pi_p9_demo:capture"


def test_build_inventory_sorts_lane_defs() -> None:
    inventory = build_inventory({"z_lane": _lane_def("oh_my_pi_p9_z"), "a_lane": _lane_def("oh_my_pi_p9_a")})
    assert inventory["schema_version"] == "bb.e4.lane_inventory.v2"
    assert inventory["inventory_id"] == "e4_lane_inventory_from_lane_defs_v2"

    assert [row["lane_id"] for row in inventory["lanes"]] == ["oh_my_pi_p9_a", "oh_my_pi_p9_z"]


@pytest.mark.parametrize(
    ("field", "canonical_value"),
    [
        pytest.param("points", 8, id="points"),
        pytest.param("run_id", "different-run", id="run-id"),
        pytest.param("provider_model", "different/model", id="provider-model"),
        pytest.param("sandbox_mode", "different sandbox", id="sandbox-mode"),
        pytest.param("evidence_status", "accepted", id="evidence-status"),
        pytest.param("phase", "P8", id="phase"),
        pytest.param("primitives", ["different"], id="primitives"),
        pytest.param("comparator_id", "different-comparator", id="comparator-id"),
        pytest.param("artifacts_root", "different/root", id="artifacts-root"),
    ],
)
def test_compare_with_canonical_reports_field_mismatch(field: str, canonical_value: object) -> None:
    generated = build_inventory({"demo": _lane_def()})
    canonical_row = {**generated["lanes"][0], field: canonical_value}

    report = compare_with_canonical(generated, {"lanes": [canonical_row]})

    assert report["ok"] is False
    assert report["errors"] == [f"oh_my_pi_p9_demo.{field} mismatch"]


def test_compare_with_canonical_reports_unexpected_canonical_field() -> None:
    generated = build_inventory({"demo": _lane_def()})
    canonical_row = {**generated["lanes"][0], "future_contract_field": "unexpected"}

    report = compare_with_canonical(generated, {"lanes": [canonical_row]})

    assert report["ok"] is False
    assert report["errors"] == ["oh_my_pi_p9_demo.future_contract_field mismatch"]


def test_compare_with_canonical_accepts_matching_subset() -> None:
    generated = build_inventory({"demo": _lane_def()})
    canonical = {"lanes": [generated["lanes"][0]]}

    report = compare_with_canonical(generated, canonical)

    assert report["ok"] is True
    assert report["errors"] == []
