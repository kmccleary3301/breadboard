from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

from breadboard.rl.phase5.bootstrap import bootstrap_campaign


SPEC_ROOT = Path("/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5")
PLAYBOOK = SPEC_ROOT / "BB_Z_RL_PHASE_5_CONFIG_NATIVE_EXECUTION_AND_OPTIMIZATION_PLAYBOOK.md"
GOAL_PROMPT = SPEC_ROOT / "phase5_config_native_1000_goal_prompt.txt"
REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED_AT = "2026-07-09T12:00:00Z"
EXPECTED_ARTIFACTS = {
    "SCORECARD.json",
    "ACTIVE_STATUS.json",
    "ARTIFACT_MANIFEST.json",
    "CLAIM_LEDGER.md",
    "EVIDENCE_TAXONOMY.json",
    "CAMPAIGN_MATRIX.yaml",
    "FIXTURE_MANIFEST.json",
    "VARIANT_CATALOG.json",
    "WORK_PACKET_DAG.yaml",
    "LOOP_SPEC.yaml",
}
EXPECTED_PACKET_DEPENDENCIES = {
    "WP0": set(),
    "WP1": {"WP0"},
    "WP2": {"WP1"},
    "WP3": {"WP2"},
    "WP4": {"WP3"},
    "WP5": {"WP2"},
    "WP6": {"WP5"},
    "WP7": {"WP3", "WP5"},
    "WP8": {"WP4", "WP6", "WP7"},
    "WP9": {"WP8"},
    "WP10": {"WP9"},
    "WP11": {"WP9"},
    "WP12": {"WP7", "WP8"},
    "WP13": {"WP10", "WP11", "WP12"},
    "WP13a": {"WP10"},
    "WP14": {"WP13", "WP13a"},
    "WP14b": {"WP7", "WP12", "WP13"},
}


def _bootstrap(output_dir: Path):
    return bootstrap_campaign(
        playbook_path=PLAYBOOK,
        goal_prompt_path=GOAL_PROMPT,
        output_dir=output_dir,
        generated_at=GENERATED_AT,
    )


def _bytes_by_name(output_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(output_dir).as_posix(): path.read_bytes()
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }


def _read_json(output_dir: Path, name: str) -> dict:
    return json.loads((output_dir / name).read_text(encoding="utf-8"))


def _assert_sorted_canonical_json(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    allowed_encodings = {
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    }
    assert raw in allowed_encodings


def test_bootstrap_is_byte_deterministic_for_a_fixed_timestamp(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = _bootstrap(first_dir)
    second = _bootstrap(second_dir)

    first_bytes = _bytes_by_name(first_dir)
    second_bytes = _bytes_by_name(second_dir)
    assert set(first_bytes) == EXPECTED_ARTIFACTS
    assert first_bytes == second_bytes
    assert first.item_count == second.item_count == 49
    assert first.catalog_points == second.catalog_points == 1000
    assert first.workstream_counts == {"A": 5, "B": 7, "C": 6, "D": 7, "E": 6, "F": 10, "G": 4, "H": 4}
    assert first.workstream_points == {"A": 90, "B": 170, "C": 150, "D": 170, "E": 120, "F": 200, "G": 60, "H": 40}
    assert first.packet_count == second.packet_count == 18
    assert first.catalog_sha256 == second.catalog_sha256
    assert first.campaign_spec_sha256 == second.campaign_spec_sha256

    assert set(first.artifact_hashes) == EXPECTED_ARTIFACTS
    for name, expected_hash in first.artifact_hashes.items():
        observed = "sha256:" + hashlib.sha256(first_bytes[name]).hexdigest()
        assert expected_hash == observed

    for name in EXPECTED_ARTIFACTS:
        if name.endswith(".json"):
            _assert_sorted_canonical_json(first_dir / name)


def test_bootstrap_materializes_a_zero_point_non_authoritative_baseline(tmp_path: Path) -> None:
    output_dir = tmp_path / "execution"
    result = _bootstrap(output_dir)
    scorecard = _read_json(output_dir, "SCORECARD.json")
    active_status = _read_json(output_dir, "ACTIVE_STATUS.json")

    assert scorecard["catalog_points"] == 1000
    assert scorecard["current_verified_points"] == 0
    assert len(scorecard["items"]) == 49
    assert {item["state"] for item in scorecard["items"]} == {"pending"}
    assert scorecard["campaign_spec_sha256"] == result.campaign_spec_sha256
    assert scorecard["frozen_hashes"]["breadboard_baseline"]["head"] == "550a387706d4ca4bc49760070f55a58100af168e"
    assert scorecard["frozen_hashes"]["wrapper_baseline"]["head"] == "d5221607f59ea05ffeba1e2931eff12142d9504d"
    assert scorecard["frozen_hashes"]["breadboard_baseline"]["canonical_payload_sha256"].startswith("sha256:")
    assert scorecard["frozen_hashes"]["wrapper_baseline"]["canonical_payload_sha256"].startswith("sha256:")
    assert scorecard["frozen_hashes"]["playbook"]["sha256"].startswith("sha256:")
    assert scorecard["frozen_hashes"]["goal_prompt"]["sha256"].startswith("sha256:")

    assert active_status["campaign_state"] == "READY"
    assert active_status["active"] is True
    assert active_status["active_status_id"] == "phase5-initial-status"
    assert active_status["current_verified_points"] == 0
    assert active_status["external_acceptance_state"] == "unclaimed"
    assert active_status["promotion_authorized"] is False
    assert active_status["scorecard_update_allowed"] is False
    assert active_status["authorities"] == []
    assert active_status["campaign_spec_sha256"] == result.campaign_spec_sha256


def test_bootstrap_emits_the_frozen_packet_dag_and_branch_triggers(tmp_path: Path) -> None:
    output_dir = tmp_path / "execution"
    _bootstrap(output_dir)

    packet_dag = yaml.safe_load((output_dir / "WORK_PACKET_DAG.yaml").read_text(encoding="utf-8"))
    packets = {packet["packet_id"]: packet for packet in packet_dag["packets"]}
    assert set(packets) == set(EXPECTED_PACKET_DEPENDENCIES) | {"WP15"}
    for packet_id, expected_dependencies in EXPECTED_PACKET_DEPENDENCIES.items():
        assert set(packets[packet_id]["dependencies"]) == expected_dependencies
    assert set(packets["WP15"]["dependencies"]) == set(packets) - {"WP15"}

    loop_spec = yaml.safe_load((output_dir / "LOOP_SPEC.yaml").read_text(encoding="utf-8"))
    branches = loop_spec["branches"]
    assert set(branches) == {"F9_PPO", "F10_RUNSC", "F10_DIGITALOCEAN"}
    assert branches["F9_PPO"]["disabled_disposition"] == (
        "DISABLED_WITH_REQUIRED_NONCLAIM"
    )
    assert branches["F9_PPO"]["forced_grpo_can_claim_ppo"] is False
    assert branches["F10_RUNSC"]["incompatible_disposition"] == (
        "INFEASIBLE_WITH_REQUIRED_NONCLAIM"
    )
    assert branches["F10_RUNSC"]["gvisor_claim_without_parity"] is False
    assert branches["F10_DIGITALOCEAN"]["not_triggered_disposition"] == "NOT_TRIGGERED"
    assert branches["F10_DIGITALOCEAN"]["substitutes_for_ibm"] is False


def test_bootstrap_cli_writes_the_same_artifact_contract(tmp_path: Path) -> None:
    output_dir = tmp_path / "cli-execution"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase5/bootstrap_phase5.py",
            "--playbook",
            str(PLAYBOOK),
            "--goal-prompt",
            str(GOAL_PROMPT),
            "--output-dir",
            str(output_dir),
            "--generated-at",
            GENERATED_AT,
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("\n") == 1
    assert "items=49" in result.stdout
    assert "points=1000" in result.stdout
    assert "counts=5/7/6/7/6/10/4/4" in result.stdout
    assert "acyclic=true" in result.stdout
    assert set(_bytes_by_name(output_dir)) == EXPECTED_ARTIFACTS
    assert _read_json(output_dir, "SCORECARD.json")["current_verified_points"] == 0
