from __future__ import annotations

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


def test_build_inventory_sorts_lane_defs() -> None:
    inventory = build_inventory({"z_lane": _lane_def("oh_my_pi_p9_z"), "a_lane": _lane_def("oh_my_pi_p9_a")})

    assert [row["lane_id"] for row in inventory["lanes"]] == ["oh_my_pi_p9_a", "oh_my_pi_p9_z"]


def test_compare_with_canonical_reports_field_mismatch() -> None:
    generated = build_inventory({"demo": _lane_def()})
    canonical = {"lanes": [{**generated["lanes"][0], "points": 8}]}

    report = compare_with_canonical(generated, canonical)

    assert report["ok"] is False
    assert report["errors"] == ["oh_my_pi_p9_demo.points mismatch"]


def test_compare_with_canonical_accepts_matching_subset() -> None:
    generated = build_inventory({"demo": _lane_def()})
    canonical = {"lanes": [generated["lanes"][0]]}

    report = compare_with_canonical(generated, canonical)

    assert report["ok"] is True
    assert report["errors"] == []
