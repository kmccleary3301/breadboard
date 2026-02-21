from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


class _FakeSessionState:
    def __init__(self) -> None:
        self._meta: Dict[str, Any] = {}
        self.task_events = []

    def get_provider_metadata(self, key: str) -> Any:
        return self._meta.get(key)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self._meta[key] = value

    def emit_task_event(self, payload: Dict[str, Any]) -> None:
        self.task_events.append(dict(payload))


def _make_conductor(config: dict, workspace: Path, session_state: _FakeSessionState | None = None) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.workspace = str(workspace)
    inst._active_session_state = session_state
    return inst  # type: ignore[return-value]


def test_rlm_branch_ids_follow_longrun_episode_lineage(tmp_path: Path) -> None:
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("longrun_episode_index", 5)
    conductor = _make_conductor({"features": {"rlm": {"enabled": True}}}, tmp_path, session_state)
    assert conductor._rlm_resolve_branch_id("root") == "ep.5.root"
    assert conductor._rlm_resolve_branch_id("task.child") == "ep.5.task.child"


def test_rlm_projects_task_events_into_branch_and_hybrid_artifacts(tmp_path: Path) -> None:
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("longrun_episode_index", 2)
    conductor = _make_conductor({"features": {"rlm": {"enabled": True}}}, tmp_path, session_state)

    conductor._emit_task_event(
        {
            "kind": "subagent_spawned",
            "task_id": "abc123",
            "sessionId": "sess-1",
            "subagent_type": "explore",
            "description": "scan workspace",
            "depth": 1,
        }
    )
    conductor._emit_task_event(
        {
            "kind": "subagent_completed",
            "task_id": "abc123",
            "sessionId": "sess-1",
            "subagent_type": "explore",
            "description": "scan workspace",
            "depth": 1,
        }
    )

    ledger_path = tmp_path / ".breadboard" / "meta" / "rlm_branches.json"
    assert ledger_path.exists()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    branches = ledger.get("branches") or {}
    assert "ep.2.task.abc123" in branches
    assert branches["ep.2.task.abc123"]["status"] in {"completed", "merged"}

    hybrid_path = tmp_path / ".breadboard" / "meta" / "rlm_hybrid_events.jsonl"
    assert hybrid_path.exists()
    rows = [
        json.loads(line)
        for line in hybrid_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 2
    seqs = [int(row.get("seq") or 0) for row in rows]
    assert seqs == sorted(seqs)
    assert any(str(row.get("kind")) == "task_event" for row in rows)

    projection_path = tmp_path / ".breadboard" / "meta" / "ctrees" / "rlm_projection.jsonl"
    assert projection_path.exists()


def test_rlm_llm_query_budget_block_records_episode_prefixed_branch(tmp_path: Path) -> None:
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("longrun_episode_index", 7)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "budget": {"max_subcalls": 1},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path, session_state)
    session_state.set_provider_metadata(
        "rlm_budget_state",
        {"started_at": 1.0, "subcalls": 1, "total_tokens": 0, "total_cost_usd": 0.0},
    )
    out = conductor._exec_raw({"function": "llm.query", "arguments": {"prompt": "hello world"}})
    assert out.get("reason") == "subcall_limit_exceeded"

    ledger_path = tmp_path / ".breadboard" / "meta" / "rlm_branches.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    branches = ledger.get("branches") or {}
    assert "ep.7.root" in branches
    meta = branches["ep.7.root"].get("metadata") or {}
    assert meta.get("lane") in {"tool_heavy", "long_context", "balanced"}


def test_rlm_batch_query_records_episode_prefixed_lineage_and_artifacts(tmp_path: Path) -> None:
    session_state = _FakeSessionState()
    session_state.set_provider_metadata("longrun_episode_index", 3)
    cfg = {
        "features": {
            "rlm": {
                "enabled": True,
                "scheduling": {"mode": "batch", "batch": {"enabled": True, "max_concurrency": 2}},
            }
        }
    }
    conductor = _make_conductor(cfg, tmp_path, session_state)
    out = conductor._exec_raw(
        {
            "function": "llm.batch_query",
            "arguments": {"queries": [{"prompt": "a"}, {"prompt": "b"}]},
            "expected_output": [{"text": "A"}, {"text": "B"}],
            "expected_status": "completed",
        }
    )
    assert out.get("item_count") == 2
    rows = out.get("results") or []
    assert rows[0].get("request_index") == 0
    assert rows[1].get("request_index") == 1

    # Replay path should still preserve deterministic request ordering in output.
    assert [int(row.get("request_index")) for row in rows] == [0, 1]
