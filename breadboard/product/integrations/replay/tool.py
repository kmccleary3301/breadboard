from __future__ import annotations

from breadboard.product.evidence.replay import ReplayPlan, ReplayWorkerResult
from breadboard.product.integrations.tool import ToolIntegrationAdapter

from ._common import json_value, load_request, worker_result


class ToolReplayWorker:
    """Replay one recorded invocation through an existing product tool port."""

    def __init__(self, tool: ToolIntegrationAdapter, *, output_path: str = "tool_result.json") -> None:
        if not isinstance(tool, ToolIntegrationAdapter):
            raise TypeError("tool replay requires a product ToolIntegrationAdapter")
        self.tool = tool
        self.output_path = output_path
        self.worker_id = f"tool/{tool.tool_id}/replay-v1"

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        if plan.worker_id != self.worker_id:
            raise ValueError("replay plan worker_id does not select this tool")
        request = load_request(input_bytes, "bb.tool_replay_input.v1")
        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("tool replay input requires arguments")
        result = json_value(self.tool.execute(arguments))
        output = {
            "schema_version": "bb.tool_replay_result.v1",
            "tool_id": self.tool.tool_id,
            "result": result,
        }
        return worker_result(
            plan,
            output_path=self.output_path,
            output=output,
            port_kind="tool.execute",
            request={"tool_id": self.tool.tool_id, "arguments": arguments},
            response={"result": result},
        )
