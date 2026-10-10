from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compaction_lanes.hermes_lane import (
    MockProvider, SUMMARY, TERMINAL, SOURCE, run_side, scenario_script,
    seed_workspace, target_profile,
)

pytestmark = pytest.mark.skipif(not (SOURCE / "run_agent.py").is_file(), reason="pinned Hermes source is unavailable")


@pytest.mark.parametrize("scenario", ["threshold", "overflow"])
def test_real_worker_compaction_through_lowered_checkpointed_conductor(tmp_path: Path, scenario: str) -> None:
    workspace = (tmp_path / "workspace").resolve()
    seed_workspace(workspace, scenario)
    with MockProvider(script=scenario_script(scenario)) as provider:
        result = run_side("bb", scenario, workspace, tmp_path.resolve() / "runtime", provider.base_url + "/v1")
        assert not provider.engine.script
        wire = [record["json"] for record in provider.engine.recorded_requests if record["path"] == "/v1/chat/completions"]
    profile = target_profile()
    assert profile["compaction"] is True
    assert result["initialize"]["compaction"] is True
    assert result["initialize"]["schema_overlay"] == json.loads(json.dumps(dict(profile["schema_overlay"]), default=dict))
    summary_indices = [i for i, request in enumerate(result["requests"]) if request["purpose"] == "compaction_summary"]
    assert summary_indices
    for index in summary_indices:
        assert wire[index]["tools"] == wire[0]["tools"]
        assert wire[index]["max_tokens"] == wire[0]["max_tokens"]
    assert any(SUMMARY in str(message.get("content", "")) for message in result["history"])
    assert not any(message.get("tool_call_id") == "read-24" for message in result["history"])
    if scenario == "threshold":
        assert result["completed"] is True
        assert summary_indices == [3]
        assert any(SUMMARY in str(message.get("content", "")) for message in wire[4]["messages"])
        assert wire[4]["messages"] != wire[2]["messages"]
        assert result["history"][-1]["content"] == "Inspection complete."
    else:
        assert result["completed"] is False
        assert result["terminal"] == TERMINAL
        assert not any(message.get("role") == "tool" for message in result["history"])
        assert len([request for request in result["requests"] if request["purpose"] == "main"]) == 8
