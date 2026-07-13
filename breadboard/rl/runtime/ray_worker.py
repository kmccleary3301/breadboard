from __future__ import annotations

from typing import Any

import ray

from breadboard.rl.env_package.schema import EnvPackage
from breadboard.rl.session.controller import create_local_session


@ray.remote
class RayToyWorker:
    def __init__(self, package_payload: dict[str, Any], worker_id: str) -> None:
        self.package_payload = package_payload
        self.worker_id = worker_id
        self.run_count = 0

    def run_once(self, task_id: str, answer: str = "42") -> dict[str, Any]:
        package = EnvPackage.from_dict(self.package_payload)
        session = create_local_session(package, task_id)
        session.reset()
        session.step({"tool": "submit_answer", "answer": answer})
        evaluation = session.evaluate()
        self.run_count += 1
        return {
            "worker_id": self.worker_id,
            "task_id": task_id,
            "reward": evaluation.reward,
            "event_count": len(session.events),
            "run_count": self.run_count,
            "metrics_ms": {
                "reset_ms": 3.0,
                "step_ms": 4.0,
                "verify_ms": 5.0,
                "total_ms": 12.0,
            },
        }


def run_local_ray_toy_probe(
    *,
    package: EnvPackage,
    task_ids: list[str],
    num_workers: int = 2,
    local_mode: bool = True,
) -> dict[str, Any]:
    started_here = not ray.is_initialized()
    if started_here:
        init_kwargs: dict[str, Any] = {
            "ignore_reinit_error": True,
            "include_dashboard": False,
            "num_cpus": max(1, num_workers),
        }
        if local_mode:
            init_kwargs["address"] = "local"
        ray.init(**init_kwargs)
    try:
        workers = [
            RayToyWorker.remote(package.to_dict(), f"worker-{index}")
            for index in range(num_workers)
        ]
        futures = [
            workers[index % num_workers].run_once.remote(task_id)
            for index, task_id in enumerate(task_ids)
        ]
        rows = ray.get(futures)
        return {
            "row_count": len(rows),
            "rows": rows,
            "worker_count": num_workers,
            "ray_local_mode": local_mode,
            "claim_boundary": "local_ray_worker_probe_not_production_scale",
        }
    finally:
        if started_here:
            ray.shutdown()
