from __future__ import annotations

import io
import hashlib

import json
import os
import tarfile
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from breadboard.rl.harness.hermes_tools import HermesToolRuntime, HermesToolRuntimeError
from scripts import e4_hermes_native_replay

from conformance.comparators.hermes_agent import (
    TRACE_SCHEMA_VERSION,
    compare_cases,
    project_bb_trace,
    project_supplier_case,
)

FIXTURES = Path(__file__).parent / "fixtures" / "hermes_agent"
CASES = tuple(sorted(path for path in FIXTURES.iterdir() if path.is_dir()))
PACKET_TRACE_SHA256 = {
    "H-01-normal-memory-skill-write": "fe1f1d43161e2b5df36ec9226bf2d78be8a6540493e0d20661c3d3652d42a307",
    "H-02-mixed-invalid-name": "83866a6d4e2a95470569fba1cbb8843d53471cc52e6dd97677a80260ba1fed23",
    "H-03-visible-empty-recovery": "270e7f13668ed301936e9b02800b2e810a938f99d500495900eac3606fa473be",
    "H-04-name-repair-duplicate": "aaed681f1c5f4bead25a118091197fb67120427ecefec5db0a242ea06da27c55",
    "H-05-terminal-lifecycle": "b0b801a0b7affa832aa381b40cfa140d0efb89e8e8f499a656c8c175e47bf36e",
    "H-06-request-budget-stop": "bd65f26d9003fd3bd3346ba8f198a42663b901bb0489ee75c601316fe8130c72",
}
SEALED_FIXTURES = Path(os.environ.get("BB_WORKSPACE_ROOT", Path(__file__).resolve().parents[2])) / "docs_tmp/e4_immutable_inputs/hermes"
KIT_CASES = SEALED_FIXTURES / "hermes_capture_cases.json"
PACKET = SEALED_FIXTURES / "hermes-supplier-capture-packet-rerun3.tar.gz"
TARGET_CONFIG = Path(__file__).resolve().parents[2] / "config/e4_targets/hermes_agent/2026.9.11/native-config.json"


@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_fixture_bytes_match_original_packet(case_dir: Path) -> None:
    assert hashlib.sha256((case_dir / "trace.json").read_bytes()).hexdigest() == (
        PACKET_TRACE_SHA256[case_dir.name]
    )


def test_declared_schema_overlay_advertises_only_bounded_tools(tmp_path: Path) -> None:
    config = json.loads(TARGET_CONFIG.read_bytes())
    source = deepcopy(json.loads((FIXTURES / "H-01-normal-memory-skill-write/trace.json").read_bytes())["requests"][0]["body"]["tools"])
    state = SimpleNamespace(tools=source)
    runtime = HermesToolRuntime(
        state, workspace=tmp_path, scratch=tmp_path, hermes_home=tmp_path,
        remaining=lambda: 120, schema_overlay=config["schema_overlay"],
    )
    schemas = runtime._bounded_tool_schemas()
    by_name = {schema["function"]["name"]: schema["function"] for schema in schemas}
    assert "Documents auto-extract" not in by_name["read_file"]["description"]
    terminal = by_name["terminal"]
    assert not {"background", "pty", "notify"} & terminal["parameters"]["properties"].keys()
    assert "30" in terminal["parameters"]["properties"]["timeout"]["description"]
    assert "600" not in terminal["description"] + terminal["parameters"]["properties"]["timeout"]["description"]
    source[-2]["function"]["description"] += " unauthorized"
    with pytest.raises(HermesToolRuntimeError, match="schema"):
        runtime._bounded_tool_schemas()



@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_supplier_projection_self_comparison(case_dir: Path) -> None:
    supplier = project_supplier_case(case_dir)
    replay = _bounded_replay(supplier)
    assert replay["schema_version"] == TRACE_SCHEMA_VERSION
    assert project_bb_trace(replay)["role"] == "breadboard"
    report = compare_cases(case_dir, replay)
    assert report["ok"] is True
    assert report["failed"] == 0
    assert report["gaps"][0]["id"] == "hermes-rerun3-unoverlaid-schemas"
    assert report["gaps"][0]["evidence"] == "source-derived"
    assert {"provider_deadline", "retry", "api_max_retries", "fallback"} <= set(report["source_derived_controls"])

@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_packet_workspace_delta_excludes_seed_and_supplier_trajectory(case_dir: Path) -> None:
    supplier = project_supplier_case(case_dir)
    replay = _bounded_replay(supplier)
    original = json.loads((case_dir / "trace.json").read_bytes())
    replay["file_effects"] = {
        path: item["sha256"] if item["exists"] else None
        for path, item in original["effects"]["files"].items()
    }
    assert compare_cases(case_dir, replay)["ok"] is True
    assert "AGENTS.md" not in supplier["file_effects"]
    assert "trajectory_samples.jsonl" not in supplier["file_effects"]
    assert "failed_trajectories.jsonl" not in supplier["file_effects"]


def test_effect_exclusion_is_not_chosen_by_trace_role() -> None:
    case_dir, replay = _replay("H-04-name-repair-duplicate")
    replay["file_effects"]["unrelated.txt"] = "sha256:" + "b" * 64
    replay["role"] = "supplier"
    assert compare_cases(case_dir, replay)["ok"] is False



def _bounded_replay(supplier: dict[str, Any]) -> dict[str, Any]:
    replay = deepcopy(supplier)
    overlay = json.loads(TARGET_CONFIG.read_bytes())["schema_overlay"]
    for request in replay["requests"]:
        for index, schema in enumerate(request["body"]["tools"]):
            name = schema["function"]["name"]
            if name in overlay:
                request["body"]["tools"][index] = json.loads(overlay[name]["approved_schema_json"])
    return replay


def _replay(case_name: str) -> tuple[Path, dict[str, Any]]:
    case_dir = FIXTURES / case_name
    return case_dir, _bounded_replay(project_supplier_case(case_dir))

@pytest.mark.parametrize(
    "key", ["max_tokens", "provider_deadline", "provider_timeout", "native_deadline",
            "tool_deadline", "watchdog_deadline", "watchdog", "terminal_deadline",
            "terminal_timeout", "retry", "api_max_retries", "fallback"],
)
def test_omitted_candidate_control_fails(key: str) -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    del replay["controls"][key]
    assert compare_cases(case_dir, replay)["ok"] is False


def test_explicit_retry_false_is_not_collapsed_into_retry_count() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    replay["controls"]["retry"] = False
    assert compare_cases(case_dir, replay)["ok"] is False



def _extra_tool_advertised(trace: dict[str, Any]) -> None:
    trace["requests"][0]["body"]["tools"].append(
        {"type": "function", "function": {"name": "unexpected_tool"}}
    )


def _changed_tool_order(trace: dict[str, Any]) -> None:
    tools = trace["requests"][0]["body"]["tools"]
    tools[0], tools[1] = tools[1], tools[0]


def _streaming_true(trace: dict[str, Any]) -> None:
    trace["controls"]["streaming"] = True
    trace["requests"][0]["body"]["stream"] = True


def _hidden_retry(trace: dict[str, Any]) -> None:
    extra = deepcopy(trace["requests"][-1])
    extra["index"] = trace["request_count"]
    trace["requests"].append(extra)
    trace["request_count"] += 1


def _missing_agents_context(trace: dict[str, Any]) -> None:
    trace["context"]["agents_md"] = []


def _name_repair_skipped(trace: dict[str, Any]) -> None:
    trace["tool_calls"][0]["repaired_name"] = trace["tool_calls"][0]["raw_tool_name"]
    trace["tool_calls"][0]["tool_name"] = trace["tool_calls"][0]["raw_tool_name"]


def _duplicate_executed_twice(trace: dict[str, Any]) -> None:
    trace["tool_results"].append(deepcopy(trace["tool_results"][0]))


def _visible_nudge_missing(trace: dict[str, Any]) -> None:
    trace["visible_corrections"] = []


def _ninth_request(trace: dict[str, Any]) -> None:
    extra = deepcopy(trace["requests"][-1])
    extra["index"] = trace["request_count"]
    trace["requests"].append(extra)
    trace["request_count"] += 1


def _changed_effect(trace: dict[str, Any]) -> None:
    path = next(iter(trace["file_effects"]))
    trace["file_effects"][path] = "sha256:" + "b" * 64


def _changed_termination(trace: dict[str, Any]) -> None:
    trace["termination"]["kind"] = "finished"


def _changed_max_tokens(trace: dict[str, Any]) -> None:
    trace["controls"]["max_tokens"] = 1024


def _changed_provider_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["provider_deadline"] = 60
    trace["controls"]["provider_timeout"] = 60


def _changed_native_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["native_deadline"] = 10
    trace["controls"]["tool_deadline"] = 10


def _changed_watchdog_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["watchdog_deadline"] = 15
    trace["controls"]["watchdog"] = 15


def _changed_terminal_deadline(trace: dict[str, Any]) -> None:
    trace["controls"]["terminal_deadline"] = 10
    trace["controls"]["terminal_timeout"] = 10


def _changed_fallback(trace: dict[str, Any]) -> None:
    trace["controls"]["fallback"] = True


def _changed_retry(trace: dict[str, Any]) -> None:
    trace["controls"]["api_max_retries"] = 0


@pytest.mark.parametrize(
    ("case_name", "mutation"),
    [
        ("H-01-normal-memory-skill-write", _extra_tool_advertised),
        ("H-01-normal-memory-skill-write", _changed_tool_order),
        ("H-01-normal-memory-skill-write", _streaming_true),
        ("H-01-normal-memory-skill-write", _hidden_retry),
        ("H-01-normal-memory-skill-write", _missing_agents_context),
        ("H-04-name-repair-duplicate", _name_repair_skipped),
        ("H-04-name-repair-duplicate", _duplicate_executed_twice),
        ("H-03-visible-empty-recovery", _visible_nudge_missing),
        ("H-06-request-budget-stop", _ninth_request),
        ("H-01-normal-memory-skill-write", _changed_effect),
        ("H-05-terminal-lifecycle", _changed_termination),
        ("H-01-normal-memory-skill-write", _changed_max_tokens),
        ("H-01-normal-memory-skill-write", _changed_provider_deadline),
        ("H-01-normal-memory-skill-write", _changed_native_deadline),
        ("H-01-normal-memory-skill-write", _changed_watchdog_deadline),
        ("H-01-normal-memory-skill-write", _changed_terminal_deadline),
        ("H-01-normal-memory-skill-write", _changed_fallback),
        ("H-01-normal-memory-skill-write", _changed_retry),
    ],
    ids=[
        "extra_tool_advertised",
        "changed_tool_order",
        "streaming_true",
        "hidden_retry",
        "missing_agents_context",
        "name_repair_skipped",
        "duplicate_executed_twice",
        "visible_nudge_missing",
        "ninth_request",
        "changed_effect",
        "changed_termination",
        "changed_max_tokens",
        "changed_provider_deadline",
        "changed_native_deadline",
        "changed_watchdog_deadline",
        "changed_terminal_deadline",
        "changed_fallback",
        "changed_retry",
    ],
)
def test_negative_gate_fails_comparison(
    case_name: str, mutation: Callable[[dict[str, Any]], None]
) -> None:
    case_dir, replay = _replay(case_name)
    mutation(replay)
    report = compare_cases(case_dir, replay)
    assert report["ok"] is False
    assert report["failed"] >= 1
    assert any(
        "first difference" in assertion["detail"]
        for assertion in report["assertions"]
        if assertion["status"] == "failed"
    )


def test_h02_preserves_invalid_name_error_and_h04_repairs_and_deduplicates() -> None:
    invalid = project_supplier_case(FIXTURES / "H-02-mixed-invalid-name")
    assert invalid["tool_results"][0]["is_error"] is True
    assert "NOT_A_TOOL" in invalid["tool_results"][0]["result"]

    repaired = project_supplier_case(FIXTURES / "H-04-name-repair-duplicate")
    assert len(repaired["tool_calls"]) == 1
    assert repaired["tool_calls"][0]["raw_sample"]["name"] == "WriteFile"
    assert repaired["tool_calls"][0]["raw_sample"]["arguments"] == (
        '{"path":"/opt/hermes/case/workspace/repaired.txt","content":"repaired\\n"}'
    )
    assert repaired["tool_calls"][0]["raw_tool_name"] == "WriteFile"
    assert repaired["tool_calls"][0]["repaired_name"] == "write_file"
    assert repaired["tool_calls"][0]["raw_arguments"] == (
        '{"path":"/opt/hermes/case/workspace/repaired.txt","content":"repaired\\n"}'
    )

    assert len(repaired["tool_results"]) == 1

def test_h06_stops_at_eight_requests_without_ninth() -> None:
    trace = project_supplier_case(FIXTURES / "H-06-request-budget-stop")
    assert trace["request_count"] == 8
    assert len(trace["requests"]) == 8
    assert trace["termination"] == {
        "kind": "stopped",
        "native_stop_reason": "tool_calls",
    }


def _replace_strings(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_strings(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, old, new) for key, item in value.items()}
    return value


def test_workspace_roots_are_typed_and_fail_closed() -> None:
    case_dir, supplier = _replay("H-01-normal-memory-skill-write")
    observed = _replace_strings(
        supplier,
        str(case_dir / "workspace"),
        "/lease/workspace-abc/repository",
    )
    observed["runtime"] = {"cwd": "/lease/workspace-abc/repository"}
    report = compare_cases(case_dir, observed)
    assert report["ok"] is True
    assert report["normalizations"] == ["workspace_root:<WORKSPACE>"]

    outside = deepcopy(observed)
    outside["requests"][0]["body"]["messages"][0]["content"] = "/outside/not-authorized"
    assert compare_cases(case_dir, outside)["ok"] is False

    relative_mismatch = deepcopy(observed)
    relative_mismatch["requests"][0]["body"]["messages"][0]["content"] = (
        "/lease/workspace-abc/repository/different.txt"
    )
    assert compare_cases(case_dir, relative_mismatch)["ok"] is False

    missing_runtime = deepcopy(observed)
    del missing_runtime["runtime"]
    report = compare_cases(case_dir, missing_runtime)
    assert report["ok"] is False
    assert "runtime.cwd" in report["errors"][0]


def test_comparator_rejects_name_only_tool_schemas() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    replay["requests"][0]["body"]["tools"] = [
        {"type": "function", "function": {"name": t["function"]["name"]}}
        for t in replay["requests"][0]["body"]["tools"]
    ]
    report = compare_cases(case_dir, replay)
    assert report["ok"] is False
    assert report["failed"] >= 1
    assert any("tools" in a.get("detail", "") for a in report["assertions"] if a["status"] == "failed")


def _sealed_packet() -> Path:
    assert hashlib.sha256(PACKET.read_bytes()).hexdigest() == (
        "870fc991ddf72271766b90f113921d1c0bfff3fb58090550691bac1fd9a780d6"
    )
    return PACKET


def _replay_kit(tmp_path: Path) -> Path:
    kit_root = tmp_path / "kit"
    capture = kit_root / "do2-20260923" / "hermes" / "kit"
    capture.mkdir(parents=True)
    (kit_root / "hermes_sif_compose.py").touch()
    for name in (
        "hermes_capture_breadboard.py",
        "hermes_capture_probe.py",
        "hermes_capture_receiver.py",
        "hermes_capture_cases.json",
    ):
        (capture / name).touch()
    cases_bytes = KIT_CASES.read_bytes()
    assert hashlib.sha256(cases_bytes).hexdigest() == (
        "77c8ee29682d92d18c3b5081015fa57a7109b7b5e7bd19f4814940b6d86cd362"
    )
    (capture / "hermes_capture_cases.json").write_bytes(cases_bytes)
    return kit_root

def test_replay_spec_retrieves_every_packet_case_and_receiver_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: "a" * 40)
    monkeypatch.setattr(e4_hermes_native_replay, "_prepare_bundle", lambda head, bundle: "f" * 64)
    monkeypatch.setattr(e4_hermes_native_replay, "verify_local_bundle", lambda bundle, head: None)
    packet = _sealed_packet()
    with tarfile.open(packet, "r:gz") as archive:
        manifest = archive.extractfile("hermes-supplier-capture-packet/captures/runner-result.json")
        assert manifest is not None
        packet_cases = {item["case_id"] for item in json.load(manifest)["cases"]}
    spec = e4_hermes_native_replay.do2_job_spec(
        packet, tmp_path / "out", kit_root=_replay_kit(tmp_path),
    )
    assert set(spec["expected_outputs"]["cases"]) == packet_cases
    for case in packet_cases:
        assert set(spec["expected_outputs"]["cases"][case]) == {
            "bb-trace.json", "comparator-report.json", "receiver/http-transcript.jsonl",
        }
        assert all(
            any(entry["local"] == str(tmp_path / "out" / case / name) for entry in spec["gets"])
            for name in spec["expected_outputs"]["cases"][case]
        )


@pytest.mark.parametrize("mutation", ("omit", "reorder", "rename", "alter_digest"))
def test_replay_spec_refuses_unsealed_case_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str,
) -> None:
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: "a" * 40)
    monkeypatch.setattr(e4_hermes_native_replay, "_prepare_bundle", lambda head, bundle: "f" * 64)
    monkeypatch.setattr(e4_hermes_native_replay, "verify_local_bundle", lambda bundle, head: None)
    kit = _replay_kit(tmp_path)
    cases_path = kit / "do2-20260923" / "hermes" / "kit" / "hermes_capture_cases.json"
    original = cases_path.read_bytes()
    if mutation == "alter_digest":
        cases_path.write_bytes(original + b" ")
    else:
        payload = json.loads(original)
        if mutation == "omit":
            del payload["cases"]["H-06-request-budget-stop"]
        elif mutation == "reorder":
            payload["cases"] = dict(reversed(tuple(payload["cases"].items())))
        else:
            renamed = payload["cases"].pop("H-06-request-budget-stop")
            renamed["case_id"] = "H-06-other-budget-stop"
            payload["cases"]["H-06-other-budget-stop"] = renamed
        cases_path.write_text(json.dumps(payload), encoding="utf-8")
    packet = _sealed_packet()
    with pytest.raises(ValueError, match="case|digest") as refused:
        e4_hermes_native_replay.do2_job_spec(
            packet, tmp_path / "out", kit_root=kit,
        )
    assert type(refused.value) is e4_hermes_native_replay.HermesReplaySealError
    assert refused.value.code == "kit_digest_mismatch"


def test_replay_spec_rejects_packet_with_unpinned_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: "a" * 40)
    packet = tmp_path / "packet.tar.gz"
    original = _sealed_packet()
    packet.write_bytes(original.read_bytes() + b"tampered")
    with pytest.raises(e4_hermes_native_replay.HermesReplaySealError) as refused:
        e4_hermes_native_replay.do2_job_spec(
            packet, tmp_path / "out", kit_root=_replay_kit(tmp_path),
        )
    assert refused.value.code == "packet_digest_mismatch"


def test_replay_spec_rejects_packet_order_even_with_a_new_trusted_packet_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: "a" * 40)
    packet = tmp_path / "reordered.tar.gz"
    original = _sealed_packet()
    member_path = "hermes-supplier-capture-packet/captures/runner-result.json"
    with tarfile.open(original, "r:gz") as source, tarfile.open(packet, "w:gz") as changed:
        for member in source:
            if member.isfile():
                stream = source.extractfile(member)
                assert stream is not None
                data = stream.read()
                if member.name == member_path:
                    result = json.loads(data)
                    result["cases"][0], result["cases"][1] = result["cases"][1], result["cases"][0]
                    data = json.dumps(result).encode("utf-8")
                    member.size = len(data)
                changed.addfile(member, io.BytesIO(data))
            else:
                changed.addfile(member)
    monkeypatch.setattr(
        e4_hermes_native_replay, "PACKET_SHA256",
        hashlib.sha256(packet.read_bytes()).hexdigest(),
    )
    with pytest.raises(e4_hermes_native_replay.HermesReplaySealError) as refused:
        e4_hermes_native_replay.do2_job_spec(
            packet, tmp_path / "out", kit_root=_replay_kit(tmp_path),
        )
    assert refused.value.code == "case_id_mismatch"


def test_replay_spec_records_git_head_instead_of_stale_literal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    head = "a" * 40

    def git_run(command: list[str], **kwargs: Any) -> Any:
        return SimpleNamespace(returncode=0, stdout="" if "status" in command else head + "\n", stderr="")

    monkeypatch.setattr(e4_hermes_native_replay.subprocess, "run", git_run, raising=False)
    monkeypatch.setattr(e4_hermes_native_replay, "_prepare_bundle", lambda head, bundle: "f" * 64)
    monkeypatch.setattr(e4_hermes_native_replay, "verify_local_bundle", lambda bundle, head: None)
    packet = _sealed_packet()
    spec = e4_hermes_native_replay.do2_job_spec(packet, tmp_path / "out", kit_root=_replay_kit(tmp_path))
    assert spec["source"]["commit"] == head
    assert f"'{head}'" in spec["sbatch_script"] and '--base-commit "$1"' in spec["sbatch_script"]
    assert 'SLURM_JOB_ID="$SLURM_JOB_ID"' in spec["sbatch_script"]


@pytest.mark.parametrize(
    ("git_stdout", "returncode"),
    [(" M breadboard/rl/harness/hermes_worker.py", 0), ("", 1)],
    ids=["dirty-checkout", "missing-head"],
)
def test_replay_spec_rejects_non_committed_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, git_stdout: str, returncode: int,
) -> None:
    def git_run(command: list[str], **kwargs: Any) -> Any:
        if "status" in command and returncode == 1:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=returncode, stdout=git_stdout, stderr="git error")

    monkeypatch.setattr(e4_hermes_native_replay.subprocess, "run", git_run, raising=False)
    with pytest.raises(ValueError, match="checkout|HEAD|dirty"):
        e4_hermes_native_replay.do2_job_spec(
            tmp_path / "packet", tmp_path / "out", kit_root=_replay_kit(tmp_path),
        )


def test_replay_spec_uses_checkout_and_explicit_kit_not_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    head = "b" * 40
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: head, raising=False)
    monkeypatch.setattr(e4_hermes_native_replay, "_prepare_bundle", lambda head, bundle: "f" * 64)
    monkeypatch.setattr(e4_hermes_native_replay, "verify_local_bundle", lambda bundle, head: None)
    kit_root = _replay_kit(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    packet = _sealed_packet()
    spec = e4_hermes_native_replay.do2_job_spec(packet, tmp_path / "out", kit_root=kit_root)
    uploaded = {entry["remote"].split("/")[-1]: entry["local"] for entry in spec["puts"]}
    assert Path(uploaded["hermes-head.bundle"]) == Path("/tmp/hermes-conductor-head.bundle").resolve()
    assert uploaded["hermes_capture_breadboard.py"] == str(kit_root / "do2-20260923" / "hermes" / "kit" / "hermes_capture_breadboard.py")


def test_replay_spec_rejects_missing_kit_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: "b" * 40, raising=False)
    with pytest.raises(ValueError, match="kit"):
        e4_hermes_native_replay.do2_job_spec(
            tmp_path / "packet", tmp_path / "out", kit_root=tmp_path / "missing",
        )


def test_replay_spec_rejects_missing_source_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(e4_hermes_native_replay, "_checkout_commit", lambda: "b" * 40, raising=False)
    monkeypatch.setattr(e4_hermes_native_replay, "__file__", str(tmp_path / "missing" / "scripts" / "replay.py"))
    with pytest.raises(ValueError, match="source"):
        e4_hermes_native_replay.do2_job_spec(
            tmp_path / "packet", tmp_path / "out", kit_root=_replay_kit(tmp_path),
        )


def test_mapping_trace_report_hashes_canonical_input_bytes() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    raw = json.dumps(replay, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    report = compare_cases(case_dir, replay)
    assert report["ok"] is True
    assert report["mode"] == "fixture"
    assert report["bb_trace_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert "job_id" not in report


@pytest.mark.parametrize("case_dir", CASES, ids=lambda path: path.name)
def test_unoverlaid_native_schemas_never_pass(case_dir: Path) -> None:
    report = compare_cases(case_dir, project_supplier_case(case_dir))
    assert not report["ok"]
    assert any(gap["id"] == "hermes-rerun3-unoverlaid-schemas" for gap in report["gaps"])


def test_request_body_preserves_absent_stream_key() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    assert "stream" not in replay["requests"][0]["body"]
    replay["requests"][0]["body"]["stream"] = False
    assert not compare_cases(case_dir, replay)["ok"]


def test_json_request_comparison_only_ignores_object_member_order() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    original = replay["requests"][0]["body"]
    replay["requests"][0]["body"] = dict(reversed(list(original.items())))
    assert compare_cases(case_dir, replay)["ok"]

    missing = deepcopy(replay)
    missing["requests"][0]["body"].pop("max_tokens")
    assert not compare_cases(case_dir, missing)["ok"]

    numeric = deepcopy(replay)
    numeric["requests"][0]["body"]["max_tokens"] = 2048.0
    assert not compare_cases(case_dir, numeric)["ok"]

    ordered = deepcopy(replay)
    ordered["requests"][0]["body"]["messages"].reverse()
    assert not compare_cases(case_dir, ordered)["ok"]

def test_supplier_and_candidate_extra_body_members_both_fail(tmp_path: Path) -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    supplier_case = tmp_path / case_dir.name
    shutil.copytree(case_dir, supplier_case)
    assert compare_cases(supplier_case, replay)["ok"]
    trace_path = supplier_case / "trace.json"
    supplier = json.loads(trace_path.read_bytes())
    supplier["requests"][0]["body"]["parallel_tool_calls"] = True
    trace_path.write_text(json.dumps(supplier), encoding="utf-8")
    assert not compare_cases(supplier_case, replay)["ok"]

    replay["requests"][0]["body"]["parallel_tool_calls"] = True
    assert not compare_cases(case_dir, replay)["ok"]


def _receiver_transcript(tmp_path: Path, replay: dict[str, Any]) -> Path:
    path = tmp_path / "http-transcript.jsonl"
    rows = (
        {"index": index, "body": request["body"]}
        for index, request in enumerate(replay["requests"])
    )
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_installed_receiver_observation_rejects_one_byte_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "456789")
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    trace_path = tmp_path / "bb-trace.json"
    trace_path.write_text(json.dumps(replay), encoding="utf-8")
    assert not compare_cases(case_dir, trace_path, installed_replay=True)["ok"]
    transcript = _receiver_transcript(tmp_path, replay)
    assert compare_cases(
        case_dir, trace_path, installed_replay=True, receiver_transcript=transcript,
    )["ok"]
    rows = transcript.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["body"]["messages"][0]["content"] += "x"
    rows[0] = json.dumps(first, ensure_ascii=False)
    transcript.write_text("\n".join(rows) + "\n", encoding="utf-8")
    report = compare_cases(
        case_dir, trace_path, installed_replay=True, receiver_transcript=transcript,
    )
    assert not report["ok"]
    assert any("receiver" in item["assertion_id"] for item in report["assertions"] if item["status"] == "failed")


def test_installed_job_id_must_match_recorded_slurm_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    transcript = _receiver_transcript(tmp_path, replay)
    trace_path = tmp_path / "bb-trace.json"
    trace_path.write_text(json.dumps(replay), encoding="utf-8")
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    assert not compare_cases(case_dir, trace_path, installed_replay=True, job_id="456789")["ok"]
    replay["job_id"] = "456789"
    trace_path.write_text(json.dumps(replay), encoding="utf-8")
    assert not compare_cases(case_dir, trace_path, installed_replay=True)["ok"]
    monkeypatch.setenv("SLURM_JOB_ID", "456789")
    assert compare_cases(case_dir, trace_path, installed_replay=True, job_id="456789", receiver_transcript=transcript)["ok"]
    monkeypatch.setenv("SLURM_JOB_ID", "other-job")
    assert not compare_cases(case_dir, trace_path, installed_replay=True, job_id="456789")["ok"]


def test_installed_replay_without_job_id_fails_even_when_trace_matches(tmp_path: Path) -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    trace_path = tmp_path / "bb-trace.json"
    trace_path.write_text(json.dumps(replay), encoding="utf-8")
    report = compare_cases(case_dir, trace_path, installed_replay=True)
    assert report["ok"] is False
    assert any("SLURM_JOB_ID" in error for error in report["errors"])


def test_path_trace_report_hashes_original_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "456789")
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    raw = json.dumps(replay, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    trace_path = tmp_path / "bb-trace.json"
    trace_path.write_bytes(raw)
    report = compare_cases(
        case_dir, trace_path, installed_replay=True, job_id="456789",
        receiver_transcript=_receiver_transcript(tmp_path, replay),
    )
    assert report["ok"] is True
    assert report["mode"] == "installed-replay"
    assert report["job_id"] == "456789"
    assert report["bb_trace_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_installed_replay_requires_persisted_trace() -> None:
    case_dir, replay = _replay("H-01-normal-memory-skill-write")
    report = compare_cases(case_dir, replay, installed_replay=True, job_id="456789")
    assert report["ok"] is False
    assert any("path" in error for error in report["errors"])
