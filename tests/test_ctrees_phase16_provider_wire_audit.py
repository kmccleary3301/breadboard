from __future__ import annotations

import json

from agentic_coder_prototype.ctrees.phase16_provider_wire_audit import summarize_provider_wire_log
from agentic_coder_prototype.logging.provider_dump import ProviderDumpLogger


def test_provider_wire_audit_reads_required_tool_choice_and_tool_names(tmp_path) -> None:
    log_dir = tmp_path / "provider_dump"
    log_dir.mkdir()
    payload = {
        "requestId": "req-1",
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "body": {
            "json": {
                "tools": [
                    {"type": "function", "function": {"name": "shell_command"}},
                    {"type": "function", "function": {"name": "apply_patch"}},
                ],
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "metadata": {"phase_label": "phase16_action_probe"},
            }
        },
        "metadata": {},
    }
    (log_dir / "0001_request.json").write_text(json.dumps(payload), encoding="utf-8")

    summary = summarize_provider_wire_log(log_dir)

    assert summary["request_count"] == 1
    assert summary["requests_with_tools"] == 1
    assert summary["requests_with_required_tool_choice"] == 1
    assert summary["phase_labels"] == ["phase16_action_probe"]
    assert summary["rows"][0]["tool_names"] == ["shell_command", "apply_patch"]


def test_provider_wire_audit_accepts_name_style_tools_and_metadata_phase() -> None:
    payload_dir = {
        "requestId": "req-2",
        "provider": "anthropic",
        "model": "claude-test",
        "body": {
            "json": {
                "tools": [{"name": "shell_command"}, {"name": "apply_patch"}],
                "tool_choice": {"type": "tool", "name": "shell_command"},
            }
        },
        "metadata": {"phase": "phase16_smoke"},
    }

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        log_dir = Path(tmp)
        (log_dir / "0002_request.json").write_text(json.dumps(payload_dir), encoding="utf-8")
        summary = summarize_provider_wire_log(log_dir)

    assert summary["request_count"] == 1
    assert summary["requests_with_tools"] == 1
    assert summary["requests_with_required_tool_choice"] == 0
    assert summary["phase_labels"] == ["phase16_smoke"]


def test_provider_dump_logger_refreshes_log_dir_from_env(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("KC_PROVIDER_LOG_DIR", str(first))
    logger = ProviderDumpLogger()
    logger.log_request(provider="openai", model="m", payload={"x": 1}, context=None)

    monkeypatch.setenv("KC_PROVIDER_LOG_DIR", str(second))
    logger.log_request(provider="openai", model="m", payload={"x": 2}, context=None)

    assert len(list(first.glob("*_request.json"))) == 1
    assert len(list(second.glob("*_request.json"))) == 1


def test_provider_wire_audit_falls_back_to_structured_requests_when_dump_empty(tmp_path) -> None:
    log_dir = tmp_path / "provider_dump"
    log_dir.mkdir()
    run_root = tmp_path / "run"
    requests_dir = run_root / "meta" / "requests"
    tools_dir = run_root / "provider_native" / "tools_provided"
    requests_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)

    request_payload = {
        "turn": 1,
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "tool_count": 1,
        "request": {
            "body_excerpt": '{"model":"gpt-5.4-mini","tool_choice":"required","parallel_tool_calls":false,"tools":[{"type":"function","function":{"name":"apply_patch"}}]}'
        },
        "extra": {"has_tools": True},
    }
    tools_payload = [
        {
            "type": "function",
            "function": {"name": "apply_patch"},
        }
    ]
    (requests_dir / "turn_1.json").write_text(json.dumps(request_payload), encoding="utf-8")
    (tools_dir / "turn_1.json").write_text(json.dumps(tools_payload), encoding="utf-8")

    summary = summarize_provider_wire_log(log_dir, run_root=run_root)

    assert summary["source_kind"] == "structured_request"
    assert summary["request_count"] == 1
    assert summary["requests_with_tools"] == 1
    assert summary["requests_with_required_tool_choice"] == 1
    assert summary["rows"][0]["tool_names"] == ["apply_patch"]
    assert summary["rows"][0]["parallel_tool_calls"] is False
