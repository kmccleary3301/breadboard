from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.e4_parity import run_lane


def _write_lane_def(
    lane_def_dir: Path,
    *,
    lane_id: str,
    config_id: str,
    status: str = "claimed",
    reverify_command: dict[str, object] | None,
) -> None:
    lane_def_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "bb.e4.lane_def.v1",
        "lane_id": lane_id,
        "config_id": config_id,
        "target_family": "test",
        "target_version": "v1",
        "kind": "target_support",
        "status": status,
        "points": 1,
        "capture": {"strategy": "legacy_builder", "argv": None, "inputs": ["fixture"]},
        "normalize": {"translator": "fixture", "config": {}},
        "replay": {"session": None, "comparator_class": "byte"},
        "compare": {"comparator": "byte", "config": {}},
        "claim": {"scope": {"behaviors": ["claim"], "surfaces": ["artifact"]}, "exclusions": []},
        "artifacts_root": "artifacts/conformance/node_gate",
        "reverify_command": reverify_command,
    }
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(payload), encoding="utf-8")


def _write_inventory(inventory_path: Path, lanes: list[dict[str, object]]) -> None:
    inventory_path.write_text(json.dumps({"lanes": lanes}), encoding="utf-8")


def _command(argv: list[str]) -> dict[str, object]:
    return {"argv": argv, "cwd": "."}


def _json_out(command: list[str]) -> Path:
    for index, item in enumerate(command):
        if item == "--json-out":
            return Path(command[index + 1])
        if item.startswith("--json-out="):
            return Path(item.split("=", 1)[1])
    raise AssertionError(f"command missing --json-out: {command!r}")


def _patch_successful_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        output_path = _json_out(command)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"ok": true}\n', encoding="utf-8")
        assert kwargs == {"cwd": run_lane.ROOT, "text": True, "capture_output": True, "check": False}
        return SimpleNamespace(returncode=0, stdout="claim stdout", stderr="")

    monkeypatch.setattr(run_lane.subprocess, "run", fake_run)
    monkeypatch.setattr(run_lane, "sha256_file", lambda path: f"sha256:{Path(path).name}")
    return calls


def test_accepted_lane_without_out_is_rejected_before_subprocess_can_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_accepted_without_out"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    would_write = tmp_path / "accepted-root" / "must-not-exist.json"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        status="accepted",
        reverify_command=_command(["python", "builder.py", "--json-out", str(would_write)]),
    )
    _write_inventory(inventory_path, [])
    subprocess_calls: list[list[str]] = []

    def fail_if_called(command: list[str], **_: object) -> None:
        subprocess_calls.append(command)
        would_write.parent.mkdir(parents=True, exist_ok=True)
        would_write.write_text("subprocess was called\n", encoding="utf-8")
        raise AssertionError("accepted lane without --out must not invoke subprocess.run")

    monkeypatch.setattr(run_lane.subprocess, "run", fail_if_called)

    with pytest.raises(run_lane.LaneRunError, match="accepted lanes require --out"):
        run_lane.run_lane(lane_id, stage="claim", out_dir=None, lane_def_dir=lane_def_dir, inventory_path=inventory_path)

    assert subprocess_calls == []
    assert not would_write.exists()


def test_claim_stage_uses_lane_def_reverify_command_and_retargets_json_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_lane_def_source"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    repo_root = tmp_path / "repo"
    scratch_root = tmp_path / "scratch"
    accepted_json = "artifacts/conformance/node_gate/lane_def_claim.json"
    inventory_json = "artifacts/conformance/node_gate/inventory_should_not_run.json"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        reverify_command=_command(
            ["python", "lane_def_builder.py", "--source", "lane-def", "--json-out", accepted_json, "--keep", "tail"]
        ),
    )
    _write_inventory(
        inventory_path,
        [
            {
                "lane_id": lane_id,
                "config_id": config_id,
                "reverify_command": _command(
                    ["python", "inventory_builder.py", "--source", "inventory", "--json-out", inventory_json]
                ),
            }
        ],
    )
    monkeypatch.setattr(run_lane, "ROOT", repo_root)
    calls = _patch_successful_subprocess(monkeypatch)

    result = run_lane.run_lane(
        lane_id, stage="claim", out_dir=scratch_root, lane_def_dir=lane_def_dir, inventory_path=inventory_path
    )

    expected_output = scratch_root / accepted_json
    assert calls == [
        [
            "python",
            "lane_def_builder.py",
            "--source",
            "lane-def",
            "--json-out",
            expected_output.resolve().as_posix(),
            "--keep",
            "tail",
        ]
    ]
    assert result == {
        "ok": True,
        "lane_id": lane_id,
        "stages": [
            {
                "stage": "claim",
                "lane_id": lane_id,
                "command": calls[0],
                "returncode": 0,
                "output_path": expected_output.resolve().as_posix(),
                "accepted_path": accepted_json,
                "stdout": "claim stdout",
                "stderr": "",
                "output_sha256": "sha256:lane_def_claim.json",
            }
        ],
    }
    assert expected_output.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert not (repo_root / accepted_json).exists()


@pytest.mark.parametrize(
    ("inventory_reverify_command", "inventory_ct_command", "expected_tool", "expected_json"),
    [
        (
            _command(["python", "inventory_reverify.py", "--source", "inventory-reverify", "--json-out", "artifacts/inventory_reverify.json"]),
            _command(["python", "ct_should_not_win.py", "--json-out", "artifacts/ct_should_not_win.json"]),
            "inventory_reverify.py",
            "artifacts/inventory_reverify.json",
        ),
        (
            None,
            _command(["python", "inventory_ct.py", "--source", "inventory-ct", "--json-out", "artifacts/inventory_ct.json"]),
            "inventory_ct.py",
            "artifacts/inventory_ct.json",
        ),
    ],
)
def test_claim_stage_falls_back_to_inventory_reverify_then_ct_command_when_lane_def_has_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inventory_reverify_command: dict[str, object] | None,
    inventory_ct_command: dict[str, object],
    expected_tool: str,
    expected_json: str,
) -> None:
    lane_id = f"run_lane_test_{Path(expected_tool).stem}"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    repo_root = tmp_path / "repo"
    scratch_root = tmp_path / "scratch"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        reverify_command=None,
    )
    inventory_lane: dict[str, object] = {
        "lane_id": lane_id,
        "config_id": config_id,
        "reverify_command": inventory_reverify_command,
        "ct": {"command": inventory_ct_command},
    }
    _write_inventory(inventory_path, [inventory_lane])
    monkeypatch.setattr(run_lane, "ROOT", repo_root)
    calls = _patch_successful_subprocess(monkeypatch)

    result = run_lane.run_lane(
        lane_id, stage="claim", out_dir=scratch_root, lane_def_dir=lane_def_dir, inventory_path=inventory_path
    )

    expected_output = scratch_root / expected_json
    assert len(calls) == 1
    assert calls[0][1] == expected_tool
    assert _json_out(calls[0]).resolve() == expected_output.resolve()
    assert result["ok"] is True
    assert result["stages"][0]["accepted_path"] == expected_json
    assert result["stages"][0]["output_path"] == expected_output.resolve().as_posix()
    assert expected_output.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert not (repo_root / expected_json).exists()


def test_all_stages_execute_safe_json_contract_and_resolve_comparator_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_all_stages"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    registry_path = tmp_path / "comparators.json"
    scratch_root = tmp_path / "scratch"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        status="claimed",
        reverify_command=_command(["python", "claim.py", "--json-out", "artifacts/claim.json"]),
    )
    lane_payload = json.loads((lane_def_dir / f"{lane_id}.yaml").read_text(encoding="utf-8"))
    lane_payload["capture"]["strategy"] = "probe_argv"
    lane_payload["capture"]["argv"] = ["python", "capture.py", "--json-out=artifacts/capture.json"]
    lane_payload["compare"]["comparator"] = "fixture_comparator"
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(lane_payload), encoding="utf-8")
    _write_inventory(inventory_path, [{"lane_id": lane_id, "config_id": config_id}])
    registry_path.write_text(
        json.dumps(
            {
                "comparators": [
                    {
                        "comparator_id": "fixture_comparator",
                        "entrypoint": {"module": "fixture", "callable": "compare"},
                        "lane_ids": [lane_id],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = _patch_successful_subprocess(monkeypatch)

    result = run_lane.run_lane(
        lane_id,
        stage="all",
        out_dir=scratch_root,
        lane_def_dir=lane_def_dir,
        inventory_path=inventory_path,
        comparator_registry_path=registry_path,
    )

    assert result["ok"] is True
    assert [stage["stage"] for stage in result["stages"]] == ["capture", "normalize", "replay", "compare", "claim"]
    assert [call[1] for call in calls] == ["capture.py", "claim.py"]
    assert calls[0][2].startswith("--json-out=")
    assert result["stages"][3]["comparator_id"] == "fixture_comparator"
    assert result["stages"][3]["skipped"] is True



def test_capture_adapter_runs_for_scratch_out_without_promoted_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_scratch_adapter"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    scratch_root = tmp_path / "scratch"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        status="claimed",
        reverify_command=None,
    )
    lane_payload = json.loads((lane_def_dir / f"{lane_id}.yaml").read_text(encoding="utf-8"))
    lane_payload["schema_version"] = "bb.e4.lane_def.v2"
    lane_payload["status"] = "planned"
    lane_payload["capture"] = {"strategy": "adapter", "inputs": ["fixture"], "adapter": "north_star_package_capture"}
    lane_payload["normalize"] = {"translator": "identity", "config": {}}
    lane_payload["compare"] = {"comparator": "semantic_replay_v1", "config": {}}
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(lane_payload), encoding="utf-8")
    _write_inventory(inventory_path, [{"lane_id": lane_id, "config_id": config_id}])
    calls: list[tuple[bool, Path | None]] = []

    def fake_adapter(lane_def: dict[str, object], inventory_lane: dict[str, object] | None, *, promote_accepted: bool, out_dir: Path | None) -> dict[str, object]:
        calls.append((promote_accepted, out_dir))
        output = out_dir / "adapter_capture.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"ok": true}\n', encoding="utf-8")
        return {"ok": True, "node_gate": output.as_posix()}

    fake_adapter.supports_scratch_out_dir = True
    monkeypatch.setattr(run_lane, "_capture_adapter_callable", lambda lane_def: fake_adapter)
    monkeypatch.setattr(run_lane, "_refresh_promoted_bindings", lambda: pytest.fail("scratch adapter must not refresh promoted bindings"))

    result = run_lane.run_lane(
        lane_id,
        stage="capture",
        out_dir=scratch_root,
        lane_def_dir=lane_def_dir,
        inventory_path=inventory_path,
        promote_accepted=False,
    )

    assert result["ok"] is True
    assert calls == [(False, scratch_root)]
    stage = result["stages"][0]
    assert stage["artifact_writer"] == "north_star_package_capture"
    assert stage["promotion_refresh"] is None
    assert stage["packet_report"]["node_gate"] == (scratch_root / "adapter_capture.json").as_posix()


def test_capture_adapter_scratch_out_requires_adapter_out_dir_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_unsafe_scratch_adapter"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    scratch_root = tmp_path / "scratch"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        status="claimed",
        reverify_command=None,
    )
    lane_payload = json.loads((lane_def_dir / f"{lane_id}.yaml").read_text(encoding="utf-8"))
    lane_payload["schema_version"] = "bb.e4.lane_def.v2"
    lane_payload["status"] = "planned"
    lane_payload["capture"] = {"strategy": "adapter", "inputs": ["fixture"], "adapter": "north_star_package_capture"}
    lane_payload["normalize"] = {"translator": "identity", "config": {}}
    lane_payload["compare"] = {"comparator": "semantic_replay_v1", "config": {}}
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(lane_payload), encoding="utf-8")
    _write_inventory(inventory_path, [{"lane_id": lane_id, "config_id": config_id}])
    called = False

    def unsafe_adapter(lane_def: dict[str, object], inventory_lane: dict[str, object] | None, *, promote_accepted: bool, out_dir: Path | None) -> dict[str, object]:
        nonlocal called
        called = True
        return {"ok": True, "node_gate": "accepted/path.json"}

    monkeypatch.setattr(run_lane, "_capture_adapter_callable", lambda lane_def: unsafe_adapter)

    with pytest.raises(run_lane.LaneRunError, match="does not declare scratch out_dir support"):
        run_lane.run_lane(
            lane_id,
            stage="capture",
            out_dir=scratch_root,
            lane_def_dir=lane_def_dir,
            inventory_path=inventory_path,
            promote_accepted=False,
        )

    assert called is False


def test_capture_adapter_promoted_run_refreshes_promoted_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_promoted_adapter"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        status="accepted",
        reverify_command=None,
    )
    lane_payload = json.loads((lane_def_dir / f"{lane_id}.yaml").read_text(encoding="utf-8"))
    lane_payload["schema_version"] = "bb.e4.lane_def.v2"
    lane_payload["run"] = {"run_id": "test_run", "provider_model": "none", "sandbox_mode": "none"}
    lane_payload["provenance"] = None
    lane_payload["acceptance"] = {
        "behavior_family": "fixture_behavior",
        "semantic_key": "fixture_semantic",
        "assertions": [{"id": "fixture_assertion", "description": "fixture assertion"}],
    }
    lane_payload["capture"] = {"strategy": "adapter", "inputs": ["fixture"], "adapter": "north_star_package_capture"}
    lane_payload["normalize"] = {"translator": "identity", "config": {}}
    lane_payload["compare"] = {"comparator": "semantic_replay_v1", "config": {}}
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(lane_payload), encoding="utf-8")
    _write_inventory(inventory_path, [{"lane_id": lane_id, "config_id": config_id}])
    calls: list[tuple[bool, Path | None]] = []

    def fake_adapter(lane_def: dict[str, object], inventory_lane: dict[str, object] | None, *, promote_accepted: bool, out_dir: Path | None) -> dict[str, object]:
        calls.append((promote_accepted, out_dir))
        return {"ok": True, "node_gate": "artifacts/promoted_adapter_capture.json"}

    monkeypatch.setattr(run_lane, "_capture_adapter_callable", lambda lane_def: fake_adapter)
    monkeypatch.setattr(run_lane, "_refresh_promoted_bindings", lambda: {"ok": True, "refreshed": True})

    result = run_lane.run_lane(
        lane_id,
        stage="capture",
        out_dir=None,
        lane_def_dir=lane_def_dir,
        inventory_path=inventory_path,
        promote_accepted=True,
    )

    assert result["ok"] is True
    assert calls == [(True, None)]
    assert result["stages"][0]["promotion_refresh"] == {"ok": True, "refreshed": True}

def test_output_with_only_empty_gate_envelope_fields_is_rewritten_to_accepted_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_accepted_compat"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    repo_root = tmp_path / "repo"
    scratch_root = tmp_path / "scratch"
    accepted_rel = "artifacts/conformance/node_gate/accepted.json"
    accepted_path = repo_root / accepted_rel
    accepted_path.parent.mkdir(parents=True, exist_ok=True)
    accepted_path.write_text(json.dumps({"ok": True}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_lane_def(
        lane_def_dir,
        lane_id=lane_id,
        config_id=config_id,
        status="accepted",
        reverify_command=_command(["python", "claim.py", "--json-out", accepted_rel]),
    )
    _write_inventory(inventory_path, [])
    monkeypatch.setattr(run_lane, "ROOT", repo_root)
    monkeypatch.setattr(run_lane, "sha256_file", lambda path: f"sha256:{Path(path).name}")

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        output_path = _json_out(command)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {"error_count": 0, "gate_errors": [], "ok": True, "pin_stale_count": 0, "semantic_count": 0},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_lane.subprocess, "run", fake_run)

    result = run_lane.run_lane(
        lane_id, stage="claim", out_dir=scratch_root, lane_def_dir=lane_def_dir, inventory_path=inventory_path
    )

    stage = result["stages"][0]
    assert stage["accepted_compatible_rewrite"] is True
    assert stage["byte_parity"] is True
    assert Path(stage["output_path"]).read_bytes() == accepted_path.read_bytes()
