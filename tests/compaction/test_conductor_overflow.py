"""Conductor run with a provider that rejects oversized requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

import pytest

from breadboard_engine.engine import create_engine
from breadboard_engine.provider.contract_messages import ProviderMessage, ProviderResult
from breadboard_engine.provider.contract_runtime import ProviderRuntimeError
from breadboard_engine.provider.runtimes import testing as mock_runtime

REPO = Path(__file__).resolve().parents[2]
BASE_CONFIG = REPO / "agent_configs/misc/opencode_mock_c_fs.yaml"
# The mock provider grows history by two messages per tool round; requests of
# this many messages or more are rejected as context overflow.
OVERFLOW_AT = 7


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compaction: str,
         turns: List[Any] | None = None) -> List[Tuple[str, int]]:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    calls: List[Tuple[str, int]] = []
    original = mock_runtime.MockRuntime.invoke

    def invoke(self: Any, *, client, model, messages, tools, stream, context):
        if (context.extra or {}).get("compaction_summary"):
            calls.append(("summary", len(messages)))
            return ProviderResult(
                messages=[ProviderMessage(role="assistant", content="## Goal\nwrite a C function")],
                raw_response=None,
                model=model,
            )
        calls.append(("model", len(messages)))
        if turns is not None:
            turns.append(context.session_state.get_provider_metadata("current_turn_index"))
        if len(messages) >= OVERFLOW_AT:
            # Runtimes sanitize the public message but keep the SDK cause, which
            # native classifiers (e.g. OpenCode provider/error.ts) read.
            raise ProviderRuntimeError(
                "provider operation failed (BadRequestError)",
                details={"code": "context_length_exceeded", "status_code": 400},
            ) from Exception("This model's maximum context length is 1000 tokens.")
        return original(
            self, client=client, model=model, messages=messages, tools=tools, stream=stream, context=context
        )

    monkeypatch.setattr(mock_runtime.MockRuntime, "invoke", invoke)
    config = tmp_path / "config.yaml"
    config.write_text(f"extends: {BASE_CONFIG}\n{compaction}", encoding="utf-8")
    engine = create_engine(str(config), workspace_dir=str(tmp_path / "ws"))
    engine.run("Implement a simple C function.")
    return calls


def test_overflow_without_compaction_ends_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _run(tmp_path, monkeypatch, "")

    assert all(kind == "model" for kind, _ in calls)
    assert calls[-1] == ("model", OVERFLOW_AT)


@pytest.mark.parametrize(
    "compaction",
    [
        "compaction:\n  enabled: true\n  contextWindow: 200000\n  keepRecentTokens: 50\n  methodOrder: [soft]\n",
        "compaction:\n  enabled: true\n  preset: pi@0.73.1\n  contextWindow: 200000\n  keepRecentTokens: 50\n",
        "compaction:\n  enabled: true\n  preset: hermes_agent@2026.9.11\n  contextWindow: 200000\n  protect_first_n: 0\n  protect_last_n: 3\n",
        "compaction:\n  enabled: true\n  preset: openclaw@2026.9.4\n  contextWindow: 200000\n  keepRecentTokens: 50\n",
        "compaction:\n  enabled: true\n  preset: opencode@1.2.17\n  contextWindow: 200000\n",
        "compaction:\n  enabled: true\n  preset: oh-my-opencode@3.10.0\n  contextWindow: 200000\n",
    ],
    ids=["omp", "pi", "hermes", "openclaw", "opencode", "oh-my-opencode"],
)
def test_overflow_with_compaction_summarizes_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compaction: str
) -> None:
    calls = _run(tmp_path, monkeypatch, compaction)

    first_summary = next(index for index, (kind, _) in enumerate(calls) if kind == "summary")
    overflowed = [size for kind, size in calls[:first_summary] if kind == "model"]
    retried = next(size for kind, size in calls[first_summary:] if kind == "model")
    assert overflowed[-1] >= OVERFLOW_AT
    assert retried < OVERFLOW_AT
    # The run kept going after recovery instead of ending on the first overflow.
    assert sum(1 for kind, _ in calls if kind == "model") > len(overflowed) + 1


@pytest.mark.parametrize("preset", ["codex@0.139.0", "claude_code@2.1.63"])
def test_terminal_preset_overflow_ends_run_without_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preset: str
) -> None:
    calls = _run(tmp_path, monkeypatch, f"compaction:\n  enabled: true\n  preset: {preset}\n  contextWindow: 200000\n")
    assert all(kind == "model" for kind, _ in calls)
    assert calls[-1] == ("model", OVERFLOW_AT)


def test_overflow_retry_stays_on_the_failed_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    turns: List[Any] = []
    calls = _run(tmp_path, monkeypatch, "compaction:\n  enabled: true\n  preset: pi@0.73.1\n"
                 "  contextWindow: 200000\n  keepRecentTokens: 50\n", turns)
    models = [size for kind, size in calls if kind == "model"]
    # A recovered request is resent on its own turn; every other request starts a new turn.
    repeats = [index for index in range(1, len(turns)) if turns[index] == turns[index - 1]]
    assert repeats
    assert repeats == [index + 1 for index, size in enumerate(models[:-1]) if size >= OVERFLOW_AT]


def test_plugin_error_armed_by_one_run_does_not_compact_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    calls: List[str] = []
    original = mock_runtime.MockRuntime.invoke
    fail = [True]

    def invoke(self: Any, *, client, model, messages, tools, stream, context):
        if (context.extra or {}).get("compaction_summary"):
            calls.append("summary")
            if fail[0]:
                raise ProviderRuntimeError("summary provider unavailable")
            return ProviderResult(messages=[ProviderMessage(role="assistant", content="## Goal\nsummary")],
                                  raw_response=None, model=model)
        calls.append("model")
        if fail[0]:
            # Recognized only by the plugin's parser: it arms pending_error, whose recovery then fails.
            raise ProviderRuntimeError("messages: all messages must have non-empty content")
        return original(self, client=client, model=model, messages=messages, tools=tools, stream=stream, context=context)

    monkeypatch.setattr(mock_runtime.MockRuntime, "invoke", invoke)
    config = tmp_path / "config.yaml"
    config.write_text(f"extends: {BASE_CONFIG}\ncompaction:\n  enabled: true\n  preset: oh-my-opencode@3.10.0\n"
                      "  contextWindow: 200000\n", encoding="utf-8")
    engine = create_engine(str(config), workspace_dir=str(tmp_path / "ws"))
    engine.run("Implement a simple C function.")
    armed = [trigger for trigger in engine._agent.agent.compaction_controller.recipe.triggers
             if getattr(trigger, "kind", None) == "pending_error"]
    assert any(trigger.pending is not None for trigger in armed), "run 1 must end with the error still armed"
    fail[0] = False
    calls.clear()
    engine.run("Implement a simple C function.")
    assert "summary" not in calls
