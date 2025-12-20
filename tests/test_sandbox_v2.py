import os
import uuid
import pytest
import ray

from adaptive_iter import decode_adaptive_iterable
from sandbox_v2 import new_dev_sandbox_v2


@pytest.fixture(scope="module")
def ray_cluster():
    # Assume Ray local; no special address
    ray.init()
    yield
    ray.shutdown()


def ensure_image(image_tag: str, dockerfile: str):
    # Build image if not present
    import shutil, subprocess
    if shutil.which("docker") is None:
        pytest.skip("Docker not available on this host")
    # Inspect image; build if missing
    res = subprocess.run(["sudo", "docker", "image", "inspect", image_tag], capture_output=True, text=True)
    if res.returncode != 0:
        build = subprocess.run(["sudo", "docker", "build", "-t", image_tag, "-f", dockerfile, "."], text=True)
        assert build.returncode == 0, f"Failed to build {image_tag}"


def test_basic_run_and_stream(ray_cluster, tmp_path):
    ensure_image("python-dev:latest", "python-dev.Dockerfile")
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    # Use local host workspace fallback for CI when Docker perms are restricted
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    sb = new_dev_sandbox_v2("python-dev:latest", str(ws), name=f"sb-{uuid.uuid4()}")
    # Streaming echo
    stream = ray.get(sb.run.remote("echo hello", stream=True))
    is_iter, it = decode_adaptive_iterable(stream)
    assert is_iter is True
    lines = list(it)
    assert lines[-1]["exit"] == 0
    assert "hello" in lines[0]


def test_file_io_and_grep(ray_cluster, tmp_path):
    ensure_image("python-dev:latest", "python-dev.Dockerfile")
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    sb = new_dev_sandbox_v2("python-dev:latest", str(ws), name=f"sb-{uuid.uuid4()}")
    content = "alpha\nbeta\nGamma\n"
    ray.get(sb.write_text.remote("data.txt", content))
    res = ray.get(sb.grep.remote("beta", path="."))
    assert res["matches"], res
    assert any(m["line"] == 2 for m in res["matches"])  # line number 2


def test_git_apply_and_diff(ray_cluster, tmp_path):
    ensure_image("python-dev:latest", "python-dev.Dockerfile")
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    sb = new_dev_sandbox_v2("python-dev:latest", str(ws), name=f"sb-{uuid.uuid4()}")

    # create file
    ray.get(sb.write_text.remote("foo.txt", "hello\n"))
    ray.get(sb.vcs.remote({"action": "init", "user": {"name": "tester", "email": "t@example.com"}}))
    ray.get(sb.vcs.remote({"action": "add"}))
    commit = ray.get(sb.vcs.remote({"action": "commit", "params": {"message": "init"}}))
    assert commit["ok"]

    # make a diff
    ray.get(sb.write_text.remote("foo.txt", "hello world\n"))
    diff = ray.get(sb.vcs.remote({"action": "diff", "params": {"staged": False, "unified": 3}}))
    assert diff["ok"]
    diff_text = diff["data"]["diff"]
    assert "foo.txt" in diff_text

    # revert and apply patch
    ray.get(sb.write_text.remote("foo.txt", "hello\n"))
    apply_res = ray.get(sb.vcs.remote({"action": "apply_patch", "params": {"patch": diff_text, "three_way": True}}))
    assert apply_res["ok"], apply_res
    status = ray.get(sb.vcs.remote({"action": "status"}))
    assert status["ok"]


