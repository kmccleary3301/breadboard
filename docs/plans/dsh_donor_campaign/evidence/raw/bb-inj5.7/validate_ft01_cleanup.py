"""Validate FT-01 owned engine and installed-client cleanup identities."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


class CleanupValidationError(ValueError):
    """Raised when finalization evidence cannot prove owned-process cleanup."""


_CASES = ("replacement-before-submit", "permission-after-dispatch")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanupValidationError(message)


def _client_process(value: object, *, require_dead: bool) -> tuple[int, str]:
    _require(isinstance(value, Mapping), "client_process must be an object")
    _require(
        set(value) == {"pid", "start_token", "lifecycle_proof", "terminal_proof", "dead"},
        "client_process has an unexpected schema",
    )
    pid = value["pid"]
    token = value["start_token"]
    _require(isinstance(pid, int) and not isinstance(pid, bool) and pid > 0, "invalid client PID")
    _require(isinstance(token, str) and bool(token), "invalid client start token")
    lifecycle = value["lifecycle_proof"]
    _require(isinstance(lifecycle, Mapping), "missing client lifecycle proof")
    _require(set(lifecycle) == {"authenticated", "evidence"}, "invalid client lifecycle proof")
    _require(lifecycle["authenticated"] is True, "client lifecycle proof is unauthenticated")
    _require(isinstance(lifecycle["evidence"], str) and bool(lifecycle["evidence"]), "empty client lifecycle evidence")
    terminal = value["terminal_proof"]
    _require(isinstance(terminal, Mapping), "missing client terminal proof")
    _require(set(terminal) == {"authenticated", "dead", "evidence"}, "invalid client terminal proof")
    _require(terminal["authenticated"] is True, "client terminal proof is unauthenticated")
    _require(isinstance(terminal["dead"], bool), "invalid client terminal dead flag")
    _require(isinstance(terminal["evidence"], str) and bool(terminal["evidence"]), "empty client terminal evidence")
    _require(isinstance(value["dead"], bool), "invalid client dead flag")
    _require(value["dead"] == terminal["dead"], "client dead flag disagrees with terminal proof")
    if require_dead:
        _require(value["dead"] is True, "owned client is not dead")
    return pid, token


def validate_attempt(attempt: Mapping[str, object]) -> None:
    """Require explicit client process evidence on every case attempt."""
    _require("client_process" in attempt, "case attempt omits client_process")
    started = attempt.get("started")
    if started is False:
        _require(attempt["client_process"] is None, "unstarted attempt must use client_process:null")
    else:
        _require(started is True, "case attempt started must be boolean")
        _client_process(attempt["client_process"], require_dead=False)


def validate_cleanup(
    cleanup: Mapping[str, object], owned_processes: Sequence[Mapping[str, object]]
) -> None:
    """Validate closed cleanup schema and prove every owned process is represented."""
    required = {"record_version", "run_id", "finalized_at_utc", "cases", "fresh_root", "harness", "output"}
    _require(set(cleanup) == required, "cleanup has an unexpected schema")
    _require(cleanup["record_version"] == 1, "invalid cleanup record version")
    run_id = cleanup["run_id"]
    _require(isinstance(run_id, str) and bool(run_id), "invalid cleanup run_id")
    _require(isinstance(cleanup["finalized_at_utc"], str) and _TIMESTAMP.fullmatch(cleanup["finalized_at_utc"]), "invalid cleanup timestamp")
    cases = cleanup["cases"]
    _require(isinstance(cases, Sequence) and not isinstance(cases, (str, bytes)) and len(cases) == 2, "cleanup must contain two cases")
    recorded: set[tuple[str, str, int, str]] = set()
    for index, expected_case in enumerate(_CASES):
        case = cases[index]
        _require(isinstance(case, Mapping), f"{expected_case}: case is not an object")
        _require(
            set(case) == {"case", "processes", "client_process", "listener", "active_authority_absent", "case_root"},
            f"{expected_case}: unexpected case schema",
        )
        _require(case["case"] == expected_case, f"unexpected case order: {case['case']!r}")
        processes = case["processes"]
        _require(isinstance(processes, Sequence) and not isinstance(processes, (str, bytes)), f"{expected_case}: invalid processes")
        pids = []
        for process in processes:
            _require(isinstance(process, Mapping) and set(process) == {"pid", "start_token", "dead"}, f"{expected_case}: invalid engine process")
            pid = process["pid"]
            token = process["start_token"]
            _require(isinstance(pid, int) and not isinstance(pid, bool) and pid > 0, f"{expected_case}: invalid engine PID")
            _require(isinstance(token, str) and bool(token), f"{expected_case}: invalid engine start token")
            _require(process["dead"] is True, f"{expected_case}: engine process is not dead")
            pids.append(pid)
            recorded.add((expected_case, "engine", pid, token))
        _require(pids == sorted(set(pids)), f"{expected_case}: engine PIDs must be sorted and unique")
        client_pid, client_token = _client_process(case["client_process"], require_dead=True)
        _require(client_pid not in pids, f"{expected_case}: client PID duplicates engine PID")
        recorded.add((expected_case, "client", client_pid, client_token))
        listener = case["listener"]
        _require(isinstance(listener, Mapping) and set(listener) == {"host", "port", "closed"}, f"{expected_case}: invalid listener")
        _require(isinstance(listener["host"], str) and bool(listener["host"]), f"{expected_case}: invalid listener host")
        _require(isinstance(listener["port"], int) and 1 <= listener["port"] <= 65535, f"{expected_case}: invalid listener port")
        _require(listener["closed"] is True, f"{expected_case}: listener remains open")
        _require(case["active_authority_absent"] is True, f"{expected_case}: authority remains active")
        case_root = case["case_root"]
        _require(isinstance(case_root, Mapping) and set(case_root) == {"path", "absent"}, f"{expected_case}: invalid case root")
        _require(isinstance(case_root["path"], str) and bool(case_root["path"],), f"{expected_case}: invalid case root path")
        _require(case_root["absent"] is True, f"{expected_case}: case root remains present")
    _require(len(owned_processes) == 4, "owned-process inventory must include exactly four process identities")
    expected = set()
    for process in owned_processes:
        _require(isinstance(process, Mapping) and set(process) == {"case", "role", "pid", "start_token"}, "invalid owned-process inventory")
        case = process["case"]
        role = process["role"]
        pid = process["pid"]
        token = process["start_token"]
        _require(case in _CASES and role in {"engine", "client"}, "invalid owned-process identity")
        _require(isinstance(pid, int) and not isinstance(pid, bool) and pid > 0, "invalid owned-process PID")
        _require(isinstance(token, str) and bool(token), "invalid owned-process start token")
        identity = (case, role, pid, token)
        _require(identity not in expected, "duplicate owned-process identity")
        expected.add(identity)
    _require(recorded == expected, "cleanup omits or invents an owned process")
    for name in ("fresh_root", "harness"):
        value = cleanup[name]
        _require(isinstance(value, Mapping) and set(value) == {"path", "absent"}, f"invalid {name}")
        _require(isinstance(value["path"], str) and value["path"].startswith("/") and bool(value["path"]), f"invalid {name} path")
        _require(value["absent"] is True, f"{name} remains present")
    output = cleanup["output"]
    _require(isinstance(output, Mapping) and set(output) == {"path", "preserved"}, "invalid output")
    _require(isinstance(output["path"], str) and output["path"].startswith("/") and bool(output["path"]), "invalid output path")
    _require(output["preserved"] is True, "durable output was not preserved")
