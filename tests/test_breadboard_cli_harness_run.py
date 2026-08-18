from __future__ import annotations
import json
import os

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import breadboard_sdk
from scripts import breadboard_cli
from breadboard.product.cli import harness as harness_operations
from breadboard.product.cli import session as session_operations
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import JsonlEventSink, Session


HARNESS_PATH = Path("agent_configs/templates/minimal_harness.v2.yaml")

@pytest.fixture
def locked_harness(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    harness_path = tmp_path / "minimal_harness.v2.yaml"
    prompt_path = tmp_path / "prompts" / "minimal_system.md"
    prompt_path.parent.mkdir()
    harness_path.write_bytes(HARNESS_PATH.read_bytes())
    prompt_path.write_bytes(
        (HARNESS_PATH.parent / "prompts" / "minimal_system.md").read_bytes()
    )
    assert breadboard_cli.main(["harness", "lock", str(harness_path)]) == 0
    capsys.readouterr()
    return harness_path


class _RunClient:
    calls: list[tuple[Any, ...]] = []

    def __init__(self, base_url: str, *, timeout_s: float) -> None:
        self.calls.append(("connect", base_url, timeout_s))

    def start_session(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        self.calls.append(("start", payload, idempotency_key))
        return {"ok": True, "data": {"session": {"session_id": "session-g3"}}}

    def events_session(self, session_id: str) -> Iterator[dict[str, Any]]:
        assert session_id == "session-g3"
        self.calls.append(("events", session_id))
        yield {"kind": "assistant.message", "payload": {"content": "working"}}
        yield {"kind": "session.completed", "payload": {"status": "completed"}}
        raise AssertionError("the CLI must stop consuming events after completion")

    def get_session(self, session_id: str) -> dict[str, Any]:
        assert session_id == "session-g3"
        self.calls.append(("get", session_id))
        return {
            "ok": True,
            "data": {
                "session": {
                    "session_id": session_id,
                    "event_count": 3,
                    "effective_lock_hash": "sha256:lock",
                    "task_hash": "sha256:task",
                }
            },
        }



class _EofClient(_RunClient):
    def events_session(self, session_id: str) -> Iterator[dict[str, Any]]:
        assert session_id == "session-g3"
        self.calls.append(("events", session_id))
        yield {"kind": "assistant.message", "payload": {"content": "still working"}}


def test_local_server_enables_only_product_api_and_restores_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "0")
    with harness_operations._local_server(tmp_path) as base_url:
        assert requests.get(f"{base_url}/v1/system", timeout=5).status_code == 200
        assert requests.get(f"{base_url}/v1/e4/lanes", timeout=5).status_code == 404
    assert os.environ["BREADBOARD_ENABLE_PUBLIC_API"] == "0"

def test_session_cli_restores_flat_legacy_event_layout(tmp_path: Path) -> None:
    session_id = "legacy-session"
    event_path = session_operations.legacy_session_event_path(tmp_path, session_id)
    lock = EffectiveHarnessLock._from_record({"graph_hash": "sha256:" + "a" * 64})
    session = Session.start(lock, "legacy task", session_id=session_id, sink=JsonlEventSink(event_path))
    session.complete("legacy completion")
    args = SimpleNamespace(workspace=tmp_path, SESSION_ID=session_id)

    restored = session_operations.get(args)
    listed = session_operations.list_sessions(args)

    assert restored.ok and restored.data["session"]["status"] == "completed"
    assert restored.record_refs == [".breadboard/sessions/legacy-session.events.jsonl"]
    assert listed.data["sessions"] == [{"session_id": session_id, "status": "completed", "event_count": 2}]
    assert listed.record_refs == restored.record_refs



def test_harness_run_submits_task_once_and_reports_completed_session(
    locked_harness: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _RunClient.calls = []
    monkeypatch.setattr(breadboard_sdk, "BreadboardClient", _RunClient)

    exit_code = breadboard_cli.main(
        [
            "--json",
            "harness",
            "run",
            str(locked_harness),
            "--server",
            "https://breadboard.test/api",
            "--task",
            "repair the harness",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["data"] == {"session_id": "session-g3", "record_count": 3, "event_count": 3}
    assert _RunClient.calls == [
        ("connect", "https://breadboard.test/api", 120),
        ("start", {"lock_id": locked_harness.with_name(locked_harness.stem + ".lock.json").name, "task": "repair the harness"}, harness_operations.sha256_json({"lock_id": locked_harness.with_name(locked_harness.stem + ".lock.json").name, "task": "repair the harness"})),
        ("events", "session-g3"),
        ("get", "session-g3"),
    ]


def test_harness_run_consumes_custom_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness_path = tmp_path / "minimal_harness.v2.yaml"
    prompt_path = tmp_path / "prompts" / "minimal_system.md"
    prompt_path.parent.mkdir()
    harness_path.write_bytes(HARNESS_PATH.read_bytes())
    prompt_path.write_bytes((HARNESS_PATH.parent / "prompts" / "minimal_system.md").read_bytes())
    custom_lock = tmp_path / "locks" / "effective.json"
    assert breadboard_cli.main(["--json", "harness", "lock", str(harness_path), "--out", str(custom_lock)]) == 0
    lock_result = capsys.readouterr()
    assert f"--lock {custom_lock}" in lock_result.out
    assert not harness_path.with_name(harness_path.stem + ".lock.json").exists()
    extensionless_lock = custom_lock.with_suffix(".lock")
    custom_lock.rename(extensionless_lock)
    custom_lock.with_name(f".{custom_lock.name}.meta.json").rename(
        extensionless_lock.with_name(f".{extensionless_lock.name}.meta.json")
    )
    custom_lock = extensionless_lock
    _RunClient.calls = []
    monkeypatch.setattr(breadboard_sdk, "BreadboardClient", _RunClient)

    exit_code = breadboard_cli.main(
        ["harness", "run", str(harness_path), "--lock", str(custom_lock), "--server", "https://breadboard.test/api"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "session-g3" in captured.out
    harness_path.write_text(harness_path.read_text().replace("mock/reference", "mock/changed"))
    exit_code = breadboard_cli.main(
        ["harness", "run", str(harness_path), "--lock", str(custom_lock), "--server", "https://breadboard.test/api"]
    )
    captured = capsys.readouterr()
    assert exit_code == 5
    assert f"breadboard harness lock {harness_path} --out {custom_lock}" in captured.err
    capsys.readouterr()
    assert breadboard_cli.main(
        ["harness", "lock", str(harness_path), "--out", str(custom_lock)]
    ) == 0
    capsys.readouterr()
    assert custom_lock.is_file()



def test_harness_run_rejects_event_stream_eof_before_terminal_event(
    locked_harness: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _EofClient.calls = []
    monkeypatch.setattr(breadboard_sdk, "BreadboardClient", _EofClient)

    exit_code = breadboard_cli.main(
        [
            "harness",
            "run",
            str(locked_harness),
            "--server",
            "https://breadboard.test/api",
            "--task",
            "repair the harness",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""


def test_harness_run_maps_sdk_failures_to_runtime_exit(
    locked_harness: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingClient:
        def __init__(self, base_url: str, *, timeout_s: float) -> None:
            assert base_url == "https://breadboard.test/api"
            assert timeout_s == 120

        def start_session(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
            raise RuntimeError("bridge unavailable")

    monkeypatch.setattr(breadboard_sdk, "BreadboardClient", FailingClient)

    exit_code = breadboard_cli.main(
        [
            "harness",
            "run",
            str(locked_harness),
            "--server",
            "https://breadboard.test/api",
            "--task",
            "repair the harness",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.out == ""
    assert "bridge unavailable" in captured.err


def test_harness_run_rejects_definition_changed_after_lock(
    locked_harness: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _RunClient.calls = []
    monkeypatch.setattr(breadboard_sdk, "BreadboardClient", _RunClient)
    locked_harness.write_text(
        locked_harness.read_text(encoding="utf-8").replace(
            "idle_turn_limit: 1",
            "idle_turn_limit: 2",
        ),
        encoding="utf-8",
    )

    exit_code = breadboard_cli.main(
        [
            "harness",
            "run",
            str(locked_harness),
            "--server",
            "https://breadboard.test/api",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 5
    assert captured.out == ""
    assert "lock_drift" in captured.err.lower()
    assert _RunClient.calls == []


@pytest.mark.parametrize(
    "target_args",
    [
        pytest.param([], id="missing-target"),
        pytest.param(
            ["--server", "https://breadboard.test/api", "--local"],
            id="conflicting-targets",
        ),
    ],
)
def test_harness_run_requires_exactly_one_execution_target(
    target_args: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        breadboard_cli.main(["harness", "run", str(HARNESS_PATH), *target_args])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "--server" in captured.err
    assert "--local" in captured.err
