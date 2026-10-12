from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from breadboard.rl.harness.runners.native_compaction import compact_history


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", [[], {"role": "user", "content": "resume"}])
async def test_compaction_rejects_invalid_worker_continuation(continuation: Any) -> None:
    async def worker_phase(operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if operation == "prepare_compaction":
            return {"kind": "compaction_prepared", "preparation": {"tokensBefore": 100}}
        if operation == "finalize_compaction":
            return {
                "kind": "compaction_finalized",
                "messages": [{"role": "assistant", "content": "retained history"}],
                "continuation": continuation,
            }
        raise AssertionError(f"Unexpected worker phase {operation}")

    async def summary_exchange(request: Any, native_request_body_json: str | None = None) -> Any:
        raise AssertionError("The native rewrite has no summary request")

    with pytest.raises(ValueError):
        await compact_history(
            [{"role": "user", "content": "task"}],
            reason="threshold", checkpoint="agent_end", usage=None,
            context_window=1000, settings=None, model_id="test-model", tools=[],
            phase=worker_phase, exchange=summary_exchange, trace_requests=[],
        )
