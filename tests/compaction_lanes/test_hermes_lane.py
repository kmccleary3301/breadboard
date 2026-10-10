from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.compaction_lanes.hermes_lane import SOURCE, TARGET_DIR, TERMINAL, run_scenario

pytestmark = pytest.mark.skipif(not (SOURCE / "run_agent.py").is_file(), reason="pinned Hermes source is unavailable")


@pytest.mark.parametrize("scenario", ["threshold", "overflow", "retry_turns"])
def test_stock_and_checkpointed_worker_wire_parity(tmp_path: Path, scenario: str) -> None:
    lane = run_scenario(tmp_path, scenario)
    assert lane["diffs"] == []
    declared = yaml.safe_load((TARGET_DIR / "harness.yaml").read_bytes())["policy"]["deviations"]
    assert lane["deviations"] == [entry["id"] for entry in declared]
    assert len(lane["compactions"]) == {"threshold": 1, "overflow": 2, "retry_turns": 6}[scenario]
    for compaction in lane["compactions"]:
        assert compaction["stock_omits_max_tokens"] is True
        index = compaction["request_index"]
        raw = bytes.fromhex(lane["requests"]["stock"][index]["raw_body_bytes"])
        assert compaction["recorded_stock_body_sha256"] == hashlib.sha256(raw).hexdigest()
    for index, request in enumerate(lane["bb"]["requests"]):
        assert base64.b64decode(request["body_b64"]).hex() == lane["requests"]["bb"][index]["raw_body_bytes"]
        if request["purpose"] == "main":
            assert lane["requests"]["stock"][index]["raw_body_bytes"] == lane["requests"]["bb"][index]["raw_body_bytes"]
    assert lane["bb"]["initialize"]["compaction"] is True
    if scenario == "overflow":
        assert lane["stock"]["terminal"] == lane["bb"]["terminal"] == TERMINAL
    else:
        assert lane["stock"]["completed"] is lane["bb"]["completed"] is True
    if scenario == "retry_turns":
        # Two overflowing tool-loop turns each issue one logical summary. The
        # stock auxiliary retry loop spends three physical exchanges on each.
        summaries = lane["bb"]["summaries"]
        assert [row["compaction_summary_retry"] for row in summaries] == [False, True, True, False, True, True]
        assert len({row["compaction_summary_id"] for row in summaries}) == 2
        assert lane["bb"]["history"][-1]["content"] == "Inspection complete."
    # The complete lane artifact is JSON serializable, including history and diffs.
    json.dumps(lane, allow_nan=False)


@pytest.mark.parametrize("side,field,value", [
    ("bb", "tools", [{"name": "altered"}]),
    ("bb", "max_tokens", 1),
    ("stock", "tools", []),
    ("stock", "max_tokens", 2048),
])
def test_summary_deviations_reject_wrong_values(side, field, value):
    from copy import deepcopy
    from scripts.compaction_lanes.hermes_lane import summary_deviations_valid
    main = {"tools": [{"name": "read_file"}], "max_tokens": 2048}
    stock = {"messages": []}
    bb = {"messages": [], **deepcopy(main)}
    assert summary_deviations_valid(stock, bb, ["tools", "max_tokens"], main)
    (bb if side == "bb" else stock)[field] = value
    assert not summary_deviations_valid(stock, bb, ["tools", "max_tokens"], main)
