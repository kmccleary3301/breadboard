import os
import uuid
import pytest
import ray

from breadboard.adaptive_iter import decode_adaptive_iterable
from breadboard.sandbox_v2 import new_dev_sandbox_v2


@pytest.fixture(scope="module")
def ray_cluster():
    # Assume Ray local; no special address
    ray.init()
    yield
    ray.shutdown()


def ensure_image(image_tag: str, dockerfile: str):
    # Only required when the docker sandbox is enabled.
    if os.environ.get("RAY_USE_DOCKER_SANDBOX", "0") in {"0", "false", "False"}:
        return
    import shutil, subprocess
    if shutil.which("docker") is None:
        pytest.skip("Docker not available on this host")
    # Inspect image; build if missing. If docker is present but not usable, skip.
    res = subprocess.run(["docker", "image", "inspect", image_tag], capture_output=True, text=True)
    if res.returncode != 0:
        build = subprocess.run(["docker", "build", "-t", image_tag, "-f", dockerfile, "."], capture_output=True, text=True)
        if build.returncode != 0:
            pytest.skip(f"Docker not usable to build {image_tag}: {build.stderr}")


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


def test_workspace_boundary_blocks_escape(ray_cluster, tmp_path):
    ensure_image("python-dev:latest", "python-dev.Dockerfile")
    ws = tmp_path / f"ws-{uuid.uuid4()}"
    ws.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret\n", encoding="utf-8")

    os.environ["RAY_USE_DOCKER_SANDBOX"] = "0"
    sb = new_dev_sandbox_v2("python-dev:latest", str(ws), name=f"sb-{uuid.uuid4()}")

    # Reads should never return the outside content.
    read_res = ray.get(sb.read_text.remote("../outside_secret.txt"))
    assert isinstance(read_res, dict)
    assert "secret" not in (read_res.get("content") or "")
    assert str(ws) in str(read_res.get("path") or "")

    raw = ray.get(sb.get.remote("../outside_secret.txt"))
    assert b"secret" not in (raw or b"")

    # Writes should never modify the outside file (implementation may raise or return an error).
    try:
        ray.get(sb.write_text.remote("../outside_secret.txt", "pwned\n"))
    except Exception:
        pass
    assert outside.read_text(encoding="utf-8") == "secret\n"
