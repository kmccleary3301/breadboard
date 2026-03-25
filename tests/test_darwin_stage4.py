from __future__ import annotations

import json
import pytest
from urllib import error as urllib_error

from breadboard_ext.darwin.stage4 import (
    _perform_stage4_live_call,
    advance_stage4_search_policy_v1,
    build_stage4_budget_envelope,
    build_stage4_campaign_round_record,
    build_stage4_comparison_envelope_digest,
    build_stage4_search_policy_v1,
    build_stage4_support_envelope_digest,
    classify_stage4_power_signal,
    consume_execution_envelope_v2,
    estimate_stage4_route_cost,
    execute_stage4_provider_prompt,
    resolve_stage4_route,
    select_stage4_search_policy_arms,
    stage4_live_execution_requested,
    validate_stage4_claim_eligibility,
    validate_stage4_matched_budget_pair,
)


def test_resolve_stage4_route_defaults_to_scaffold_without_provider_call() -> None:
    route = resolve_stage4_route(task_class="repo_patch_workspace", actual_provider_used=False)
    assert route["execution_mode"] == "scaffold"
    assert route["claim_eligible"] is False
    assert route["route_class"] == "default_worker"


def test_build_stage4_budget_envelope_carries_stage4_comparison_fields() -> None:
    envelope = build_stage4_budget_envelope(
        budget_class="class_a",
        wall_clock_ms=1250,
        token_counts={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15, "cached_input_tokens": 8},
        cost_estimate=0.0,
        comparison_class="bounded_internal",
        route_id=None,
        provider_model=None,
        execution_mode="scaffold",
        route_class="local_baseline",
        cost_source="local_execution",
        support_envelope_digest="a" * 64,
        comparison_envelope_digest="c" * 64,
        evaluator_pack_version="stage4.evalpack.lane.repo_swe.task.v1",
        replication_reserve_fraction=0.2,
        control_reserve_fraction=0.1,
    )
    assert envelope["execution_mode"] == "scaffold"
    assert envelope["route_class"] == "local_baseline"
    assert envelope["cost_source"] == "local_execution"
    assert envelope["token_counts"]["cached_input_tokens"] == 8
    assert envelope["control_reserve_policy"] == "replication=0.20;control=0.10"


def test_consume_execution_envelope_v2_requires_stage4_fields() -> None:
    consumed = consume_execution_envelope_v2(
        {
            "bindings": {
                "cwd": "/repo",
                "out_dir": "artifacts/out",
                "task_id": "task.repo",
                "budget_class": "class_a",
                "tool_bindings": ["shell"],
                "environment_digest": "sha256:env",
                "command": ["python", "-m", "pytest"],
            }
        },
        support_envelope_digest="b" * 64,
        evaluator_pack_version="stage4.evalpack.lane.repo_swe.task.v1",
    )
    assert consumed["task_id"] == "task.repo"
    assert consumed["environment_digest"] == "sha256:env"
    assert consumed["consumed_fields"][-1] == "evaluator_pack_version"


def test_validate_stage4_matched_budget_pair_requires_strict_contract() -> None:
    baseline = {
        "lane_id": "lane.repo_swe",
        "budget_class": "class_a",
        "comparison_class": "bounded_internal",
        "route_class": "default_worker",
        "execution_mode": "scaffold",
        "evaluator_pack_version": "stage4.evalpack.repo_swe.v1",
        "comparison_envelope_digest": "c" * 64,
        "support_envelope_digest": "a" * 64,
        "task_id": "task.repo",
        "control_reserve_policy": "replication=0.20;control=0.10",
    }
    candidate = dict(baseline)
    candidate["support_envelope_digest"] = "b" * 64
    result = validate_stage4_matched_budget_pair(baseline=baseline, candidate=candidate)
    assert result.ok is True


def test_validate_stage4_matched_budget_pair_requires_comparison_envelope_match() -> None:
    baseline = {
        "lane_id": "lane.repo_swe",
        "budget_class": "class_a",
        "comparison_class": "bounded_internal",
        "route_class": "default_worker",
        "execution_mode": "scaffold",
        "evaluator_pack_version": "stage4.evalpack.repo_swe.v1",
        "comparison_envelope_digest": "c" * 64,
        "support_envelope_digest": "a" * 64,
        "task_id": "task.repo",
        "control_reserve_policy": "replication=0.20;control=0.10",
    }
    candidate = dict(baseline)
    candidate["comparison_envelope_digest"] = "d" * 64
    result = validate_stage4_matched_budget_pair(baseline=baseline, candidate=candidate)
    assert result.ok is False
    assert result.reason == "comparison_envelope_digest_mismatch"


def test_validate_stage4_claim_eligibility_rejects_scaffold_rows() -> None:
    result = validate_stage4_claim_eligibility(
        {
            "execution_mode": "scaffold",
            "cost_source": "scaffold_placeholder",
        }
    )
    assert result.ok is False
    assert result.reason == "execution_mode_not_live"


def test_classify_stage4_power_signal_recognizes_score_retained_runtime_improvement() -> None:
    result = classify_stage4_power_signal(
        comparison_valid=True,
        claim_eligible=True,
        delta_score=0.0,
        delta_runtime_ms=-45,
        delta_cost_usd=0.0,
    )
    assert result.positive is True
    assert result.signal_class == "score_retained_runtime_improved"


def test_classify_stage4_power_signal_rejects_invalid_rows() -> None:
    result = classify_stage4_power_signal(
        comparison_valid=False,
        claim_eligible=True,
        delta_score=1.0,
        delta_runtime_ms=-45,
        delta_cost_usd=-0.0001,
    )
    assert result.positive is False
    assert result.signal_class == "invalid_comparison"


def test_build_stage4_support_envelope_digest_is_stable() -> None:
    digest_a = build_stage4_support_envelope_digest(
        lane_id="lane.repo_swe",
        task_id="task.repo",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        allowed_tools=["pytest", "git_diff"],
        environment_digest="sha256:env",
        claim_target="internal",
    )
    digest_b = build_stage4_support_envelope_digest(
        lane_id="lane.repo_swe",
        task_id="task.repo",
        topology_id="policy.topology.pev_v0",
        policy_bundle_id="policy.topology.pev_v0",
        budget_class="class_a",
        allowed_tools=["pytest", "git_diff"],
        environment_digest="sha256:env",
        claim_target="internal",
    )
    assert digest_a == digest_b


def test_build_stage4_comparison_envelope_digest_is_stable() -> None:
    digest_a = build_stage4_comparison_envelope_digest(
        lane_id="lane.repo_swe",
        task_id="task.repo",
        budget_class="class_a",
        comparison_class="stage4_live_economics",
        environment_digest="sha256:env",
        claim_target="internal",
    )
    digest_b = build_stage4_comparison_envelope_digest(
        lane_id="lane.repo_swe",
        task_id="task.repo",
        budget_class="class_a",
        comparison_class="stage4_live_economics",
        environment_digest="sha256:env",
        claim_target="internal",
    )
    assert digest_a == digest_b


def test_stage4_evaluator_pack_version_normalizes_task_family() -> None:
    from breadboard_ext.darwin.stage4 import stage4_evaluator_pack_version

    baseline = stage4_evaluator_pack_version(
        lane_id="lane.repo_swe",
        task_id="task.stage4.lane.repo_swe.arm.repo_swe.control.stage4.v0",
    )
    candidate = stage4_evaluator_pack_version(
        lane_id="lane.repo_swe",
        task_id="task.stage4.lane.repo_swe.arm.repo_swe.topology.stage4.v0",
    )
    assert baseline == candidate


def test_stage4_live_execution_requested_uses_explicit_env_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    assert stage4_live_execution_requested() is False
    monkeypatch.setenv("DARWIN_STAGE4_ENABLE_LIVE", "1")
    assert stage4_live_execution_requested() is True


def test_execute_stage4_provider_prompt_blocks_without_live_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DARWIN_STAGE4_ENABLE_LIVE", raising=False)
    result = execute_stage4_provider_prompt(
        lane_id="lane.repo_swe",
        task_class="repo_patch_workspace",
        prompt="propose a bounded mutation",
    )
    assert result["execution_mode"] == "scaffold"
    assert result["live_block_reason"] == "live_execution_not_requested"
    assert result["claim_eligible"] is False


def test_execute_stage4_provider_prompt_accepts_real_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DARWIN_STAGE4_ENABLE_LIVE", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", "0.20")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", "0.02")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", "0.80")
    monkeypatch.setattr(
        "breadboard_ext.darwin.stage4._perform_stage4_live_call",
        lambda **_: {
            "response_id": "resp_123",
            "text": "bounded mutation proposal",
            "prompt_tokens": 120,
            "cached_input_tokens": 100,
            "cache_write_tokens": 0,
            "completion_tokens": 24,
            "total_tokens": 144,
            "provider_cost_usd": None,
        },
    )
    result = execute_stage4_provider_prompt(
        lane_id="lane.repo_swe",
        task_class="repo_patch_workspace",
        prompt="propose a bounded mutation",
    )
    assert result["execution_mode"] == "live"
    assert result["cost_source"] == "estimated_from_pricing_table"
    assert result["claim_eligible"] is True
    assert result["usage"]["total_tokens"] == 144
    assert result["usage"]["cached_input_tokens"] == 100
    assert result["cost_estimate"] == pytest.approx(0.0000252)


def test_estimate_stage4_route_cost_requires_cached_pricing_for_cached_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", "0.75")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", "4.50")
    monkeypatch.delenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", raising=False)
    estimate, cost_source = estimate_stage4_route_cost(
        route_id="openrouter/openai/gpt-5.4-mini",
        prompt_tokens=2000,
        completion_tokens=200,
        cached_input_tokens=1500,
    )
    assert estimate == 0.0
    assert cost_source == "cached_pricing_missing"


def test_select_stage4_search_policy_arms_picks_top_repo_swe_mutations() -> None:
    policy = build_stage4_search_policy_v1(lane_id="lane.repo_swe", budget_class="class_a")
    selected = select_stage4_search_policy_arms(
        search_policy=policy,
        candidate_rows=[
            {"lane_id": "lane.repo_swe", "operator_id": "baseline_seed", "control_tag": "control"},
            {"lane_id": "lane.repo_swe", "operator_id": "mut.topology.single_to_pev_v1", "control_tag": "mutation"},
            {"lane_id": "lane.repo_swe", "operator_id": "mut.tool_scope.add_git_diff_v1", "control_tag": "mutation"},
            {"lane_id": "lane.repo_swe", "operator_id": "mut.budget.class_a_to_class_b_v1", "control_tag": "mutation"},
            {"lane_id": "lane.repo_swe", "operator_id": "mut.policy.shadow_memory_enable_v1", "control_tag": "mutation"},
            {"lane_id": "lane.harness", "operator_id": "baseline_seed", "control_tag": "watchdog"},
        ],
    )
    repo_swe_mutations = [row["operator_id"] for row in selected if row["lane_id"] == "lane.repo_swe" and row["control_tag"] != "control"]
    assert repo_swe_mutations == [
        "mut.topology.single_to_pev_v1",
        "mut.tool_scope.add_git_diff_v1",
        "mut.budget.class_a_to_class_b_v1",
    ]


def test_select_stage4_search_policy_arms_for_systems_picks_bounded_mutations() -> None:
    policy = build_stage4_search_policy_v1(lane_id="lane.systems", budget_class="class_a")
    selected = select_stage4_search_policy_arms(
        search_policy=policy,
        candidate_rows=[
            {"lane_id": "lane.systems", "operator_id": "baseline_seed", "control_tag": "control"},
            {"lane_id": "lane.systems", "operator_id": "mut.topology.single_to_pev_v1", "control_tag": "mutation"},
            {"lane_id": "lane.systems", "operator_id": "mut.policy.shadow_memory_enable_v1", "control_tag": "mutation"},
        ],
    )
    systems_mutations = [row["operator_id"] for row in selected if row["lane_id"] == "lane.systems" and row["control_tag"] != "control"]
    assert systems_mutations == [
        "mut.topology.single_to_pev_v1",
        "mut.policy.shadow_memory_enable_v1",
    ]


def test_advance_stage4_search_policy_promotes_discovery_after_positive_signal() -> None:
    policy = build_stage4_search_policy_v1(lane_id="lane.repo_swe", budget_class="class_a")
    updated = advance_stage4_search_policy_v1(
        search_policy=policy,
        comparison_rows=[
            {
                "lane_id": "lane.repo_swe",
                "operator_id": "mut.topology.single_to_pev_v1",
                "comparison_valid": True,
                "positive_power_signal": True,
            }
        ],
    )
    assert updated["campaign_class"] == "C1 Discovery"
    assert updated["repetition_count"] == 3


def test_build_stage4_campaign_round_record_carries_round_metadata() -> None:
    policy = build_stage4_search_policy_v1(lane_id="lane.systems", budget_class="class_a")
    record = build_stage4_campaign_round_record(
        round_id="round.lane.systems.r1",
        lane_id="lane.systems",
        search_policy=policy,
        selected_arms=[{"campaign_arm_id": "arm.systems.control.stage4.v0", "operator_id": "baseline_seed", "control_tag": "control"}],
    )
    assert record["round_id"] == "round.lane.systems.r1"
    assert record["campaign_class"] == policy["campaign_class"]


def test_execute_stage4_provider_prompt_falls_back_to_openai_when_openrouter_is_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DARWIN_STAGE4_ENABLE_LIVE", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", "0.75")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", "0.075")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", "4.50")

    def _fake_live_call(*, route, **_):
        if route["route_id"] == "openrouter/openai/gpt-5.4-mini":
            raise urllib_error.HTTPError(
                url="https://openrouter.ai/api/v1/chat/completions",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )
        return {
            "response_id": "resp_fallback",
            "text": "fallback mutation proposal",
            "prompt_tokens": 110,
            "cached_input_tokens": 90,
            "cache_write_tokens": 0,
            "completion_tokens": 22,
            "total_tokens": 132,
            "provider_cost_usd": None,
        }

    monkeypatch.setattr("breadboard_ext.darwin.stage4._perform_stage4_live_call", _fake_live_call)
    result = execute_stage4_provider_prompt(
        lane_id="lane.repo_swe",
        task_class="repo_patch_workspace",
        prompt="propose a bounded mutation",
    )
    assert result["execution_mode"] == "live"
    assert result["route_id"] == "openai/gpt-5.4-mini"
    assert result["claim_eligible"] is True


def test_execute_stage4_provider_prompt_falls_back_to_openai_when_openrouter_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DARWIN_STAGE4_ENABLE_LIVE", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", "0.75")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", "0.075")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", "4.50")

    def _fake_live_call(*, route, **_):
        if route["route_id"] == "openrouter/openai/gpt-5.4-mini":
            raise TimeoutError("timed out waiting for openrouter")
        return {
            "response_id": "resp_fallback_timeout",
            "text": "fallback mutation proposal",
            "prompt_tokens": 120,
            "cached_input_tokens": 100,
            "cache_write_tokens": 0,
            "completion_tokens": 24,
            "total_tokens": 144,
            "provider_cost_usd": None,
        }

    monkeypatch.setattr("breadboard_ext.darwin.stage4._perform_stage4_live_call", _fake_live_call)
    result = execute_stage4_provider_prompt(
        lane_id="lane.repo_swe",
        task_class="repo_patch_workspace",
        prompt="propose a bounded mutation",
    )
    assert result["execution_mode"] == "live"
    assert result["route_id"] == "openai/gpt-5.4-mini"
    assert result["fallback_reason"] == "openrouter_timeout"
    assert result["claim_eligible"] is True


def test_execute_stage4_provider_prompt_falls_back_to_openai_when_openrouter_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DARWIN_STAGE4_ENABLE_LIVE", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M", "0.75")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M", "0.075")
    monkeypatch.setenv("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M", "4.50")

    def _fake_live_call(*, route, **_):
        assert route["route_id"] == "openai/gpt-5.4-mini"
        return {
            "response_id": "resp_fallback_missing_key",
            "text": "fallback mutation proposal",
            "prompt_tokens": 100,
            "cached_input_tokens": 80,
            "cache_write_tokens": 0,
            "completion_tokens": 20,
            "total_tokens": 120,
            "provider_cost_usd": None,
        }

    monkeypatch.setattr("breadboard_ext.darwin.stage4._perform_stage4_live_call", _fake_live_call)
    result = execute_stage4_provider_prompt(
        lane_id="lane.repo_swe",
        task_class="repo_patch_workspace",
        prompt="propose a bounded mutation",
    )
    assert result["execution_mode"] == "live"
    assert result["route_id"] == "openai/gpt-5.4-mini"
    assert result["fallback_reason"] == "openrouter_key_missing"
    assert result["claim_eligible"] is True


def test_perform_stage4_live_call_caps_openrouter_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "id": "resp_openrouter_timeout_cap",
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                }
            ).encode("utf-8")

    def _fake_urlopen(request_obj, timeout):
        captured["timeout"] = timeout
        captured["url"] = request_obj.full_url
        return _FakeResponse()

    monkeypatch.setattr("breadboard_ext.darwin.stage4.urllib_request.urlopen", _fake_urlopen)
    payload = _perform_stage4_live_call(
        route=resolve_stage4_route(task_class="repo_patch_workspace", actual_provider_used=False),
        prompt="say hi",
        timeout_s=60,
    )
    assert payload["response_id"] == "resp_openrouter_timeout_cap"
    assert captured["timeout"] == 15


def test_perform_stage4_live_call_caps_openai_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("DARWIN_STAGE4_OPENAI_TIMEOUT_S", "12")
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "id": "resp_openai_timeout_cap",
                    "output_text": "ok",
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                }
            ).encode("utf-8")

    def _fake_urlopen(request_obj, timeout):
        captured["timeout"] = timeout
        captured["url"] = request_obj.full_url
        return _FakeResponse()

    monkeypatch.setattr("breadboard_ext.darwin.stage4.urllib_request.urlopen", _fake_urlopen)
    payload = _perform_stage4_live_call(
        route={
            "route_id": "openai/gpt-5.4-mini",
            "provider_model": "openai/gpt-5.4-mini",
        },
        prompt="say hi",
        timeout_s=60,
    )
    assert payload["response_id"] == "resp_openai_timeout_cap"
    assert captured["timeout"] == 12
