from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase3.evidence import sha256_file, write_phase3_json
from breadboard.rl.phase3.integrations import HARBOR_BLOCKED_CLAIM_BOUNDARY, run_harbor_service_proof


SCHEMA = "bb.rl.phase3.self_hosted_harbor_facade.v1"
REPORT_ID = "phase3_self_hosted_harbor_facade"
LOCAL_FACADE_CLAIM_BOUNDARY = "local_harbor_compatible_facade_lifecycle_only"
TOKEN = "phase3-self-hosted-harbor-token"
TASK_NAME = "phase3-harbor-smoke"


class HarborFacadeHandler(BaseHTTPRequestHandler):
    server_version = "Phase3SelfHostedHarbor/1.0"

    def _server_state(self) -> dict[str, Any]:
        return self.server.state  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _authorized(self) -> bool:
        expected = "Bearer " + str(self._server_state()["token"])
        return self.headers.get("Authorization") == expected

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(401, {"ok": False, "error": "unauthorized"})
        return False

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib hook name.
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name.
        if not self._require_auth():
            return
        state = self._server_state()
        if self.path == "/health":
            self._send_json(200, {"ok": True, "dataset": state["dataset"], "backend": "self_hosted_harbor_facade"})
        elif self.path == "/metrics.json":
            self._send_json(200, {"ok": True, "requests": state["requests"], "trials": len(state["trials"])})
        elif self.path == "/list_tasks":
            self._send_json(200, [state["task_name"]])
        elif self.path.startswith("/trial/"):
            trial_id = self.path.removeprefix("/trial/")
            trial = state["trials"].get(trial_id)
            if not trial:
                self._send_json(404, {"ok": False, "error": "trial_not_found"})
            else:
                self._send_json(200, trial)
        else:
            self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name.
        if not self._require_auth():
            return
        state = self._server_state()
        state["requests"] += 1
        payload = self._read_json()
        if self.path == "/score":
            passed = payload.get("task_name") == state["task_name"]
            self._send_json(200, {"ok": True, "score": 1.0 if passed else 0.0, "passed": passed})
        elif self.path == "/trial/create":
            if payload.get("task_name") != state["task_name"]:
                self._send_json(404, {"ok": False, "error": "task_not_found"})
                return
            trial_id = uuid.uuid4().hex
            state["trials"][trial_id] = {"trial_id": trial_id, "task_name": state["task_name"], "status": "created", "stdout": "", "answer": "", "n_exec_calls": 0}
            self._send_json(200, {"ok": True, "trial_id": trial_id, "task_name": state["task_name"]})
        elif self.path.startswith("/trial/") and self.path.endswith("/exec"):
            trial_id = self.path.split("/")[2]
            trial = state["trials"].get(trial_id)
            if not trial:
                self._send_json(404, {"ok": False, "error": "trial_not_found"})
                return
            trial["status"] = "executed"
            trial["stdout"] = "phase3-harbor-proof"
            trial["cmd"] = payload.get("cmd", "")
            trial["n_exec_calls"] = int(trial.get("n_exec_calls") or 0) + 1
            self._send_json(200, {"ok": True, "trial_id": trial_id, "stdout": trial["stdout"], "returncode": 0})
        elif self.path.startswith("/trial/") and self.path.endswith("/finalize"):
            trial_id = self.path.split("/")[2]
            trial = state["trials"].get(trial_id)
            if not trial:
                self._send_json(404, {"ok": False, "error": "trial_not_found"})
                return
            trial["status"] = "finalized"
            trial["answer"] = payload.get("answer", "")
            self._send_json(200, {"ok": True, "trial_id": trial_id, "reward": 1.0, "status": trial["status"]})
        else:
            self._send_json(404, {"ok": False, "error": "not_found"})


def _start_server(*, token: str, task_name: str) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), HarborFacadeHandler)
    server.state = {"token": token, "task_name": task_name, "dataset": "phase3-self-hosted", "requests": 0, "trials": {}}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _component(*, target_run_id: str, provider_report_path: Path, provider_report: dict[str, Any]) -> dict[str, Any]:
    facade_passed = provider_report.get("passed") is True
    return {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "p3_aux_harbor_local_facade",
        "milestone_id": "P3-M9",
        "component": "harbor_local_facade",
        "claim_boundary": HARBOR_BLOCKED_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "points": 0,
        "passed": False,
        "blocked_reason": "local_facade_not_target_harbor_nemo_evidence" if facade_passed else str(provider_report.get("blocked_reason") or "harbor_self_hosted_report_failed"),
        "provider_report": provider_report,
        "provider_kind": "local_harbor_facade",
        "input_hashes": {"provider_report": sha256_file(provider_report_path)},
        "artifact_paths": {"provider_report": str(provider_report_path)},
        "required_artifact_keys": ["provider_report"],
        "scorecard_update_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--task-name", default=TASK_NAME)
    args = parser.parse_args()

    out = args.output_dir or args.phase_dir / "runs" / "self_hosted_harbor"
    out.mkdir(parents=True, exist_ok=True)
    env_package = out / "self_hosted_harbor_env_package.tar"
    env_package.write_bytes(b"phase3 self-hosted harbor env package\n")

    server, base_url = _start_server(token=TOKEN, task_name=args.task_name)
    previous = {key: os.environ.get(key) for key in ("BREADBOARD_HARBOR_BASE_URL", "BREADBOARD_HARBOR_TOKEN", "BREADBOARD_HARBOR_TASK_NAME", "BREADBOARD_HARBOR_ALLOW_LOCAL")}
    os.environ["BREADBOARD_HARBOR_BASE_URL"] = base_url
    os.environ["BREADBOARD_HARBOR_TOKEN"] = TOKEN
    os.environ["BREADBOARD_HARBOR_TASK_NAME"] = args.task_name
    os.environ["BREADBOARD_HARBOR_ALLOW_LOCAL"] = "1"
    try:
        time.sleep(0.05)
        provider_report = run_harbor_service_proof(env_package, target_run_id=args.target_run_id, task_name=args.task_name)
    finally:
        server.shutdown()
        server.server_close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    provider_report["self_hosted"] = True
    provider_report["self_hosted_base_url"] = base_url
    provider_report["claim_boundary"] = LOCAL_FACADE_CLAIM_BOUNDARY
    provider_report["promotional"] = False
    provider_report["target_endpoint_proven"] = False
    provider_report["auth_proven"] = False
    provider_path = out / "harbor_service_proof.json"
    write_phase3_json(provider_path, provider_report)
    component = _component(target_run_id=args.target_run_id, provider_report_path=provider_path, provider_report=provider_report)
    component_path = out / "P3-M9_harbor_nemo_gym.json"
    write_phase3_json(component_path, component)
    local_facade_passed = provider_report.get("passed") is True
    summary = {
        "schema_version": SCHEMA,
        "report_id": REPORT_ID,
        "target_run_id": args.target_run_id,
        "base_url": base_url,
        "task_name": args.task_name,
        "provider_report": str(provider_path),
        "component_report": str(component_path),
        "local_facade_passed": local_facade_passed,
        "passed": local_facade_passed,
        "promotional": False,
        "blocked_reason": "" if local_facade_passed else component["blocked_reason"],
    }
    write_phase3_json(out / "phase3_self_hosted_harbor_facade.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if local_facade_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
