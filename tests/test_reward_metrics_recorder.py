import sqlite3
import pytest
from types import SimpleNamespace

from agentic_coder_prototype.monitoring.reward_metrics import (
    DEFAULT_REWARD_METRIC_NAMES,
    RewardMetricsRecorder,
    RewardMetricsSQLiteWriter,
    _sanitize_metric_name,
)
from agentic_coder_prototype.state.session_state import SessionState
from agentic_coder_prototype.provider_metrics import ProviderMetricsCollector
from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from scripts.export_provider_metrics import aggregate


def test_sanitize_metric_name_enforces_uppercase_strip():
    assert _sanitize_metric_name("  svs  ") == "SVS"
    with pytest.raises(ValueError):
        _sanitize_metric_name("   ")
    with pytest.raises(TypeError):
        _sanitize_metric_name(None)  # type: ignore[arg-type]


def test_reward_metrics_recorder_records_turn_with_defaults():
    recorder = RewardMetricsRecorder()
    recorder.record_turn(3, {"svs": 1, "acs": 0.5}, metadata={"dialect": "unified_diff"})

    payload = recorder.as_payload()
    assert payload["metric_names"] == DEFAULT_REWARD_METRIC_NAMES
    assert payload["turns"] == [
        {
            "turn": 3,
            "metrics": {"SVS": 1.0, "ACS": 0.5},
            "meta": {"dialect": "unified_diff"},
        }
    ]


def test_reward_metrics_recorder_rejects_unknown_metric():
    recorder = RewardMetricsRecorder(metric_names=["SVS"])
    with pytest.raises(KeyError):
        recorder.set_metric(0, "ACS", 0.0)


def test_record_diff_metrics_updates_reward_payload():
    session = SessionState(workspace="/tmp", image="img", config={})
    conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
    dummy = SimpleNamespace(provider_metrics=ProviderMetricsCollector())
    dummy._count_diff_hunks = lambda text: conductor_cls._count_diff_hunks(text)

    tool_call = SimpleNamespace(
        function="apply_unified_patch",
        arguments={"patch": "@@\n-old\n+new\n"},
        dialect="unified_diff",
    )
    result = {"ok": True}

    conductor_cls._record_diff_metrics(
        dummy,
        tool_call,
        result,
        session_state=session,
        turn_index=2,
    )

    payload = session.reward_metrics_payload()
    assert payload["turns"][0]["turn"] == 2
    assert payload["turns"][0]["metrics"]["PAS"] == 1.0


def test_record_lsp_reward_metrics_helper():
    session = SessionState(workspace="/tmp", image="img", config={})
    conductor_cls = OpenAIConductor.__ray_metadata__.modified_class

    class EnhancedStub:
        def consume_lsp_metrics(self):
            return {"led_errors": 0, "sbs_files_with_issues": 3}

    dummy = SimpleNamespace(
        agent_executor=SimpleNamespace(enhanced_executor=EnhancedStub())
    )
    conductor_cls._record_lsp_reward_metrics(dummy, session, 5)
    conductor_cls._record_test_reward_metric(dummy, session, 5, 0.0)

    payload = session.reward_metrics_payload()
    metrics = payload["turns"][0]["metrics"]
    assert metrics["LED"] == 1.0
    assert metrics["SBS"] == 3.0
    assert metrics["TPF_DELTA"] == 0.0


def test_record_usage_reward_metrics_captures_tokens_latency():
    session = SessionState(workspace="/tmp", image="img", config={})
    conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
    dummy = SimpleNamespace(
        agent_executor=SimpleNamespace(enhanced_executor=None),
        _last_runtime_latency=0.5,
        _last_html_detected=False,
    )

    conductor_cls._record_usage_reward_metrics(
        dummy,
        session,
        7,
        {"prompt_tokens": 20, "completion_tokens": 10},
    )

    metrics = session.reward_metrics_payload()["turns"][0]["metrics"]
    assert metrics["TE"] == 30.0
    assert metrics["TOE"] == pytest.approx(10 / 30)
    assert metrics["LE"] == pytest.approx(0.5)
    assert metrics["SPA"] == 1.0


def test_reward_metrics_sqlite_writer_persists(tmp_path):
    writer = RewardMetricsSQLiteWriter(str(tmp_path / "rewards.db"))
    payload = {
        "metric_names": ["SVS"],
        "turns": [
            {"turn": 0, "metrics": {"SVS": 1.0}, "meta": {"dialect": "unified"}},
            {"turn": 1, "metrics": {"SVS": 0.5}},
        ],
    }
    writer.write("run-123", payload)

    with sqlite3.connect(str(tmp_path / "rewards.db")) as conn:
        rows = conn.execute("SELECT run_id, turn, metric, value, metadata FROM reward_metrics ORDER BY turn").fetchall()

    assert rows == [
        ("run-123", 0, "SVS", 1.0, "{\"dialect\": \"unified\"}"),
        ("run-123", 1, "SVS", 0.5, None),
    ]


def test_aggregate_handles_reward_events():
    events = [
        {
            "event": "provider_metrics",
            "summary": {"calls": 2, "errors": 1, "html_errors": 0},
            "routes": {"model": {"calls": 2, "errors": 1, "html_errors": 0}},
        },
        {
            "event": "reward_metrics",
            "metric_names": ["PAS", "ACS"],
            "turns": [
                {"turn": 0, "metrics": {"PAS": 1.0}},
                {"turn": 1, "metrics": {"PAS": 0.5, "ACS": 1.0, "TPF_DELTA": 1.0}},
            ],
        },
    ]
    summary = aggregate(events)
    reward_summary = summary["reward_metrics"]
    assert reward_summary["turns"] == 2
    pas_entry = reward_summary["metrics"]["PAS"]
    assert pas_entry["count"] == 2
    assert pas_entry["avg"] == pytest.approx(0.75)
    assert reward_summary["metrics"]["TPF_DELTA"]["avg"] == pytest.approx(1.0)
