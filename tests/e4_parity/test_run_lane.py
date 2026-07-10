from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.e4_parity import run_lane
from scripts.e4_parity.tree_digest import digest_directory


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
        "normalize": {"mode": "identity", "translator": "identity", "config": {}},
        "replay": {
            "mode": "stored",
            "artifacts": ["fixture"],
            "session": None,
            "comparator_class": "byte",
        },
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


def _normalized_h2_lane(
    input_paths: list[str],
    *,
    lane_id: str = "identity_normalize_fixture",
    mode: str | None = "identity",
) -> dict[str, object]:
    normalize: dict[str, object] = {"translator": "identity", "config": {"fixture": "h2"}}
    if mode is not None:
        normalize["mode"] = mode
    return {
        "schema_version": "bb.e4.lane_def.v2",
        "_lane_def_version": 2,
        "lane_id": lane_id,
        "config_id": f"{lane_id}_v1",
        "kind": "target_support",
        "status": "planned",
        "capture": {
            "strategy": "adapter",
            "argv": None,
            "inputs": input_paths,
            "adapter": "pi_p5_l1_capture",
        },
        "normalize": normalize,
        "replay": {"mode": "stored", "session": None, "comparator_class": "semantic"},
        "compare": {"comparator": "pi_stored_report_replay", "config": {}},
        "claim": {"scope": {"behaviors": ["fixture"], "surfaces": ["fixture"]}, "exclusions": []},
        "artifacts_root": "artifacts/fixture",
        "reverify_command": None,
        "run": None,
        "provenance": None,
        "acceptance": {
            "behavior_family": None,
            "semantic_key": None,
            "target": None,
            "assertions": [],
        },
    }


def _patch_h2_lane_loading(
    monkeypatch: pytest.MonkeyPatch, lane_def: dict[str, object]
) -> None:
    monkeypatch.setattr(run_lane, "load_lane_defs", lambda _directory: {lane_def["lane_id"]: lane_def})
    monkeypatch.setattr(run_lane, "_inventory_lane", lambda _lane_id, _inventory_path: None)


def _report_path(repo_root: Path, report_ref: object) -> Path:
    assert isinstance(report_ref, str) and report_ref
    path = Path(report_ref)
    return path if path.is_absolute() else repo_root / path


def test_stored_replay_hashes_every_declared_artifact_in_manifest_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = [
        ("replay/session.json", b'{"session":"stored"}\n'),
        ("replay/transcript.jsonl", b'{"event":"first"}\n{"event":"second"}\n'),
    ]
    for logical_path, content in artifacts:
        path = tmp_path / logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    lane_def = _normalized_h2_lane([])
    lane_def["replay"]["artifacts"] = [logical_path for logical_path, _ in artifacts]
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="replay",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert result["ok"] is True
    stage = result["stages"][0]
    assert stage["outcome"] == "reused_stored_result"
    assert stage["manifest_rule"] == "/replay/mode"
    assert stage["reused_inputs"] == [
        {
            "path": logical_path,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
        for logical_path, content in artifacts
    ]
    assert stage["honesty_errors"] == []


def test_stored_replay_fails_when_a_declared_artifact_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_ref = "replay/missing-result.json"
    lane_def = _normalized_h2_lane([])
    lane_def["replay"]["artifacts"] = [missing_ref]
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="replay",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert result["ok"] is False
    assert missing_ref in result["stages"][0]["detail"]


def test_stored_replay_directory_uses_shared_canonical_tree_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logical_path = "replay/session-tree"
    directory = tmp_path / logical_path
    (directory / "nested").mkdir(parents=True)
    (directory / "manifest.json").write_bytes(b'{"session":"stored"}\n')
    (directory / "nested" / "events.jsonl").write_bytes(b'{"event":1}\n')
    expected_digest = digest_directory(directory).digest
    calls: list[Path] = []

    def recording_digest_directory(path: Path):
        calls.append(Path(path))
        return digest_directory(path)

    lane_def = _normalized_h2_lane([])
    lane_def["replay"]["artifacts"] = [logical_path]
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)
    monkeypatch.setattr(run_lane, "digest_directory", recording_digest_directory)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="replay",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert calls == [directory]
    assert result["ok"] is True
    stage = result["stages"][0]
    assert stage["reused_inputs"] == [{"path": logical_path, "sha256": expected_digest}]
    assert stage["honesty_errors"] == []


def test_unsafe_stored_replay_tree_emits_honest_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logical_path = "replay/unsafe-session-tree"
    directory = tmp_path / logical_path
    directory.mkdir(parents=True)
    target = directory / "target.json"
    target.write_bytes(b'{"stored":true}\n')
    try:
        (directory / "linked.json").symlink_to(target.name)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    lane_def = _normalized_h2_lane([])
    lane_def["replay"]["artifacts"] = [logical_path]
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="replay",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert result["ok"] is False
    stage = result["stages"][0]
    assert stage["outcome"] == "executed_fail"
    assert stage["honesty_errors"] == []
    report_path = _report_path(tmp_path, stage["report_ref"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    rendered_report = json.dumps(report, sort_keys=True)
    assert logical_path in rendered_report
    assert "symlink" in rendered_report.lower()


def test_identity_normalize_resolves_and_invokes_registered_translator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.e4_parity.adapters import identity

    logical_paths = ["inputs/first.json", "inputs/second.txt"]
    input_bytes = [b'{"raw":true}\n', b"second input\n"]
    for logical_path, content in zip(logical_paths, input_bytes, strict=True):
        path = tmp_path / logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    lane_def = _normalized_h2_lane(logical_paths)
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)
    calls: list[tuple[object, object]] = []

    def recording_identity(payload: object, *, config: object = None) -> object:
        calls.append((payload, config))
        return payload

    monkeypatch.setattr(identity, "translate", recording_identity)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="normalize",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert calls == [(logical_paths, {"fixture": "h2"})]
    assert result["ok"] is True
    stage = result["stages"][0]
    assert stage["outcome"] == "executed_pass"
    assert stage["reused_inputs"] is None
    assert stage["honesty_errors"] == []
    report_path = _report_path(tmp_path, stage["report_ref"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["lane_id"] == lane_def["lane_id"]
    assert report["mode"] == "identity"
    assert report["translator"] == "identity"
    assert report["input_hashes"] == [
        {
            "path": logical_path,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
        for logical_path, content in zip(logical_paths, input_bytes, strict=True)
    ]
    assert report["output"] == logical_paths


def test_pi_p5_l1_adapter_lane_produces_executed_normalize_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical_path = "inputs/pi-p5-l1.json"
    content = b'{"surface":"cli/config/context/tool"}\n'
    input_path = tmp_path / logical_path
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(content)
    lane_def = _normalized_h2_lane(
        [logical_path],
        lane_id="pi_p5_l1_cli_config_context_tool_surface",
    )
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="normalize",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert result["ok"] is True
    stage = result["stages"][0]
    assert stage["outcome"] == "executed_pass"
    report = json.loads(_report_path(tmp_path, stage["report_ref"]).read_text(encoding="utf-8"))
    assert report["lane_id"] == "pi_p5_l1_cli_config_context_tool_surface"
    assert report["translator"] == "identity"
    assert report["input_hashes"] == [
        {
            "path": logical_path,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
    ]


def test_missing_normalize_mode_fails_with_a_concrete_stage_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical_path = "inputs/source.json"
    input_path = tmp_path / logical_path
    input_path.parent.mkdir(parents=True)
    input_path.write_text('{"source":true}\n', encoding="utf-8")
    lane_def = _normalized_h2_lane([logical_path], mode=None)
    _patch_h2_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        str(lane_def["lane_id"]),
        stage="normalize",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert result["ok"] is False
    stage = result["stages"][0]
    assert stage["outcome"] == "executed_fail"
    assert stage["returncode"] != 0
    assert "normalize.mode" in stage["detail"]
    assert stage["honesty_errors"] == []
    report_path = _report_path(tmp_path, stage["report_ref"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert "normalize.mode" in report["detail"]


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
    assert result["ok"] is True
    stage = result["stages"][0]
    assert stage["stage"] == "claim"
    assert stage["lane_id"] == lane_id
    assert stage["command"] == calls[0]
    assert stage["returncode"] == 0
    assert stage["output_path"] == expected_output.resolve().as_posix()
    assert stage["accepted_path"] == accepted_json
    assert stage["stdout"] == "claim stdout"
    assert stage["stderr"] == ""
    assert stage["output_sha256"] == "sha256:lane_def_claim.json"
    assert stage["outcome"] == "executed_pass"
    assert stage["report_ref"] == expected_output.resolve().as_posix()
    assert stage["honesty_errors"] == []
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


def test_all_stages_execute_normalize_then_fail_closed_on_undeclared_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_id = "run_lane_test_all_stages"
    config_id = "run_lane_test_config"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    scratch_root = tmp_path / "scratch"
    input_path = tmp_path / "fixture"
    input_path.write_text('{"fixture":true}\n', encoding="utf-8")
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
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(lane_payload), encoding="utf-8")
    lane_payload["replay"].pop("mode")
    lane_payload["replay"].pop("artifacts")
    monkeypatch.setattr(run_lane, "load_lane_defs", lambda _directory: {lane_id: lane_payload})
    _write_inventory(inventory_path, [{"lane_id": lane_id, "config_id": config_id}])
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)
    calls = _patch_successful_subprocess(monkeypatch)

    result = run_lane.run_lane(
        lane_id,
        stage="all",
        out_dir=scratch_root,
        lane_def_dir=lane_def_dir,
        inventory_path=inventory_path,
    )

    assert result["ok"] is False
    assert [stage["stage"] for stage in result["stages"]] == ["capture", "normalize", "replay", "compare", "claim"]
    assert [call[1] for call in calls] == ["capture.py"]
    assert calls[0][2].startswith("--json-out=")
    assert result["stages"][1]["outcome"] == "executed_pass"
    assert _report_path(tmp_path, result["stages"][1]["report_ref"]).is_file()
    assert result["stages"][2]["outcome"] == "executed_fail"
    assert "no author declaration" in result["stages"][2]["detail"]
    assert all(stage["outcome"] == "executed_fail" for stage in result["stages"][2:])



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
    lane_payload["normalize"] = {"mode": "identity", "translator": "identity", "config": {}}
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
    lane_payload["normalize"] = {"mode": "identity", "translator": "identity", "config": {}}
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
    lane_payload["normalize"] = {"mode": "identity", "translator": "identity", "config": {}}
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
