from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentic_coder_prototype.compat.request_body_contract import (  # noqa: E402
    capture_request_body,
    default_request_body_cases,
    serialize_request,
)


def _ensure_env() -> None:
    os.environ.setdefault("RAY_SCE_LOCAL_MODE", "1")
    os.environ.setdefault("AGENT_SCHEMA_V2_ENABLED", "1")
    os.environ.setdefault("OPENAI_API_KEY", "compat-dummy")
    os.environ.setdefault("ANTHROPIC_API_KEY", "compat-dummy")
    os.environ.setdefault("OPENROUTER_API_KEY", "compat-dummy")


def _write_fixture(out_dir: Path, case_id: str, payload_bytes: bytes) -> Path:
    target = out_dir / case_id / "basic.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload_bytes)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump request-body golden fixtures for harness compatibility checks.")
    parser.add_argument(
        "--out-dir",
        default="tests/fixtures/compat/request_bodies",
        help="Output directory for request body fixtures.",
    )
    args = parser.parse_args()

    _ensure_env()
    out_dir = Path(args.out_dir).resolve()
    repo_root = REPO_ROOT

    compat_root = Path(os.environ.get("BB_COMPAT_WORKSPACE_ROOT", "/tmp/bb_compat_workspaces"))

    for case in default_request_body_cases():
        case_id = case["id"]
        config_path = str(repo_root / case["config_path"])
        workspace_root = compat_root / case_id
        captured = capture_request_body(
            config_path=config_path,
            workspace_root=workspace_root,
            user_prompt=case.get("user_prompt", "Hello from compat harness."),
            system_prompt=case.get("system_prompt", ""),
        )
        payload_bytes = serialize_request(captured.payload)
        target = _write_fixture(out_dir, case_id, payload_bytes)
        digest = hashlib.sha256(payload_bytes).hexdigest()
        print(f"[compat] {case_id}: {target} sha256={digest}")


if __name__ == "__main__":
    main()
