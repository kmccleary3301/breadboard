"""Runtime backend primitives for RL sessions."""

from breadboard.rl.runtime.base import RuntimeHealth, RuntimeResult, RuntimeSnapshot
from breadboard.rl.runtime.local_process import LocalProcessToyRuntime
from breadboard.rl.runtime.pool import RuntimePool, WorkerRecord
from breadboard.rl.runtime.signature import RuntimeSignature, build_runtime_signature
from breadboard.rl.runtime.telemetry import build_warm_vs_cold_report, summarize_stage_metrics

__all__ = [
    "LocalProcessToyRuntime",
    "RuntimePool",
    "RuntimeHealth",
    "RuntimeResult",
    "RuntimeSnapshot",
    "RuntimeSignature",
    "WorkerRecord",
    "build_runtime_signature",
    "build_warm_vs_cold_report",
    "run_local_ray_toy_probe",
    "summarize_stage_metrics",
]


def __getattr__(name: str):
    if name == "run_local_ray_toy_probe":
        from breadboard.rl.runtime.ray_worker import run_local_ray_toy_probe

        return run_local_ray_toy_probe
    raise AttributeError(name)
