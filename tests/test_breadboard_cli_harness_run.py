from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import breadboard_sdk
from scripts import breadboard_cli


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

    def __init__(self, base_url: str) -> None:
        self.calls.append(("connect", base_url))

    def create_session(self, *, config_path: str, task: str) -> dict[str, str]:
        self.calls.append(("create", config_path, task))
        return {"session_id": "session-g3"}

    def post_input(self, session_id: str, *, content: str) -> None:
        assert session_id == "session-g3"
        self.calls.append(("input", session_id, content))

    def read_session_records(self, session_id: str) -> dict[str, list[dict[str, str]]]:
        assert session_id == "session-g3"
        self.calls.append(("records", session_id))
        return {"records": [{"id": "record-1"}, {"id": "record-2"}]}

    def stream_events(
        self, session_id: str, *, query: dict[str, str]
    ) -> Iterator[dict[str, Any]]:
        assert session_id == "session-g3"
        assert query == {"replay": "true"}
        self.calls.append(("events", session_id))
        yield {"type": "assistant_message", "payload": {"content": "working"}}
        yield {"type": "completion", "payload": {"status": "completed"}}
        raise AssertionError("the CLI must stop consuming events after completion")



class _EofClient(_RunClient):
    def stream_events(
        self, session_id: str, *, query: dict[str, str]
    ) -> Iterator[dict[str, Any]]:
        assert session_id == "session-g3"
        assert query == {"replay": "true"}
        self.calls.append(("events", session_id))
        yield {"type": "assistant_message", "payload": {"content": "still working"}}


def test_harness_run_submits_task_once_and_reports_completed_session(
    locked_harness: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _RunClient.calls = []
    monkeypatch.setattr(breadboard_sdk, "BreadboardClient", _RunClient)

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
    assert exit_code == 0
    assert captured.err == ""
    assert "session-g3" in captured.out
    assert "2" in captured.out
    assert _RunClient.calls == [
        ("connect", "https://breadboard.test/api"),
        ("create", str(locked_harness), ""),
        ("input", "session-g3", "repair the harness"),
        ("events", "session-g3"),
        ("records", "session-g3"),
    ]



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
        def __init__(self, base_url: str) -> None:
            assert base_url == "https://breadboard.test/api"

        def create_session(self, *, config_path: str, task: str) -> dict[str, str]:
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
