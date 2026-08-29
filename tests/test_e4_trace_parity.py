from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from breadboard_engine.e4_trace_parity import (
    E4ParityError,
    NormalizationRule,
    TemporaryPathRoots,
    build_e4_parity_report,
    canonical_json_bytes,
    compare_e4_traces,
    json_sha256,
    validate_e4_trace,
    workspace_snapshot,
)


SHA256 = "0" * 64
GIT_COMMIT = "a" * 40


def _trace(
    events: list[dict[str, object]],
    *,
    target_id: str = "pi@0.57.1",
    fixture_id: str = "surface.catalog.v1",
) -> dict[str, object]:
    return {
        "schema_version": "bb.e4.execution_trace.v1",
        "target_id": target_id,
        "fixture_id": fixture_id,
        "events": events,
        "provider_requests": [],
        "provider_responses": [],
        "process": {
            "stdout_base64": "",
            "stderr_base64": "",
            "exit_code": 0,
            "signal": None,
        },
        "workspace": {
            "schema_version": "bb.e4.workspace_snapshot.v1",
            "entries": [],
        },
        "terminal": {"reason": "completed", "result": None, "error": None},
    }


def test_trace_comparison_is_exact_by_default() -> None:
    reference = {
        "events": [
            {"kind": "message", "text": "ready"},
            {"kind": "tool", "id": "call_1", "arguments": {"path": "a.txt"}},
        ],
        "terminal": {"reason": "completed", "exit_code": 0},
    }
    assert compare_e4_traces(reference, reference).matches

    clone = {
        "events": [
            {"kind": "message", "text": "ready"},
            {"kind": "tool", "id": "call_2", "arguments": {"path": "a.txt"}},
            {"kind": "unexpected"},
        ],
        "terminal": {"reason": "completed", "exit_code": 0},
        "extra": True,
    }
    comparison = compare_e4_traces(reference, clone)

    assert not comparison.matches
    assert [(item.pointer, item.reason) for item in comparison.mismatches] == [
        ("/extra", "unexpected clone field"),
        ("/events", "array lengths differ"),
        ("/events/1/id", "values differ"),
    ]
    assert comparison.normalized_fields == ()

    signed_zero = compare_e4_traces({"value": -0.0}, {"value": 0.0})
    assert not signed_zero.matches
    assert signed_zero.mismatches[0].pointer == "/value"


def test_only_named_timestamp_pid_and_temp_paths_can_be_normalized() -> None:
    reference = {
        "event": {"timestamp": "2026-08-29T06:00:00Z"},
        "process": {
            "pid": 1024,
            "output": "/private/tmp/reference/session/result.json",
        },
    }
    clone = {
        "event": {"timestamp": "2026-08-29T06:00:01+00:00"},
        "process": {
            "pid": 2048,
            "output": "/tmp/clone/session/result.json",
        },
    }
    comparison = compare_e4_traces(
        reference,
        clone,
        rules=(
            NormalizationRule("/event/timestamp", "timestamp"),
            NormalizationRule("/process/pid", "pid"),
            NormalizationRule("/process/output", "temporary_path"),
        ),
        temporary_roots=TemporaryPathRoots("/private/tmp/reference", "/tmp/clone"),
    )

    assert comparison.matches
    assert [field.as_dict() for field in comparison.normalized_fields] == [
        {
            "pointer_sha256": hashlib.sha256(b"/event/timestamp").hexdigest(),
            "pointer_depth": 2,
            "kind": "timestamp",
            "normalized": "<timestamp>",
        },
        {
            "pointer_sha256": hashlib.sha256(b"/process/output").hexdigest(),
            "pointer_depth": 2,
            "kind": "temporary_path",
            "normalized": "<tmp>/session/result.json",
        },
        {
            "pointer_sha256": hashlib.sha256(b"/process/pid").hexdigest(),
            "pointer_depth": 2,
            "kind": "pid",
            "normalized": "<pid>",
        },
    ]


def test_normalization_policy_fails_closed() -> None:
    with pytest.raises(E4ParityError, match="non-root JSON pointer"):
        NormalizationRule("", "timestamp")
    with pytest.raises(E4ParityError, match="timestamp, pid, or temporary_path"):
        NormalizationRule("/event/id", "identifier")
    with pytest.raises(E4ParityError, match="duplicate normalization pointer"):
        compare_e4_traces(
            {"timestamp": 1},
            {"timestamp": 2},
            rules=(
                NormalizationRule("/timestamp", "timestamp"),
                NormalizationRule("/timestamp", "timestamp"),
            ),
        )
    with pytest.raises(E4ParityError, match="invalid escape"):
        NormalizationRule("/bad~2pointer", "timestamp")
    with pytest.raises(E4ParityError, match="exceeds 1024 UTF-8 bytes"):
        NormalizationRule("/" + ("a" * 1024), "timestamp")
    with pytest.raises(E4ParityError, match="did not match trace fields"):
        compare_e4_traces(
            {"events": []},
            {"events": []},
            rules=(NormalizationRule("/missing", "timestamp"),),
        )
    with pytest.raises(E4ParityError, match="requires both trace roots"):
        compare_e4_traces(
            {"path": "/tmp/a"},
            {"path": "/tmp/a"},
            rules=(NormalizationRule("/path", "temporary_path"),),
        )

    comparison = compare_e4_traces(
        {"path": "/tmp/reference/a"},
        {"path": "/outside/a"},
        rules=(NormalizationRule("/path", "temporary_path"),),
        temporary_roots=TemporaryPathRoots("/tmp/reference", "/tmp/clone"),
    )
    assert not comparison.matches
    assert "outside its admitted root" in comparison.mismatches[0].reason

    type_mismatch = compare_e4_traces(
        {"timestamp": "2026-08-29T06:00:00Z"},
        {"timestamp": 1},
        rules=(NormalizationRule("/timestamp", "timestamp"),),
    )
    assert not type_mismatch.matches
    assert type_mismatch.normalized_fields == ()

    unzoned_timestamp = compare_e4_traces(
        {"timestamp": "2026-08-29"},
        {"timestamp": "2026-08-30"},
        rules=(NormalizationRule("/timestamp", "timestamp"),),
    )
    assert not unzoned_timestamp.matches
    assert "timezone-aware ISO-8601" in unzoned_timestamp.mismatches[0].reason

    with pytest.raises(E4ParityError, match="filesystem root"):
        TemporaryPathRoots("/", "/tmp/clone")
    with pytest.raises(E4ParityError, match="unsafe component"):
        TemporaryPathRoots("/tmp/../reference", "/tmp/clone")
    with pytest.raises(E4ParityError, match="printable text"):
        TemporaryPathRoots("/tmp/reference\u0000root", "/tmp/clone")
    assert type_mismatch.mismatches[0].reason == "JSON types differ"


def test_trace_values_must_be_closed_json() -> None:
    with pytest.raises(E4ParityError, match="non-JSON tuple"):
        canonical_json_bytes({"events": ()})
    with pytest.raises(E4ParityError, match="non-finite number"):
        canonical_json_bytes({"usage": math.nan})
    with pytest.raises(E4ParityError, match="non-string object key"):
        canonical_json_bytes({1: "invalid"})

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(E4ParityError, match="cyclic array"):
        compare_e4_traces(cyclic, cyclic)

    nested: object = None
    for _ in range(130):
        nested = [nested]
    with pytest.raises(E4ParityError, match="JSON depth exceeds"):
        canonical_json_bytes(nested)

    with pytest.raises(E4ParityError, match=r"JSON .*byte size exceeds"):
        canonical_json_bytes({"escaped": "\x00" * (11 * 1024 * 1024)})


def test_trace_comparison_bounds_mismatch_evidence() -> None:
    reference = {f"field_{index}": 0 for index in range(10_001)}
    clone = {f"field_{index}": 1 for index in range(10_001)}

    comparison = compare_e4_traces(reference, clone)

    assert not comparison.matches
    assert len(comparison.mismatches) == 10_001
    assert comparison.mismatches[-1].reason == "mismatch count exceeds 10000"


def test_workspace_snapshot_captures_bytes_modes_and_links(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    (workspace / "src.txt").write_text("sibling", encoding="utf-8")
    script = source / "run.sh"
    script.write_bytes(b"#!/bin/sh\necho exact\n")
    script.chmod(0o750)
    link = workspace / "run-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not traverse", encoding="utf-8")
    escape = workspace / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError:
        escape = None
    try:
        link.symlink_to("src/run.sh")
    except OSError:
        link = None

    try:
        first = workspace_snapshot(workspace)
    except E4ParityError as exc:
        if "secure descriptor-relative workspace traversal is unavailable" in str(exc):
            pytest.skip(str(exc))
        raise
    assert [entry["path"] for entry in first["entries"]] == sorted(
        entry["path"] for entry in first["entries"]
    )
    entries = {entry["path"]: entry for entry in first["entries"]}
    assert entries["src"]["kind"] == "directory"
    assert entries["src/run.sh"] == {
        "path": "src/run.sh",
        "kind": "file",
        "mode": script.stat().st_mode & 0o7777,
        "bytes": 21,
        "sha256": "be34b9e32c72a56a86c345db89e2971977580eb6470cda01f6f2ccbc389c0618",
    }
    if escape is not None:
        assert entries["escape"]["kind"] == "symlink"
        assert not any(
            entry["path"].startswith("escape/") for entry in first["entries"]
        )
    if link is not None:
        assert entries["run-link"]["kind"] == "symlink"
        assert entries["run-link"]["target"] == "src/run.sh"

    script.write_bytes(b"#!/bin/sh\necho changed\n")
    second = workspace_snapshot(workspace)
    assert json_sha256(first) != json_sha256(second)

    root_alias = tmp_path / "workspace-alias"
    try:
        root_alias.symlink_to(workspace, target_is_directory=True)
    except OSError:
        root_alias = None
    if root_alias is not None:
        with pytest.raises(E4ParityError, match="secure directory"):
            workspace_snapshot(root_alias)

    hardlink_workspace = tmp_path / "hardlink-workspace"
    hardlink_workspace.mkdir()
    try:
        (hardlink_workspace / "linked-secret").hardlink_to(outside / "secret.txt")
    except OSError:
        hardlink_workspace = None
    if hardlink_workspace is not None:
        with pytest.raises(E4ParityError, match="changed during snapshot"):
            workspace_snapshot(hardlink_workspace)


def test_execution_trace_schema_is_closed_and_canonical() -> None:
    trace = _trace([{"kind": "done"}])
    validate_e4_trace(trace)

    invalid_base64 = _trace([{"kind": "test"}])
    invalid_base64["process"]["stdout_base64"] = "%"
    with pytest.raises(E4ParityError, match="canonical base64"):
        validate_e4_trace(invalid_base64)

    incomplete = _trace([])
    incomplete.pop("terminal")
    with pytest.raises(E4ParityError, match="exact execution-trace fields"):
        validate_e4_trace(incomplete)

    empty = _trace([])
    with pytest.raises(E4ParityError, match="events must not be empty"):
        validate_e4_trace(empty)

    ambiguous_process = _trace([{"kind": "test"}])
    ambiguous_process["process"]["signal"] = 9
    with pytest.raises(E4ParityError, match="exactly one"):
        validate_e4_trace(ambiguous_process)

    oversized_identity = _trace([{"kind": "test"}])
    oversized_identity["target_id"] = "x" * 257
    with pytest.raises(E4ParityError, match="exceeds 256 UTF-8 bytes"):
        validate_e4_trace(oversized_identity)


def test_parity_report_binds_every_required_identity() -> None:
    reference = _trace([{"kind": "done"}])
    clone = _trace([{"kind": "done"}])
    upstream_identity = {
        "package": "@mariozechner/pi-coding-agent",
        "version": "0.57.1",
        "tarball_sha256": "8" * 64,
    }
    reference_sha256 = json_sha256(reference)

    invalid_workspace = _trace([{"kind": "test"}])
    invalid_workspace["workspace"]["entries"] = [
        {
            "path": "result.txt",
            "kind": "file",
            "mode": 0o644,
            "bytes": 1,
            "sha256": "invalid",
        }
    ]
    with pytest.raises(E4ParityError, match="file size and digest"):
        validate_e4_trace(invalid_workspace)

    invalid_terminal = _trace([{"kind": "test"}])
    invalid_terminal["terminal"]["unexpected"] = True

    invalid_workspace_path = _trace([{"kind": "test"}])
    invalid_workspace_path["workspace"]["entries"] = [
        {"path": "bad\u0000path", "kind": "directory", "mode": 0o755}
    ]
    with pytest.raises(E4ParityError, match="safe and relative"):
        validate_e4_trace(invalid_workspace_path)
    with pytest.raises(E4ParityError, match="exact terminal fields"):
        validate_e4_trace(invalid_terminal)

    oversized_workspace_path = _trace([{"kind": "test"}])
    oversized_workspace_path["workspace"]["entries"] = [
        {"path": "x" * 256, "kind": "directory", "mode": 0o755}
    ]
    with pytest.raises(E4ParityError, match="safe and relative"):
        validate_e4_trace(oversized_workspace_path)

    report = build_e4_parity_report(
        target_id="pi@0.57.1",
        target_descriptor_sha256="1" * 64,
        target_config_sha256="2" * 64,
        upstream_identity=upstream_identity,
        fixture_id="surface.catalog.v1",
        fixture_sha256="3" * 64,
        engine_commit=GIT_COMMIT,
        built_package_sha256="4" * 64,
        reference_trace=reference,
        clone_trace=clone,
    )

    assert report == {
        "schema_version": "bb.e4.parity_report.v2",
        "target_id": "pi@0.57.1",
        "target_descriptor_sha256": "1" * 64,
        "target_config_sha256": "2" * 64,
        "upstream_identity_sha256": json_sha256(upstream_identity),
        "fixture_id": "surface.catalog.v1",
        "fixture_sha256": "3" * 64,
        "engine_commit": GIT_COMMIT,
        "built_package_sha256": "4" * 64,
        "reference_trace_sha256": json_sha256(reference),
        "clone_trace_sha256": json_sha256(clone),
        "normalization_rules": [],
        "status": "passed",
        "normalized_fields": [],
        "mismatches": [],
    }
    reference["events"][0]["kind"] = "mutated"
    assert report["reference_trace_sha256"] == reference_sha256
    identity_sha256 = report["upstream_identity_sha256"]
    reference["events"][0]["kind"] = "done"
    upstream_identity["version"] = "mutated"
    assert report["upstream_identity_sha256"] == identity_sha256
    upstream_identity["version"] = "0.57.1"

    failed_report = build_e4_parity_report(
        target_id="pi@0.57.1",
        target_descriptor_sha256="1" * 64,
        target_config_sha256="2" * 64,
        upstream_identity=upstream_identity,
        fixture_id="surface.catalog.v1",
        fixture_sha256="3" * 64,
        engine_commit=GIT_COMMIT,
        built_package_sha256="4" * 64,
        reference_trace=reference,
        clone_trace=_trace([{"kind": "error"}]),
    )
    assert failed_report["status"] == "failed"
    assert failed_report["mismatches"] == [
        {
            "pointer_sha256": hashlib.sha256(b"/events/0/kind").hexdigest(),
            "pointer_depth": 3,
            "reason": "values differ",
            "reference_type": "str",
            "clone_type": "str",
        }
    ]

    secret_pointer = "/events/0/files/~1private~1tmp~1secret"
    secret_reference = _trace([{"kind": "done", "files": {"/private/tmp/secret": "a"}}])
    secret_clone = _trace([{"kind": "done", "files": {"/private/tmp/secret": "b"}}])
    secret_report = build_e4_parity_report(
        target_id="pi@0.57.1",
        target_descriptor_sha256="1" * 64,
        target_config_sha256="2" * 64,
        upstream_identity=upstream_identity,
        fixture_id="surface.catalog.v1",
        fixture_sha256="3" * 64,
        engine_commit=GIT_COMMIT,
        built_package_sha256="4" * 64,
        reference_trace=secret_reference,
        clone_trace=secret_clone,
    )
    secret_report_bytes = canonical_json_bytes(secret_report)
    assert b"/private/tmp/secret" not in secret_report_bytes
    assert b"~1private~1tmp~1secret" not in secret_report_bytes
    assert (
        secret_report["mismatches"][0]["pointer_sha256"]
        == hashlib.sha256(secret_pointer.encode("utf-8")).hexdigest()
    )

    with pytest.raises(E4ParityError, match="lowercase SHA-256"):
        build_e4_parity_report(
            target_id="pi@0.57.1",
            target_descriptor_sha256=SHA256.upper().replace("0", "A", 1),
            target_config_sha256=SHA256,
            upstream_identity=upstream_identity,
            fixture_id="surface.catalog.v1",
            fixture_sha256=SHA256,
            engine_commit=GIT_COMMIT,
            built_package_sha256=SHA256,
            reference_trace=reference,
            clone_trace=clone,
        )
    with pytest.raises(E4ParityError, match="full lowercase Git object ID"):
        build_e4_parity_report(
            target_id="pi@0.57.1",
            target_descriptor_sha256=SHA256,
            target_config_sha256=SHA256,
            upstream_identity=upstream_identity,
            fixture_id="surface.catalog.v1",
            fixture_sha256=SHA256,
            engine_commit="short",
            built_package_sha256=SHA256,
            reference_trace=reference,
            clone_trace=clone,
        )
