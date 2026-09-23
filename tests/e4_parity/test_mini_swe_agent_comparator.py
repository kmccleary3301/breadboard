from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

LANE_ID = "mini_swe_agent_2_4_6_replay"
ARTIFACTS_ROOT = Path("docs/conformance/e4_target_support") / LANE_ID


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _trace(role: str, case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.mini-trace.v1",
        "role": role,
        "case_id": case_id,
        "scenario_sha256": "sha256:" + "a" * 64,
        "requests": [
            {"index": 0, "body": {"messages": [{"role": "user", "content": "hi"}]}, "served": "completion"}
        ],
        "history": [
            {"role": "assistant", "content": "working", "tool_calls": [{"id": "call-1", "name": "bash"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "first"},
            {"role": "tool", "tool_call_id": "call-2", "content": "second"},
        ],
        "exit": {"status": "submitted", "submission": "done"},
        "effects": {"files": {"result.txt": "sha256:" + "b" * 64}},
        "counters": {"model_calls": 1, "http_attempts": 1, "model_cost": 0.1},
        "normalizations": ["workspace_root:<WORKSPACE>"],
    }


def _write_packet(root: Path, capture: dict[str, Any], replay: dict[str, Any]) -> None:
    packet = root / ARTIFACTS_ROOT
    (packet / "capture").mkdir(parents=True, exist_ok=True)
    (packet / "replay").mkdir(parents=True, exist_ok=True)
    for role, traces, directory in (
        ("supplier", capture, "capture"),
        ("breadboard", replay, "replay"),
    ):
        cases: dict[str, dict[str, str]] = {}
        for case_id, trace in traces.items():
            trace_path = packet / directory / f"{case_id}.json"
            trace_path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            cases[case_id] = {"path": f"{directory}/{case_id}.json", "sha256": _sha256(trace_path)}
        manifest = {"schema_version": "bb.e4.mini-trace-manifest.v1", "role": role, "cases": cases}
        manifest_name = "raw_capture_manifest.json" if role == "supplier" else "bb_replay_result.json"
        (packet / manifest_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # run_lane requires the registered comparator_ref role to exist, although the
    # live Mini comparator derives its report from the two trace manifests.
    (packet / "comparator_report.json").write_text("{}\n", encoding="utf-8")


def _lane_command(repo_root: Path, scratch: Path, lane_def_dir: Path, inventory: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(repo_root)
    return subprocess.run(
        [
            sys.executable,
            "scripts/e4_parity/run_lane.py",
            "--lane",
            LANE_ID,
            "--stage",
            "compare",
            "--out",
            str(scratch),
            "--lane-def-dir",
            str(lane_def_dir),
            "--inventory",
            str(inventory),
            "--comparator-registry",
            str(repo_root / "conformance/comparators/registry.json"),
            "--json",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_lane(tmp_path: Path, repo_root: Path) -> tuple[Path, Path, Path]:
    lane_def_dir = tmp_path / "lanes"
    lane_def_dir.mkdir()
    shutil.copyfile(
        repo_root / "config/e4_lanes/mini_swe_agent_2_4_6_replay.yaml",
        lane_def_dir / f"{LANE_ID}.yaml",
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text('{"lanes": []}\n', encoding="utf-8")
    scratch = tmp_path / "scratch"
    return lane_def_dir, inventory, scratch


def _report(scratch: Path) -> dict[str, Any]:
    return json.loads(
        (scratch / ARTIFACTS_ROOT / "comparator_report.json").read_text(encoding="utf-8")
    )


def _base_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    capture = {case_id: _trace("supplier", case_id) for case_id in ("normal_grouped_batch", "provider_failures")}
    replay = {case_id: _trace("breadboard", case_id) for case_id in capture}
    return capture, replay


def test_positive_pair_passes_through_actual_lane_command(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    capture, replay = _base_pair()
    lane_def_dir, inventory, scratch = _prepare_lane(tmp_path, repo_root)
    _write_packet(scratch, capture, replay)

    result = _lane_command(repo_root, scratch, lane_def_dir, inventory)

    assert result.returncode == 0, result.stderr
    report = _report(scratch)
    assert report["ok"] is True
    assert report["failed"] == 0
    assert report["errors"] == []
    assert {a["assertion_id"] for a in report["assertions"]} >= {
        "normal_grouped_batch.requests_equal",
        "normal_grouped_batch.history_equal",
        "normal_grouped_batch.exit_equal",
        "normal_grouped_batch.effects_equal",
        "normal_grouped_batch.counters_equal",
    }


def _mutate_extra_tool(trace: dict[str, Any]) -> None:
    trace["history"].append({"role": "tool", "tool_call_id": "call-extra", "content": "unexpected"})


def _mutate_extra_effect(trace: dict[str, Any]) -> None:
    trace["effects"]["files"]["unexpected.txt"] = "sha256:" + "c" * 64


def _mutate_shell_order(trace: dict[str, Any]) -> None:
    trace["history"][1], trace["history"][2] = trace["history"][2], trace["history"][1]


def _mutate_retry_count(trace: dict[str, Any]) -> None:
    trace["counters"]["http_attempts"] += 1


def _mutate_final_history(trace: dict[str, Any]) -> None:
    trace["exit"]["submission"] = "changed"


@pytest.mark.parametrize(
    ("mutation", "assertion_id"),
    [
        (_mutate_extra_tool, "normal_grouped_batch.history_equal"),
        (_mutate_extra_effect, "normal_grouped_batch.effects_equal"),
        (_mutate_shell_order, "normal_grouped_batch.history_equal"),
        (_mutate_retry_count, "normal_grouped_batch.counters_equal"),
        (_mutate_final_history, "normal_grouped_batch.exit_equal"),
    ],
    ids=["batch_atomicity", "effect_commit_separation", "shell_order", "retry_count", "final_history"],
)
def test_negative_mutations_fail_semantic_predicate_and_preserve_report(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    assertion_id: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    capture, replay = _base_pair()
    mutation(replay["normal_grouped_batch"])
    lane_def_dir, inventory, scratch = _prepare_lane(tmp_path, repo_root)
    _write_packet(scratch, capture, replay)

    result = _lane_command(repo_root, scratch, lane_def_dir, inventory)

    assert result.returncode == 1, result.stderr
    report = _report(scratch)
    assert report["ok"] is False
    assert report["failed"] >= 1
    failed = {a["assertion_id"]: a for a in report["assertions"] if a["status"] == "failed"}
    assert assertion_id in failed
    assert "first difference" in failed[assertion_id]["detail"]


def test_stale_hash_fails_hash_assertion_specifically(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    capture, replay = _base_pair()
    lane_def_dir, inventory, scratch = _prepare_lane(tmp_path, repo_root)
    _write_packet(scratch, capture, replay)
    manifest_path = scratch / ARTIFACTS_ROOT / "bb_replay_result.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cases"]["normal_grouped_batch"]["sha256"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = _lane_command(repo_root, scratch, lane_def_dir, inventory)

    assert result.returncode == 1, result.stderr
    report = _report(scratch)
    failed = {a["assertion_id"] for a in report["assertions"] if a["status"] == "failed"}
    assert failed == {"replay_hashes_valid"}
    assert "expected sha256:000000" in next(
        a["detail"] for a in report["assertions"] if a["assertion_id"] == "replay_hashes_valid"
    )
