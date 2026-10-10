"""Pi 0.57.1 compaction phases with native AgentMessages, not wire messages."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from tests.rl.harness.test_pi_compaction_stock_oracle import (
    FramedWorkerClient,
    _NODE_MODULES_0_57_1,
    _STOCK_ORACLE,
    _WORKER_0_57_1,
    _assistant,
    _scenario,
)

pytestmark = pytest.mark.skipif(
    not _NODE_MODULES_0_57_1.is_dir(), reason="pinned Pi 0.57.1 package unavailable",
)


@pytest.fixture
async def worker(tmp_path: Path):
    client = FramedWorkerClient(_WORKER_0_57_1, _NODE_MODULES_0_57_1, tmp_path)
    initialized = await client.start("0.57.1")
    try:
        yield client, initialized
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_worker_prepare_and_finalize_overflow_compaction(worker):
    client, _ = worker
    step = _scenario("split_turn")[0]
    prepared = await client.invoke("prepare_compaction", {
        "reason": "overflow", "checkpoint": "overflow", "messages": step["messages"],
        "context_window": step["context_window"], "settings": step["settings"],
    })
    assert prepared["kind"] == "compaction_prepared"
    assert prepared["schema_version"] == "bb.pi-native.v0.57.1"
    assert prepared["preparation"]["tokensBefore"] > 0
    assert prepared["preparation"]["firstKeptIndex"] > 0
    finalized = await client.invoke("finalize_compaction", {
        "summary": step["responses"]["history"], "turn_prefix_summary": step["responses"]["prefix"],
        "preparation": prepared["preparation"],
    })
    assert finalized["kind"] == "compaction_finalized"
    assert finalized["messages"][0]["role"] == "compactionSummary"
    assert finalized["messages"][0]["tokensBefore"] == prepared["preparation"]["tokensBefore"]
    assert "Stock history summary" in finalized["summary"]
    assert "Stock turn prefix" in finalized["summary"]


@pytest.mark.parametrize("tokens,triggered", [(2500, False), (8500, True)])
@pytest.mark.asyncio
async def test_worker_threshold_uses_stock_provider_usage(worker, tokens: int, triggered: bool):
    client, _ = worker
    history = [{"role": "user", "content": "Task " + "word " * 500, "timestamp": 1000},
               _assistant("Answer " + "word " * 500, 1001)]
    prepared = await client.invoke("prepare_compaction", {
        "reason": "threshold", "checkpoint": "agent_end", "messages": history,
        "context_window": 10000,
        "usage": {"prompt_tokens": tokens - 500, "completion_tokens": 500, "total_tokens": tokens},
        "settings": {"enabled": True, "reserveTokens": 2000, "keepRecentTokens": 200},
    })
    assert prepared["kind"] == ("compaction_prepared" if triggered else "compaction_unavailable")
    if not triggered:
        assert prepared["reason"] == "not_triggered"


@pytest.mark.asyncio
async def test_worker_split_turn_compaction(worker):
    client, _ = worker
    step = _scenario("split_turn")[0]
    prepared = await client.invoke("prepare_compaction", {
        "reason": "overflow", "checkpoint": "overflow", "messages": step["messages"],
        "settings": step["settings"],
    })
    assert prepared["preparation"]["isSplitTurn"] is True
    assert prepared["summary_request"] is not None
    assert prepared["turn_prefix_request"] is not None


@pytest.mark.asyncio
async def test_worker_parity_with_stock_prepare_compaction(worker):
    client, initialized = worker
    step = _scenario("split_turn")[0]
    prepared = await client.invoke("prepare_compaction", {
        "reason": "overflow", "checkpoint": "overflow", "messages": step["messages"],
        "settings": step["settings"],
    })
    stock = subprocess.run(
        ["node", "--input-type=module", "-e", _STOCK_ORACLE],
        input=json.dumps({"node_modules": str(_NODE_MODULES_0_57_1), "version": "0.57.1",
                          "model": initialized["bootstrap"]["model_config"],
                          "system_prompt": initialized["system_prompt"], "steps": [step]}),
        text=True, capture_output=True,
    )
    assert stock.returncode == 0, stock.stderr
    expected = json.loads(stock.stdout)[0]["preparation"]
    for key in ("firstKeptEntryId", "tokensBefore", "isSplitTurn", "fileOps"):
        assert prepared["preparation"][key] == expected[key]
