from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from breadboard.rl.harness.openhands_worker import _IPCTransport


class _Channel:
    def __init__(self) -> None:
        self.response: dict[str, Any] | None = None

    def respond(self, value: dict[str, Any]) -> None:
        self.response = value

    def receive(self) -> dict[str, Any]:
        return {
            "operation": "provider_response",
            "payload": {
                "status_code": 200,
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(b'{"ok":true}').decode("ascii"),
            },
        }


@pytest.mark.parametrize(
    ("framing_headers", "expected_header_names"),
    [
        (
            [("Transfer-Encoding", "chunked"), ("X-Test", "preserve")],
            ["X-Test", "Content-Length"],
        ),
        (
            [
                ("Transfer-Encoding", "chunked"),
                ("content-length", "stale"),
                ("X-Test", "preserve"),
            ],
            ["content-length", "X-Test"],
        ),
        (
            [
                ("Content-Length", "stale"),
                ("content-length", "duplicate"),
                ("X-Test", "preserve"),
            ],
            ["Content-Length", "X-Test"],
        ),
    ],
)
def test_forwarded_framing_preserves_body_verbatim(
    framing_headers: list[tuple[str, str]], expected_header_names: list[str]
) -> None:
    document = {"model": "fixture-model", "messages": [{"role": "user", "content": "hello"}]}
    original_body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[("Authorization", "Bearer credential"), *framing_headers],
        content=original_body,
    )
    if expected_header_names == ["X-Test", "Content-Length"]:
        request.headers.pop("content-length", None)

    transport.handle_request(request)

    assert channel.response is not None
    forwarded = channel.response["http_request"]
    forwarded_body = base64.b64decode(forwarded["body_b64"], validate=True)
    assert forwarded_body == original_body
    assert all(name.casefold() != "transfer-encoding" for name, _ in forwarded["headers"])
    content_lengths = [
        value for name, value in forwarded["headers"] if name.casefold() == "content-length"
    ]
    assert content_lengths == [str(len(original_body))]
    forwarded_names = [
        name for name, _ in forwarded["headers"] if name.casefold() != "host"
    ]
    assert forwarded_names == expected_header_names

def test_forwarded_framing_is_canonical_without_body_rewrite() -> None:
    document = {
        "model": "fixture-model",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0,
    }
    original_body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[
            ("Authorization", "Bearer credential"),
            ("Transfer-Encoding", "chunked"),
            ("Content-Length", "stale"),
            ("content-length", "duplicate"),
            ("X-Test", "preserve"),
        ],
        content=original_body,
    )

    transport.handle_request(request)

    assert channel.response is not None
    forwarded = channel.response["http_request"]
    assert base64.b64decode(forwarded["body_b64"], validate=True) == original_body
    assert all(name.casefold() != "transfer-encoding" for name, _ in forwarded["headers"])
    assert [
        (name, value)
        for name, value in forwarded["headers"]
        if name.casefold() == "content-length"
    ] == [("Content-Length", str(len(original_body)))]


def test_worker_built_llm_uses_sealed_config_and_mutating_sampling_drops_temperature(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    py312 = (
        shutil.which("python3.12")
        or "/opt/breadboard-native-tools/python/bin/python3.12"
        or "/Users/kylemccleary/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12"
    )
    if not Path(py312).is_file():
        pytest.skip("python3.12 not found")

    env = dict(os.environ)
    repo_root = str(Path(__file__).resolve().parents[3])
    uv_pkg = "/Users/kylemccleary/.cache/uv/archive-v0/EmOGkXXkN6m3sPSJ/lib/python3.12/site-packages"
    pythonpaths = [repo_root]
    if os.path.isdir(uv_pkg):
        pythonpaths.append(uv_pkg)
    for p in sys.path:
        if "site-packages" in p and p not in pythonpaths:
            pythonpaths.append(p)
    env["PYTHONPATH"] = ":".join(pythonpaths)
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"

    worker_test_code = """
import os, sys, json, base64, pathlib
from breadboard.rl.harness.openhands_worker import OpenHandsActor

class SingleRequestChannel:
    def __init__(self):
        self.response = None
    def respond(self, value):
        self.response = value
    def receive(self):
        return {
            "operation": "provider_response",
            "payload": {
                "status_code": 200,
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(b'{"choices":[{"index":0,"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}]}').decode(),
            },
        }

workspace = sys.argv[1]
scratch = sys.argv[2]
config_path = sys.argv[3] if len(sys.argv) > 3 else None

channel = SingleRequestChannel()
actor = OpenHandsActor(channel)
payload = {
    "task": "respond with done",
    "model_config": {
        "model_name": "openai/gpt-4o-mini",
        "model_canonical_name": None,
        "max_input_tokens": 131072,
        "base_url": "http://127.0.0.1:1234/v1",
    },
    "workspace": workspace,
    "scratch": scratch,
    "max_iteration_per_run": 2,
}
if config_path:
    payload["native_config_path"] = config_path

actor.dispatch("initialize", payload)
actor.dispatch("sample", {})
actor.close()

assert channel.response is not None, "Worker never issued HTTP request"
body = json.loads(base64.b64decode(channel.response["http_request"]["body_b64"]))
print(json.dumps({"has_temperature": "temperature" in body, "temperature": body.get("temperature")}))
"""
    # 1. Sealed config: supports_sampling_params is True, so temperature is present
    ws1 = str(tmp_path / "ws1")
    sc1 = str(tmp_path / "sc1")
    Path(ws1).mkdir(parents=True)
    Path(sc1).mkdir(parents=True)
    res1 = subprocess.run([py312, "-c", worker_test_code, ws1, sc1], env=env, capture_output=True, text=True, check=True)
    out1 = json.loads(res1.stdout.strip().splitlines()[-1])
    assert out1["has_temperature"] is True
    assert out1["temperature"] == 0

    # 2. Mutated config: copy sealed config and set supports_sampling_params to False
    sealed_config_path = Path(repo_root) / "config/e4_targets/openhands_sdk/1.47.0/native-config.json"
    mutated_config = json.loads(sealed_config_path.read_text(encoding="utf-8"))
    mutated_config["model"]["capability_overrides"]["supports_sampling_params"] = False
    mutated_path = tmp_path / "mutated-native-config.json"
    mutated_path.write_text(json.dumps(mutated_config), encoding="utf-8")

    ws2 = str(tmp_path / "ws2")
    sc2 = str(tmp_path / "sc2")
    Path(ws2).mkdir(parents=True)
    Path(sc2).mkdir(parents=True)
    res2 = subprocess.run([py312, "-c", worker_test_code, ws2, sc2, str(mutated_path)], env=env, capture_output=True, text=True, check=True)
    out2 = json.loads(res2.stdout.strip().splitlines()[-1])
    assert out2["has_temperature"] is False, f"Expected temperature to be dropped with supports_sampling_params=False, got {out2}"


def test_ipctransport_forwards_sdk_body_verbatim() -> None:
    # 2. _IPCTransport forwards the SDK body byte-for-byte without rewriting
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    sdk_body = b'{"messages":[{"content":"hi","role":"user"}],"model":"openai/gpt-4o-mini","temperature":0.0}'
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[("Authorization", "Bearer credential")],
        content=sdk_body,
    )
    transport.handle_request(request)
    assert channel.response is not None
    forwarded = channel.response["http_request"]
    assert base64.b64decode(forwarded["body_b64"], validate=True) == sdk_body


@pytest.mark.parametrize("case_id", ["OH-01-normal-file-effect", "OH-02-invalid-call-continues", "OH-05-iteration-budget"])
def test_worker_first_request_matches_supplier_packet_and_preserves_temperature(case_id: str, tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    py312 = (
        shutil.which("python3.12")
        or "/opt/breadboard-native-tools/python/bin/python3.12"
        or "/Users/kylemccleary/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12"
    )
    if not Path(py312).is_file():
        pytest.skip("python3.12 not found")
    packet_path = Path("/Users/kylemccleary/projects/breadboard/docs_tmp/bb_direction_assessment/engine_pr_handoff_20260827/e4_admission_20260914T221653Z/do2-20260923/openhands/packet/openhands-supplier-capture-packet-rerun2/captures") / case_id / "trace.json"
    if not packet_path.is_file():
        pytest.skip(f"supplier packet trace for {case_id} not found")
    fixture_path = packet_path

    supplier_trace = json.loads(fixture_path.read_text(encoding="utf-8"))
    supplier_req0 = supplier_trace["requests"][0]["body"]
    repo_root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    uv_pkg = "/Users/kylemccleary/.cache/uv/archive-v0/EmOGkXXkN6m3sPSJ/lib/python3.12/site-packages"
    pythonpaths = [str(repo_root)]
    if os.path.isdir(uv_pkg):
        pythonpaths.append(uv_pkg)
    for p in sys.path:
        if "site-packages" in p and p not in pythonpaths:
            pythonpaths.append(p)
    env["PYTHONPATH"] = ":".join(pythonpaths)
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"

    ws = tmp_path / "ws"
    sc = tmp_path / "sc"
    ws.mkdir(parents=True)
    sc.mkdir(parents=True)

    test_code = """
import os, sys, json, base64, pathlib, importlib.util

worker_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("openhands_worker", worker_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
OpenHandsActor = mod.OpenHandsActor

ws = sys.argv[2]
sc = sys.argv[3]
task = sys.argv[4]
max_iter = int(sys.argv[5])
resp_body_json = sys.argv[6]

class SingleRequestChannel:
    def __init__(self):
        self.response = None
    def respond(self, value):
        self.response = value
    def receive(self):
        return {
            "operation": "provider_response",
            "payload": {
                "status_code": 200,
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(resp_body_json.encode()).decode(),
            },
        }

channel = SingleRequestChannel()
actor = OpenHandsActor(channel)
payload = {
    "task": task,
    "model_config": {
        "model_name": "openai/gpt-4o-mini",
        "model_canonical_name": None,
        "max_input_tokens": 131072,
        "base_url": "http://127.0.0.1:1234/v1",
    },
    "workspace": ws,
    "scratch": sc,
    "max_iteration_per_run": max_iter,
}
actor.dispatch("initialize", payload)
actor.dispatch("sample", {})
actor.close()

assert channel.response is not None, "Worker never issued HTTP request"
body_b64 = channel.response["http_request"]["body_b64"]
raw = base64.b64decode(body_b64)
body = json.loads(raw)
print(json.dumps(body))
"""
    task = supplier_trace.get("task")
    if not task:
        # Extract task from messages[1].content[0].text
        task = supplier_req0["messages"][1]["content"][0]["text"]
    max_iter = supplier_trace.get("controls", {}).get("max_iterations", 16)
    resp0 = supplier_trace.get("responses", [{}])[0].get("response", {"choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]})

    worker_path = str(repo_root / "breadboard/rl/harness/openhands_worker.py")
    res = subprocess.run([py312, "-c", test_code, worker_path, str(ws), str(sc), task, str(max_iter), json.dumps(resp0)], env=env, capture_output=True, text=True, check=True)
    worker_body = json.loads(res.stdout.strip().splitlines()[-1])

    assert "temperature" in worker_body
    assert worker_body["temperature"] == 0.0
    assert worker_body["model"] == supplier_req0["model"]

    # Compare normalized bodies
    supplier_norm = json.loads(json.dumps(supplier_req0).replace("/opt/openhands/case/workspace", "<WORKSPACE>"))
    worker_norm = json.loads(json.dumps(worker_body).replace(str(ws), "<WORKSPACE>"))
    worker_norm["prompt_cache_key"] = supplier_norm.get("prompt_cache_key")
    assert worker_norm == supplier_norm


def test_sealed_openhands_versions_match_supplier_requirements_lock() -> None:
    """Verify that sealed native-config versions match the supplier's pinned requirements.lock exactly.

    Cites supplier rerun2 requirements.lock:90,104 for litellm and openai, reading the lock file
    dynamically rather than comparing against hardcoded literals.
    """
    import json
    from pathlib import Path
    from breadboard_engine.e4_targets import load_e4_target

    lock_candidates = [
        Path("/Users/kylemccleary/projects/breadboard/docs_tmp/bb_direction_assessment/engine_pr_handoff_20260827/e4_admission_20260914T221653Z/do2-20260923/openhands/packet/openhands-supplier-capture-packet-rerun2/requirements.lock"),
        Path(__file__).resolve().parents[3] / "openhands/packet/openhands-supplier-capture-packet-rerun2/requirements.lock",
    ]
    lock_path = next((p for p in lock_candidates if p.is_file()), None)
    if lock_path is None:
        pytest.skip("supplier requirements.lock not available")

    locked_versions = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            pkg, _, ver = line.partition("==")
            locked_versions[pkg.strip().lower()] = ver.strip()

    target = load_e4_target("openhands-sdk@1.47.0")
    native_config = json.loads(target.read_asset_text("native-config.json"))
    sealed_versions = native_config["versions"]

    assert sealed_versions["litellm"] == locked_versions["litellm"]
    assert sealed_versions["openai"] == locked_versions["openai"]
