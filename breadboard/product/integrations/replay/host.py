from __future__ import annotations

from breadboard.product.evidence.replay import ReplayPlan, ReplayWorkerResult
from breadboard.product.integrations.host import SandboxHostAdapter

from ._common import json_value, load_request, worker_result


class HostReplayWorker:
    """Replay one recorded command through an existing product host port."""

    def __init__(self, host: SandboxHostAdapter, *, output_path: str = "host_result.json") -> None:
        if not isinstance(host, SandboxHostAdapter):
            raise TypeError("host replay requires a product SandboxHostAdapter")
        self.host = host
        self.output_path = output_path
        self.worker_id = f"host/{host.host_id}/replay-v1"

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        if plan.worker_id != self.worker_id:
            raise ValueError("replay plan worker_id does not select this host")
        request = load_request(input_bytes, "bb.host_replay_input.v1")
        command, options = request.get("command"), request.get("options", {})
        if not isinstance(command, str) or not command:
            raise ValueError("host replay input requires a command")
        if not isinstance(options, dict):
            raise ValueError("host replay options must be an object")
        result = json_value(self.host.execute(command, **options))
        output = {
            "schema_version": "bb.host_replay_result.v1",
            "host_id": self.host.host_id,
            "result": result,
        }
        return worker_result(
            plan,
            output_path=self.output_path,
            output=output,
            port_kind="host.execute",
            request={"host_id": self.host.host_id, "command": command, "options": options},
            response={"result": result},
        )
