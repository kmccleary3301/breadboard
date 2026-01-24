from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import ray

from breadboard.adaptive_iter import decode_adaptive_iterable
from breadboard.sandbox_v2 import new_dev_sandbox_v2


def _docker_usable() -> bool:
    if os.environ.get("BREADBOARD_TEST_DOCKER", "0") != "1":
        return False
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="module")
def ray_cluster():
    ray.shutdown()
    ray.init(ignore_reinit_error=True)
    yield
    ray.shutdown()


def test_docker_sandbox_runs_shell(ray_cluster, tmp_path):
    if not _docker_usable():
        pytest.skip("Docker sandbox test disabled or docker not usable (set BREADBOARD_TEST_DOCKER=1)")

    os.environ["RAY_USE_DOCKER_SANDBOX"] = "1"
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)

    sb = new_dev_sandbox_v2(
        "alpine:3.19",
        str(ws),
        name=f"sb-{uuid.uuid4()}",
        driver="docker",
        driver_options={"network": "none"},
    )
    stream = ray.get(sb.run.remote("echo hello", stream=True))
    is_iter, it = decode_adaptive_iterable(stream)
    assert is_iter is True
    items = list(it)
    assert items[-1]["exit"] == 0
    assert "hello" in str(items[0])
