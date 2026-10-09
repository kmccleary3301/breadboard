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


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compaction: str) -> List[Tuple[str, int]]:
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
        if len(messages) >= OVERFLOW_AT:
            raise ProviderRuntimeError(
                "provider operation failed (BadRequestError)",
                details={"code": "context_length_exceeded", "status_code": 400},
            )
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
        "compaction:\n  enabled: true\n  preset: opencode@1.2.17\n  contextWindow: 200000\n",
        "compaction:\n  enabled: true\n  preset: oh-my-opencode@3.10.0\n  contextWindow: 200000\n",
    ],
    ids=["omp", "pi", "opencode", "oh-my-opencode"],
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
