from __future__ import annotations

import hashlib
import math
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import breadboard_engine.e4_trace_parity as parity
from breadboard_engine.e4_trace_parity import (
    E4ParityError,
    NormalizationRule,
    TemporaryPathRoots,
    TraceMismatch,
    build_e4_parity_report,
    canonical_json_bytes,
    compare_e4_traces,
    json_sha256,
    validate_e4_trace,
    validate_workspace_snapshot,
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
            "normalized_sha256": hashlib.sha256(b"<timestamp>").hexdigest(),
        },
        {
            "pointer_sha256": hashlib.sha256(b"/process/output").hexdigest(),
            "pointer_depth": 2,
            "kind": "temporary_path",
            "normalized_sha256": hashlib.sha256(
                b"<tmp>/session/result.json"
            ).hexdigest(),
        },
        {
            "pointer_sha256": hashlib.sha256(b"/process/pid").hexdigest(),
            "pointer_depth": 2,
            "kind": "pid",
            "normalized_sha256": hashlib.sha256(b"<pid>").hexdigest(),
        },
    ]
    assert b"session/result.json" not in canonical_json_bytes(
        [field.as_dict() for field in comparison.normalized_fields]
    )


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

    root_relative = compare_e4_traces(
        {"path": "/tmp/reference/session"},
        {"path": r"\tmp\clone\session"},
        rules=(NormalizationRule("/path", "temporary_path"),),
        temporary_roots=TemporaryPathRoots("/tmp/reference", "/tmp/clone"),
    )
    assert not root_relative.matches
    assert "Windows root-relative" in root_relative.mismatches[0].reason

    mixed_separators = compare_e4_traces(
        {"path": "/tmp/reference/session"},
        {"path": r"/tmp/clone\session"},
        rules=(NormalizationRule("/path", "temporary_path"),),
        temporary_roots=TemporaryPathRoots("/tmp/reference", "/tmp/clone"),
    )
    assert not mixed_separators.matches
    assert "mix path separators" in mixed_separators.mismatches[0].reason

    trailing_separators = compare_e4_traces(
        {"path": "/tmp/reference/session/"},
        {"path": "/tmp/clone/session/"},
        rules=(NormalizationRule("/path", "temporary_path"),),
        temporary_roots=TemporaryPathRoots("/tmp/reference", "/tmp/clone"),
    )
    assert trailing_separators.matches

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

    structural_normalization_cases = (
        (
            {"meta": {"timestamp": "2026-08-29T06:00:00Z"}},
            {},
            "/meta/timestamp",
            "field is missing from clone",
        ),
        (
            {"timestamps": [0, 1]},
            {"timestamps": [0]},
            "/timestamps/1",
            "array lengths differ",
        ),
        (
            {"meta": {"timestamp": "2026-08-29T06:00:00Z"}},
            {"meta": []},
            "/meta/timestamp",
            "JSON types differ",
        ),
    )
    for reference_value, clone_value, pointer, reason in structural_normalization_cases:
        structural_mismatch = compare_e4_traces(
            reference_value,
            clone_value,
            rules=(NormalizationRule(pointer, "timestamp"),),
        )
        assert not structural_mismatch.matches
        assert structural_mismatch.mismatches[0].reason == reason

    with pytest.raises(E4ParityError, match="did not match trace fields"):
        compare_e4_traces(
            {"timestamps": [0, 1]},
            {"timestamps": [0]},
            rules=(NormalizationRule("/timestamps/99", "timestamp"),),
        )

    with pytest.raises(E4ParityError, match="did not match trace fields"):
        compare_e4_traces(
            {"meta": 1},
            {},
            rules=(NormalizationRule("/meta/timestamp", "timestamp"),),
        )

    with pytest.raises(E4ParityError, match="normalization pointers overlap"):
        compare_e4_traces(
            {"meta": {"timestamp": "2026-08-29T06:00:00Z"}},
            {"meta": {"timestamp": "2026-08-29T06:00:01Z"}},
            rules=(
                NormalizationRule("/meta", "timestamp"),
                NormalizationRule("/meta/timestamp", "timestamp"),
            ),
        )

    large_timestamp = 1 << 4000
    large_timestamp_comparison = compare_e4_traces(
        {"timestamp": large_timestamp},
        {"timestamp": large_timestamp + 1},
        rules=(NormalizationRule("/timestamp", "timestamp"),),
    )
    assert large_timestamp_comparison.matches
    assert "timezone-aware ISO-8601" in unzoned_timestamp.mismatches[0].reason

    with pytest.raises(E4ParityError, match="filesystem root"):
        TemporaryPathRoots("/", "/tmp/clone")
    with pytest.raises(E4ParityError, match="unsafe component"):
        TemporaryPathRoots("/tmp/../reference", "/tmp/clone")
    with pytest.raises(E4ParityError, match="printable text"):
        TemporaryPathRoots("/tmp/reference\u0000root", "/tmp/clone")
    unc_comparison = compare_e4_traces(
        {"path": r"\\server\share\reference\session\result.json" + "\\"},
        {"path": r"\\server\share\clone\session\result.json" + "\\"},
        rules=(NormalizationRule("/path", "temporary_path"),),
        temporary_roots=TemporaryPathRoots(
            r"\\server\share\reference",
            r"\\server\share\clone",
        ),
    )
    assert unc_comparison.matches
    with pytest.raises(E4ParityError, match="filesystem root"):
        TemporaryPathRoots(
            r"\\server\share",
            r"\\server\share\clone",
        )
    with pytest.raises(E4ParityError, match="device namespace"):
        TemporaryPathRoots(
            r"\\?\UNC\server\share\reference",
            r"\\server\share\clone",
        )
    assert type_mismatch.mismatches[0].reason == "JSON types differ"
    with pytest.raises(E4ParityError, match="Windows root-relative"):
        TemporaryPathRoots(
            r"\temp\reference",
            r"\\server\share\clone",
        )


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

    with pytest.raises(E4ParityError, match="integer exceeds 4096 bits"):
        canonical_json_bytes({"integer": 1 << 4097})


def test_trace_comparison_bounds_mismatch_evidence() -> None:
    reference = {f"field_{index}": 0 for index in range(10_001)}
    clone = {f"field_{index}": 1 for index in range(10_001)}

    comparison = compare_e4_traces(reference, clone)

    assert not comparison.matches
    assert len(comparison.mismatches) == 10_001
    assert comparison.mismatches[-1].reason == "mismatch count exceeds 10000"
    root_mismatch = compare_e4_traces(True, False).mismatches[0]
    assert root_mismatch.pointer == ""
    assert (
        TraceMismatch(pointer="/field", reason="test", reference=0, clone=1).pointer
        == "/field"
    )

    long_key = "x" * 2048
    amplified = compare_e4_traces(
        {long_key: {f"field_{index}": 0 for index in range(10_001)}},
        {long_key: {f"field_{index}": 1 for index in range(10_001)}},
    )
    assert len(amplified.mismatches) == 10_001
    assert sum(len(item.pointer) for item in amplified.mismatches) < 1_000_000
    expected_pointer = f"/{long_key}/field_0"
    assert (
        amplified.mismatches[0].as_dict()["pointer_sha256"]
        == hashlib.sha256(expected_pointer.encode("utf-8")).hexdigest()
    )
    reference["timestamp"] = "2026-08-29T06:00:00Z"
    clone["timestamp"] = "2026-08-29T06:00:01Z"
    normalized = compare_e4_traces(
        reference,
        clone,
        rules=(NormalizationRule("/timestamp", "timestamp"),),
    )
    assert len(normalized.mismatches) == 10_001
    assert [field.pointer for field in normalized.normalized_fields] == ["/timestamp"]

    with pytest.raises(E4ParityError, match="did not match trace fields"):
        compare_e4_traces(
            reference,
            clone,
            rules=(NormalizationRule("/zzz", "timestamp"),),
        )


def test_workspace_snapshot_requires_directory_ancestors() -> None:
    file_entry = {
        "kind": "file",
        "mode": 0o644,
        "bytes": 0,
        "sha256": hashlib.sha256(b"").hexdigest(),
    }
    malformed_entries = (
        [{"path": "dir/file", **file_entry}],
        [
            {"path": "dir", **file_entry},
            {"path": "dir/file", **file_entry},
        ],
    )

    for entries in malformed_entries:
        with pytest.raises(E4ParityError, match="must exist as a directory"):
            validate_workspace_snapshot(
                {
                    "schema_version": "bb.e4.workspace_snapshot.v1",
                    "entries": entries,
                }
            )


def test_workspace_entry_limit_is_reachable_before_json_node_limit() -> None:
    snapshot = {
        "schema_version": "bb.e4.workspace_snapshot.v1",
        "entries": [
            {"path": f"f{index:05}", "kind": "directory", "mode": 0o755}
            for index in range(16_001)
        ],
    }

    with pytest.raises(E4ParityError, match="entry count exceeds 16000"):
        validate_workspace_snapshot(snapshot)


def test_workspace_snapshot_bounds_directory_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeScandir:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return (SimpleNamespace(name=f"entry-{index}") for index in range(16_001))

    def fake_scandir(_directory_fd: int):
        return FakeScandir()

    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {fake_scandir})

    with pytest.raises(E4ParityError, match="entry count exceeds 16000"):
        workspace_snapshot(tmp_path)


def test_workspace_snapshot_reserves_pending_directory_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "a").mkdir(parents=True)
    (workspace / "a" / "nested").write_text("nested", encoding="utf-8")
    (workspace / "b").write_text("sibling", encoding="utf-8")
    real_stat = os.stat
    inspected: list[object] = []

    def recording_stat(path, *args, **kwargs):
        inspected.append(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(parity, "_MAX_WORKSPACE_ENTRIES", 2)
    monkeypatch.setattr(os, "stat", recording_stat)
    monkeypatch.setattr(
        os,
        "supports_dir_fd",
        (os.supports_dir_fd - {real_stat}) | {recording_stat},
    )

    with pytest.raises(E4ParityError, match="entry count exceeds 2"):
        workspace_snapshot(workspace)
    assert "nested" not in inspected


def test_workspace_snapshot_rejects_changed_final_directory_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "stable").write_text("stable", encoding="utf-8")
    scans = iter((("stable",), ("changed",)))

    class FakeScandir:
        def __init__(self, names: tuple[str, ...]) -> None:
            self._names = names

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return (SimpleNamespace(name=name) for name in self._names)

    def fake_scandir(_directory_fd: int):
        return FakeScandir(next(scans))

    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {fake_scandir})

    with pytest.raises(E4ParityError, match="directory changed during snapshot"):
        workspace_snapshot(workspace)


def test_workspace_snapshot_rejects_oversized_name_before_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeScandir:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter((SimpleNamespace(name="x" * 256),))

    def fake_scandir(_directory_fd: int):
        return FakeScandir()

    def forbidden_stat(*_args, **_kwargs):
        raise AssertionError("oversized workspace names must fail before stat")

    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setattr(os, "stat", forbidden_stat)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {fake_scandir})
    monkeypatch.setattr(
        os,
        "supports_dir_fd",
        (os.supports_dir_fd - {os.stat}) | {forbidden_stat},
    )

    with pytest.raises(E4ParityError, match="path exceeds admitted bounds"):
        workspace_snapshot(tmp_path)


def test_workspace_snapshot_translates_file_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.py").write_text("pass\n", encoding="utf-8")

    def fail_read(_fd: int, _size: int) -> bytes:
        raise OSError("simulated EIO")

    monkeypatch.setattr(os, "read", fail_read)

    with pytest.raises(E4ParityError, match="could not read workspace file source.py"):
        workspace_snapshot(workspace)


def test_workspace_snapshot_translates_directory_fstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    child = workspace / "child"
    child.mkdir(parents=True)
    root_inode = os.stat(workspace).st_ino
    real_fstat = os.fstat

    def fail_child_directory(fd: int):
        opened_stat = real_fstat(fd)
        if stat.S_ISDIR(opened_stat.st_mode) and opened_stat.st_ino != root_inode:
            raise OSError("simulated ESTALE")
        return opened_stat

    monkeypatch.setattr(os, "fstat", fail_child_directory)

    with pytest.raises(
        E4ParityError,
        match="could not inspect workspace directory child",
    ):
        workspace_snapshot(workspace)


def test_workspace_snapshot_translates_root_fstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_root(_fd: int):
        raise OSError("simulated EIO")

    monkeypatch.setattr(os, "fstat", fail_root)

    with pytest.raises(E4ParityError, match="could not inspect workspace root"):
        workspace_snapshot(workspace)


def test_workspace_snapshot_translates_root_close_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_close = os.close

    def close_then_fail(fd: int) -> None:
        real_close(fd)
        raise OSError("simulated EIO")

    monkeypatch.setattr(os, "close", close_then_fail)

    with pytest.raises(E4ParityError, match="could not close workspace root"):
        workspace_snapshot(workspace)


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
    with pytest.raises(
        TypeError, match="normalization_rules must be an exact list or tuple"
    ):
        build_e4_parity_report(
            target_id="pi@0.57.1",
            target_descriptor_sha256=SHA256,
            target_config_sha256=SHA256,
            upstream_identity=upstream_identity,
            fixture_id="surface.catalog.v1",
            fixture_sha256=SHA256,
            engine_commit=GIT_COMMIT,
            built_package_sha256=SHA256,
            reference_trace=reference,
            clone_trace=clone,
            normalization_rules=iter(()),
        )
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
