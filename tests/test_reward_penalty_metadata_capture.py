from __future__ import annotations

from types import SimpleNamespace

from agentic_coder_prototype.conductor_patching import record_diff_metrics
from agentic_coder_prototype.monitoring.reward_metrics import RewardMetricsRecorder


class _DummyProviderMetrics:
    def add_dialect_metric(self, **kwargs):  # pragma: no cover - behavior not relevant for this test
        return None


class _DummySessionState:
    def __init__(self) -> None:
        self.reward_metrics = RewardMetricsRecorder()

    def add_reward_metric(self, turn_index: int, name: str, value: float) -> None:
        self.reward_metrics.set_metric(turn_index, name, value)

    def add_reward_metrics(self, turn_index: int, metrics=None, *, metadata=None):
        return self.reward_metrics.record_turn(turn_index, metrics, metadata=metadata, overwrite=False)


def test_record_diff_metrics_sets_penalty_metadata() -> None:
    conductor = SimpleNamespace(provider_metrics=_DummyProviderMetrics())
    session_state = _DummySessionState()
    tool_call = SimpleNamespace(
        function="apply_unified_patch",
        dialect="unified_diff",
        arguments={
            "patch": "diff --git a/foo.txt b/foo.txt\nnew file mode 100644\n--- /dev/null\n+++ b/foo.txt\n@@\n+hello\n"
        },
    )
    result = {"ok": False, "data": {"rejects": {}}}

    record_diff_metrics(
        conductor,
        tool_call,
        result,
        session_state=session_state,
        turn_index=0,
    )

    payload = session_state.reward_metrics.as_payload()
    assert payload["turns"]
    meta = payload["turns"][0].get("meta") or {}
    assert meta.get("invalid_diff_block") is True
    assert meta.get("add_file_via_diff") is True
