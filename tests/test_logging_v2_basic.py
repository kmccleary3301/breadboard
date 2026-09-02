import json
import os
from pathlib import Path

from breadboard_engine.run_logging import LoggerV2Manager
from breadboard_engine.run_logging.api_recorder import APIRequestRecorder
from breadboard_engine.run_logging.prompt_logger import PromptArtifactLogger
from breadboard_engine.run_logging.request_recorder import StructuredRequestRecorder


def test_logger_v2_creates_run_tree(tmp_path):
    cfg = {
        "logging": {
            "root_dir": str(tmp_path / "logging"),
            "redact": True,
            "include_raw": True,
        }
    }
    lm = LoggerV2Manager(cfg)
    run = lm.start_run("session-xyz")
    runp = Path(run)
    # Ensure key subfolders
    assert (runp / "conversation").exists()
    assert (runp / "raw/requests").exists()
    assert (runp / "prompts/per_turn").exists()
    # Write some artifacts
    rec = APIRequestRecorder(lm)
    rec.save_request(1, {"model": "gpt-5-nano", "api_key": "SECRET"})
    rec.save_response(1, {"status": "ok", "authorization": "Bearer SECRET"})
    pl = PromptArtifactLogger(lm)
    pl.save_compiled_system("SYSTEM PROMPT")
    pl.save_per_turn(1, "TOOLS AVAIL")
    sr = StructuredRequestRecorder(lm)
    sr.record_request(
        1,
        provider_id="openrouter",
        runtime_id="openrouter_chat",
        model="m",
        request_headers={
            "Authorization": "Bearer SECRET",
            "Accept": "application/json",
        },
        request_body={"model": "m", "messages": [{"role": "user", "content": "Hello"}]},
        stream=False,
        tool_count=0,
        endpoint="https://example.com",
    )
    sr.record_request(
        2,
        provider_id="openrouter",
        runtime_id="openrouter_chat",
        model="m",
        request_headers={},
        request_body="x" * 3000,
        stream=True,
        tool_count=1,
        endpoint=None,
    )
    # Files should be created and redacted
    req = (runp / "raw/requests/turn_1.request.json").read_text()
    assert "REDACTED" in req
    resp = (runp / "raw/responses/turn_1.response.json").read_text()
    assert "REDACTED" in resp
    sys = (runp / "prompts/compiled_system.md").read_text()
    assert "SYSTEM PROMPT" in sys
    structured = json.loads((runp / "meta/requests/turn_1.json").read_text())
    assert structured["request"]["headers"]["Authorization"] == "***REDACTED***"
    assert structured["request"]["headers"]["Accept"] == "application/json"
    assert structured["request"]["body_length"] >= len("Hello")
    assert structured["request"]["body_excerpt"]
    long_req = json.loads((runp / "meta/requests/turn_2.json").read_text())
    assert long_req["request"]["body_truncated"] is True
    assert len(long_req["request"]["body_excerpt"]) == 2048




def test_logger_rejects_late_symlink_and_hardlink_targets(tmp_path):
    manager = LoggerV2Manager({"logging": {"root_dir": str(tmp_path / "logging")}})
    run = Path(manager.start_run("workspace-adversary"))

    symlink_secret = tmp_path / "symlink-secret"
    symlink_secret.write_text("symlink-canary", encoding="utf-8")
    symlink_target = run / "conversation" / "late-write.txt"
    symlink_target.symlink_to(symlink_secret)
    assert manager.write_text("conversation/late-write.txt", "replacement") == ""
    assert symlink_secret.read_text(encoding="utf-8") == "symlink-canary"

    hardlink_secret = tmp_path / "hardlink-secret"
    hardlink_secret.write_text("hardlink-canary", encoding="utf-8")
    hardlink_target = run / "conversation" / "late-append.txt"
    os.link(hardlink_secret, hardlink_target)
    assert manager.append_text("conversation/late-append.txt", "replacement") == ""
    assert hardlink_secret.read_text(encoding="utf-8") == "hardlink-canary"
