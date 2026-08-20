#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard_sdk.langflow_patch import make_langflow_v2_sync_override


class _Request:
    def __init__(self, flow_id: str, inputs: dict[str, object], background: bool = False, stream: bool = False):
        self.flow_id = flow_id
        self.inputs = inputs
        self.background = background
        self.stream = stream


class _Flow:
    def __init__(self, data: dict[str, object]):
        self.data = data


async def main() -> int:
    root = REPO_ROOT
    flow_path = (
        root.parent
        / "other_harness_refs/oss_targets/langflow/src/backend/base/langflow/initial_setup/starter_projects/Simple Agent.json"
    )
    request_path = root / "tests/fixtures/contracts/langflow/simple_agent_sync_request.json"
    expected_path = root / "tests/fixtures/contracts/langflow/simple_agent_sync_expected_response.json"

    request = json.loads(request_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    flow = json.loads(flow_path.read_text(encoding="utf-8"))

    override = make_langflow_v2_sync_override(
        breadboard_runner=lambda payload: {"terminal_text": "4", "status": "completed", "errors": []},
        native_sync_executor=lambda *args, **kwargs: {"status": "native"},
    )
    response = await override(_Request(request["flow_id"], request["inputs"]), _Flow(flow))
    ok = response == expected
    print(json.dumps({"ok": ok, "response": response}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
