from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest, SessionStatus
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord, SessionRegistry
from agentic_coder_prototype.api.cli_bridge.runtime_emission import emit_session_start_records
from agentic_coder_prototype.api.cli_bridge.service import SessionService
from agentic_coder_prototype.api.cli_bridge.session_runner import SessionRunner
from agentic_coder_prototype.compilation.effective_operation_policy import (
    comparable_policy_pack_projection,
    compile_effective_operation_policy,
    policy_pack_for_config_authority,
    policy_pack_from_effective_record,
)
from agentic_coder_prototype.compilation.v2_loader import load_agent_config
from agentic_coder_prototype.permissions.policy_pack import PolicyPack


FIXTURE_CONFIGS = [
    "agent_configs/atp_hilbert_like_gpt54_v1.yaml",
    "agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml",
]


def _policy_config() -> dict[str, object]:
    return {
        "version": 2,
        "policies": {
            "tools": {"allow": ["read_file", "write"], "deny": ["shell"]},
            "models": {"allow": ["openrouter/openai/*"], "deny": ["openai/gpt-4.1"]},
            "network": {"allow": ["https://example.com/*"], "deny": ["http://*"]},
            "budgets": {"token_budget": 1000, "cost_budget_usd": 1.5, "wall_time_seconds": 60},
            "limits": {"concurrency": 2, "file_bytes": 4096, "network_bytes": 8192, "tool_calls": 12},
            "approvals": {"mode": "on_demand", "approver_scope": "operator"},
        },
    }


def test_compiler_output_validates_and_is_deterministic() -> None:
    config = _policy_config()

    first = compile_effective_operation_policy(
        config,
        session_id="sess-policy",
        config_path="fixture.yaml",
        generated_at_utc="2026-07-04T00:00:00Z",
    )
    second = compile_effective_operation_policy(
        config,
        session_id="sess-policy",
        config_path="fixture.yaml",
        generated_at_utc="2026-07-04T00:00:00Z",
    )

    assert first == second
    assert first["schema_version"] == "bb.effective_operation_policy.v1"
    assert first["tool_policy"]["default_decision"] == "deny"
    assert first["resource_policy"]["rules"][0]["match"] == {
        "kind": "model",
        "pattern": "openrouter/openai/*",
        "scope_ref": None,
    }
    assert first["budgets"] == {"token_budget": 1000, "cost_budget_usd": 1.5, "wall_time_seconds": 60}
    assert first["limits"] == {"concurrency": 2, "file_bytes": 4096, "network_bytes": 8192, "tool_calls": 12}
    assert first["approvals"]["mode"] == "on_demand"

def test_compiler_preserves_explicit_zero_budgets_and_limits() -> None:
    config = {
        "version": 2,
        "policies": {
            "budgets": {
                "token_budget": 0,
                "tokens": 500,
                "cost_budget_usd": 0,
                "wall_time_seconds": 0,
            },
            "limits": {
                "concurrency": 0,
                "max_concurrency": 3,
                "file_bytes": 0,
                "network_bytes": 0,
                "tool_calls": 0,
            },
        },
    }

    record = compile_effective_operation_policy(
        config,
        session_id="sess-zero-policy",
        config_path="fixture.yaml",
        generated_at_utc="2026-07-04T00:00:00Z",
    )

    assert record["budgets"] == {"token_budget": 0, "cost_budget_usd": 0, "wall_time_seconds": 0}
    assert record["limits"] == {"concurrency": 0, "file_bytes": 0, "network_bytes": 0, "tool_calls": 0}


def test_policy_pack_round_trip_matches_fixture_configs() -> None:
    for config_path in FIXTURE_CONFIGS:
        config = load_agent_config(config_path)
        record = compile_effective_operation_policy(
            config,
            session_id=Path(config_path).stem,
            config_path=config_path,
            generated_at_utc="2026-07-04T00:00:00Z",
        )

        assert comparable_policy_pack_projection(policy_pack_from_effective_record(record)) == comparable_policy_pack_projection(
            PolicyPack.from_config(config)
        )


def test_work_items_reference_emitted_operation_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime_records"
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(runtime_root))

    paths = emit_session_start_records(
        session_id="policy-ref-session",
        request=SessionCreateRequest(
            config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml",
            task="policy ref probe",
        ),
        generated_at="2026-07-04T00:00:00Z",
    )

    policy_path = Path(paths["effective_operation_policy"])
    digest = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    expected_ref = f"effective_operation_policy.json#sha256:{digest}"
    config_plane = runtime_root / "policy-ref-session" / "records" / "config_plane.jsonl"
    envelopes = [json.loads(line) for line in config_plane.read_text(encoding="utf-8").splitlines()]
    work_item_envelopes = [row for row in envelopes if row["name"].startswith("work_item_")]
    assert [row["name"] for row in work_item_envelopes] == [
        "work_item_created",
        "work_item_lease_acquired",
        "work_item_attempt_started",
        "work_item_snapshot",
    ]
    assert {row["operation_policy_ref"] for row in work_item_envelopes} == {expected_ref}
    assert {tuple(sorted(row["correlation"].items())) for row in work_item_envelopes} == {
        (("run_id", "policy-ref-session"), ("session_id", "policy-ref-session"), ("work_item_id", "policy-ref-session"))
    }


def test_runner_metadata_merge_preserves_service_keys_with_request_precedence() -> None:
    registry = SessionRegistry()
    session = SessionRecord(
        session_id="sess-meta",
        status=SessionStatus.STARTING,
        metadata={"config_path": "service.yaml", "runtime_records": {"effective_operation_policy": "p"}, "mode": "service"},
    )
    request = SessionCreateRequest(
        config_path="request.yaml",
        task="hi",
        metadata={"mode": "request", "request_only": True},
    )

    SessionRunner(session=session, registry=registry, request=request)

    assert session.metadata["runtime_records"] == {"effective_operation_policy": "p"}
    assert session.metadata["config_path"] == "service.yaml"
    assert session.metadata["request_only"] is True
    assert session.metadata["mode"] == "request"


@pytest.mark.asyncio
async def test_policy_authority_unset_keeps_emission_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BREADBOARD_POLICY_AUTHORITY", raising=False)
    monkeypatch.delenv("BREADBOARD_EMIT_PRIMITIVES", raising=False)
    monkeypatch.setenv("BREADBOARD_RUNTIME_RECORD_ROOT", str(tmp_path / "runtime_records"))

    monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.service.SessionRunner.schedule_start", lambda _runner: None)
    monkeypatch.setattr("agentic_coder_prototype.api.cli_bridge.service.SessionRunner.authorize_start", lambda _runner: None)
    service = SessionService(SessionRegistry())

    response = await service.create_session(
        SessionCreateRequest(
            config_path="agent_configs/atp_hilbert_like_gpt54_v1.yaml",
            task="emission off",
            metadata={"client": "test"},
        )
    )
    record = await service.registry.get(response.session_id)

    assert record is not None
    assert "runtime_records" not in record.metadata
    assert record.metadata["client"] == "test"
    assert not (tmp_path / "runtime_records").exists()


def test_parity_authority_logs_no_divergence_for_fixture(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("BREADBOARD_POLICY_AUTHORITY", "parity")
    config_path = "agent_configs/atp_hilbert_like_gpt54_v1.yaml"
    config = load_agent_config(config_path)

    with caplog.at_level("WARNING"):
        policy_pack_for_config_authority(
            config,
            session_id="parity-log-session",
            config_path=config_path,
        )

    assert [record.getMessage() for record in caplog.records if "Effective operation policy divergence" in record.getMessage()] == []
