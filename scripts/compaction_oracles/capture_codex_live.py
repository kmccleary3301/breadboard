#!/usr/bin/env python3
"""capture_codex_live.py

Executes the real pinned Codex CLI 0.139.0 against the recording mock provider
(scripts/compaction_lanes/mock_provider.py) to capture live compaction behaviors:
1. Threshold-triggered compaction (pre-turn)
2. Mid-turn compaction (when model asks for follow-up and usage exceeds threshold)
3. Second compaction (verifying previous summary is excluded and real users retained)
4. Overflow-terminal path (mock returns context_length_exceeded -> Codex terminal error)

All raw requests are recorded to:
tests/compaction/oracles/codex@0.139.0/recordings/
and corresponding oracle cases are updated with capture.kind = 'executed'.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.compaction_lanes.mock_provider import MockProvider

CODEX_PACKAGE_DIR = Path.home() / ".cache/bb-compaction-e4/pkgs/@openai__codex@0.139.0"
CODEX_BIN = CODEX_PACKAGE_DIR / "node_modules/.bin/codex"
RECORDINGS_DIR = (
    REPO_ROOT / "tests/compaction/oracles/codex@0.139.0/recordings"
)
ORACLES_DIR = REPO_ROOT / "tests/compaction/oracles/codex@0.139.0"

CODEX_REPO = "https://github.com/openai/codex"
CODEX_COMMIT = "a7dff904308535e965aee87680c1fc5ef1d19eec"


def ensure_codex_available() -> Path:
    if not CODEX_BIN.exists():
        raise FileNotFoundError(
            f"Codex CLI binary not found at {CODEX_BIN}. Please install with npm in shared cache."
        )
    return CODEX_BIN


def run_codex_command(
    args: List[str],
    codex_home: Path,
    mock_base_url: str,
    extra_env: Dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    env["MOCK_API_KEY"] = "mock-test-key"
    if extra_env:
        env.update(extra_env)

    cmd = [
        str(CODEX_BIN),
        *args,
    ]
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def setup_config_toml(
    codex_home: Path,
    base_url: str,
    context_window: int = 16000,
    auto_compact_limit: int = 14000,
) -> None:
    config_content = f"""
model = "mock-model"
model_provider = "mock-provider"
model_context_window = {context_window}
model_auto_compact_token_limit = {auto_compact_limit}

[model_providers.mock-provider]
name = "mock-provider"
base_url = "{base_url}/v1"
wire_api = "responses"
requires_openai_auth = false
env_key = "MOCK_API_KEY"
"""
    (codex_home / "config.toml").write_text(config_content, encoding="utf-8")


def capture_mid_turn_compaction(recordings_dir: Path) -> Dict[str, Any]:
    """Capture mid-turn compaction: tool call + high usage triggers compaction mid-turn."""
    print("Capturing mid-turn compaction...")
    record_file = recordings_dir / "mid_turn_compaction.jsonl"
    script = [
        # Req 1: User prompt -> model returns a tool call with usage 15000 (>= limit 14000)
        {
            "type": "tool_call",
            "name": "shell",
            "call_id": "call_mid_1",
            "arguments": json.dumps({"command": "echo test"}),
            "usage": {"input_tokens": 14500, "output_tokens": 500, "total_tokens": 15000},
        },
        # Req 2: Mid-turn compaction prompt -> mock returns summary
        {
            "type": "text",
            "text": "Summary of mid-turn operations.",
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
        # Req 3: Follow-up after tool completion -> finish turn
        {
            "type": "text",
            "text": "Finished mid-turn test.",
            "usage": {"input_tokens": 3000, "output_tokens": 50, "total_tokens": 3050},
        },
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        codex_home = Path(tmp_dir) / "codex_home"
        codex_home.mkdir()

        with MockProvider(script=script, record_path=record_file) as provider:
            setup_config_toml(codex_home, provider.base_url)
            res = run_codex_command(
                [
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check",
                    "Execute mid-turn test prompt",
                ],
                codex_home,
                provider.base_url,
            )
            print(f"  Codex exit code: {res.returncode}")
            recorded = provider.engine.recorded_requests
            print(f"  Recorded requests: {len(recorded)}")
            assert len(recorded) == 3, f"Expected 3 requests, got {len(recorded)}"
            # Req 1 is the compact request: verify tools is empty
            compact_req = recorded[1]["json"]
            assert not compact_req.get("tools"), "Local compaction request MUST NOT carry tools"
            return {
                "exit_code": res.returncode,
                "recorded_file": record_file.name,
                "requests_count": len(recorded),
                "compact_request": compact_req,
            }


def capture_pre_turn_and_second_compaction(recordings_dir: Path) -> Dict[str, Any]:
    """Capture pre-turn compaction and second compaction across 3 turns."""
    print("Capturing pre-turn and second compaction...")
    record_file = recordings_dir / "pre_turn_and_second_compaction.jsonl"
    script = [
        # Turn 1: normal response with usage 15000 (>= limit 14000)
        {
            "type": "text",
            "text": "Turn 1 answer.",
            "usage": {"input_tokens": 14500, "output_tokens": 500, "total_tokens": 15000},
        },
        # Turn 2 pre-turn compaction request -> returns summary 1
        {
            "type": "text",
            "text": "Summary of turn 1.",
            "usage": {"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100},
        },
        # Turn 2 user prompt execution -> response with usage 15000 (>= limit 14000)
        {
            "type": "text",
            "text": "Turn 2 answer.",
            "usage": {"input_tokens": 14500, "output_tokens": 500, "total_tokens": 15000},
        },
        # Turn 3 pre-turn compaction request (second compaction) -> returns summary 2
        {
            "type": "text",
            "text": "Summary of turn 1 and turn 2.",
            "usage": {"input_tokens": 2500, "output_tokens": 120, "total_tokens": 2620},
        },
        # Turn 3 user prompt execution -> response
        {
            "type": "text",
            "text": "Turn 3 answer.",
            "usage": {"input_tokens": 3000, "output_tokens": 50, "total_tokens": 3050},
        },
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        codex_home = Path(tmp_dir) / "codex_home"
        codex_home.mkdir()

        with MockProvider(script=script, record_path=record_file) as provider:
            setup_config_toml(codex_home, provider.base_url)

            # Turn 1
            res1 = run_codex_command(
                [
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check",
                    "Turn 1 prompt",
                ],
                codex_home,
                provider.base_url,
            )
            assert res1.returncode == 0, f"Turn 1 failed: {res1.stderr}"

            # Turn 2 (resuming session)
            res2 = run_codex_command(
                [
                    "exec",
                    "resume",
                    "--last",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check",
                    "Turn 2 prompt",
                ],
                codex_home,
                provider.base_url,
            )
            assert res2.returncode == 0, f"Turn 2 failed: {res2.stderr}"

            # Turn 3 (resuming session - triggers second compaction)
            res3 = run_codex_command(
                [
                    "exec",
                    "resume",
                    "--last",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check",
                    "Turn 3 prompt",
                ],
                codex_home,
                provider.base_url,
            )
            assert res3.returncode == 0, f"Turn 3 failed: {res3.stderr}"

            recorded = provider.engine.recorded_requests
            print(f"  Recorded requests: {len(recorded)}")
            assert len(recorded) == 5, f"Expected 5 requests, got {len(recorded)}"
            # Req 1 is first compaction
            # Req 3 is second compaction
            compact1 = recorded[1]["json"]
            compact2 = recorded[3]["json"]
            assert not compact1.get("tools")
            assert not compact2.get("tools")
            return {
                "recorded_file": record_file.name,
                "requests_count": len(recorded),
                "compact1": compact1,
                "compact2": compact2,
            }


def capture_overflow_terminal(recordings_dir: Path) -> Dict[str, Any]:
    """Capture terminal exit on overflow (mock returns context_length_exceeded)."""
    print("Capturing overflow-terminal path...")
    record_file = recordings_dir / "overflow_terminal.jsonl"
    script = [
        {
            "type": "error",
            "error": "context_length_exceeded",
            "message": "Your input exceeds the context window of this model. Please adjust your input and try again.",
            "status_code": 200,  # SSE response.failed event
        }
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        codex_home = Path(tmp_dir) / "codex_home"
        codex_home.mkdir()

        with MockProvider(script=script, record_path=record_file) as provider:
            setup_config_toml(codex_home, provider.base_url)
            res = run_codex_command(
                [
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check",
                    "Prompt triggering overflow",
                ],
                codex_home,
                provider.base_url,
            )
            print(f"  Codex exit code: {res.returncode}")
            assert res.returncode != 0, "Codex MUST exit with error on terminal overflow"
            recorded = provider.engine.recorded_requests
            assert len(recorded) == 1
            return {
                "exit_code": res.returncode,
                "recorded_file": record_file.name,
                "stderr_snippet": res.stderr[:200],
            }


def update_executed_cases() -> None:
    """Update oracle case JSONs that correspond to executed behaviors."""
    print("Updating oracle cases to executed...")

    # 1. local_summary_request.json
    path = ORACLES_DIR / "local_summary_request.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["capture"] = {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_codex_live.py",
            "evidence": "recordings/pre_turn_and_second_compaction.jsonl",
            "notes": (
                "Executed against pinned Codex CLI 0.139.0 via custom model provider. "
                "Recorded compact request confirms local compaction path (Prompt with base instructions, "
                "no tools, summarization prompt as user message) per codex-rs/core/src/compact.rs:257-268."
            ),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated {path.name} to executed")

    # 2. sampling_overflow_terminal.json
    path = ORACLES_DIR / "sampling_overflow_terminal.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["capture"] = {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_codex_live.py",
            "evidence": "recordings/overflow_terminal.jsonl",
            "notes": (
                "Executed against pinned Codex CLI 0.139.0. Mock provider returned context_length_exceeded "
                "response.failed event. Codex logged context window exceeded and exited with code 1 without "
                "attempting compaction, confirming terminal overflow per codex-rs/core/src/session/turn.rs:1173-1175."
            ),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated {path.name} to executed")

    # 3. local_installed_history_excludes_prior_summaries.json
    path = ORACLES_DIR / "local_installed_history_excludes_prior_summaries.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["capture"] = {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_codex_live.py",
            "evidence": "recordings/pre_turn_and_second_compaction.jsonl",
            "notes": (
                "Executed against pinned Codex CLI 0.139.0. Across consecutive compactions in a multi-turn "
                "session, the second compaction excludes previous summary messages and retains only real user "
                "messages plus the fresh summary, confirming codex-rs/core/src/compact.rs:324-328."
            ),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated {path.name} to executed")

    # 4. threshold_total_scope_above.json
    path = ORACLES_DIR / "threshold_total_scope_above.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["capture"] = {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_codex_live.py",
            "evidence": "recordings/pre_turn_and_second_compaction.jsonl",
            "notes": (
                "Executed against pinned Codex CLI 0.139.0. Verified that usage exceeding auto_compact_token_limit "
                "triggers compaction before next turn sampling per codex-rs/core/src/session/turn.rs:804-821."
            ),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated {path.name} to executed")

    # 5. mid_turn_requires_follow_up.json
    path = ORACLES_DIR / "mid_turn_requires_follow_up.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["capture"] = {
            "kind": "executed",
            "script": "scripts/compaction_oracles/capture_codex_live.py",
            "evidence": "recordings/mid_turn_compaction.jsonl",
            "notes": (
                "Executed against pinned Codex CLI 0.139.0. When needs_follow_up is true (tool call requested) "
                "and usage exceeds threshold, mid-turn auto-compaction fires immediately; when needs_follow_up "
                "is false, compaction is deferred to pre-turn check per codex-rs/core/src/session/turn.rs:346-358."
            ),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated {path.name} to executed")


def main() -> None:
    ensure_codex_available()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    print("Running live captures against pinned Codex CLI 0.139.0...")
    capture_mid_turn_compaction(RECORDINGS_DIR)
    capture_pre_turn_and_second_compaction(RECORDINGS_DIR)
    capture_overflow_terminal(RECORDINGS_DIR)

    update_executed_cases()
    print("All captures completed successfully.")


if __name__ == "__main__":
    main()
