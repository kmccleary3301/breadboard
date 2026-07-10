from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from agentic_coder_prototype.compilation import v2_loader


_DELETE = object()

LOADER_ENTRYPOINTS = [
    pytest.param(v2_loader.build_config_view, id="build-config-view"),
    pytest.param(v2_loader.load_agent_config, id="load-agent-config"),
]

# Each mutation corresponds to a validation branch in _validate_v2. The expected
# pointer is deliberately independent of jsonschema's human-readable wording:
# these tests pin schema ownership without coupling to legacy Python messages.
INVALID_V2_MUTATIONS = [
    pytest.param(("version",), _DELETE, "/version", id="missing-version"),
    pytest.param(("workspace",), _DELETE, "/workspace", id="missing-workspace"),
    pytest.param(("providers",), _DELETE, "/providers", id="missing-providers"),
    pytest.param(("modes",), _DELETE, "/modes", id="missing-modes"),
    pytest.param(("loop",), _DELETE, "/loop", id="missing-loop"),
    pytest.param(("version",), 3, "/version", id="wrong-version"),
    pytest.param(("providers", "default_model"), _DELETE, "/providers/default_model", id="missing-default-model"),
    pytest.param(("providers", "default_model"), "", "/providers/default_model", id="empty-default-model"),
    pytest.param(("providers", "models"), [], "/providers/models", id="empty-models"),
    pytest.param(("providers", "models", 0, "id"), _DELETE, "/providers/models/0/id", id="model-missing-id"),
    pytest.param(
        ("providers", "models", 0, "adapter"),
        _DELETE,
        "/providers/models/0/adapter",
        id="model-missing-adapter",
    ),
    pytest.param(("loop", "sequence"), [], "/loop/sequence", id="empty-loop-sequence"),
    pytest.param(("features",), "invalid", "/features", id="features-not-object"),
    pytest.param(("features", "rlm"), "invalid", "/features/rlm", id="rlm-not-object"),
    pytest.param(("features", "rlm", "enabled"), "yes", "/features/rlm/enabled", id="rlm-enabled-not-boolean"),
    pytest.param(("features", "rlm", "budget"), "invalid", "/features/rlm/budget", id="budget-not-object"),
    pytest.param(("features", "rlm", "budget", "max_depth"), -1, "/features/rlm/budget/max_depth", id="budget-negative-max-depth"),
    pytest.param(("features", "rlm", "budget", "max_subcalls"), -1, "/features/rlm/budget/max_subcalls", id="budget-negative-max-subcalls"),
    pytest.param(("features", "rlm", "budget", "max_total_tokens"), -1, "/features/rlm/budget/max_total_tokens", id="budget-negative-max-total-tokens"),
    pytest.param(("features", "rlm", "budget", "max_wallclock_seconds"), -1, "/features/rlm/budget/max_wallclock_seconds", id="budget-negative-max-wallclock-seconds"),
    pytest.param(("features", "rlm", "budget", "max_total_cost_usd"), -0.01, "/features/rlm/budget/max_total_cost_usd", id="budget-negative-max-total-cost"),
    pytest.param(("features", "rlm", "budget", "per_branch"), "invalid", "/features/rlm/budget/per_branch", id="per-branch-not-object"),
    pytest.param(("features", "rlm", "budget", "per_branch", "max_subcalls"), -1, "/features/rlm/budget/per_branch/max_subcalls", id="per-branch-negative-max-subcalls"),
    pytest.param(("features", "rlm", "budget", "per_branch", "max_total_tokens"), -1, "/features/rlm/budget/per_branch/max_total_tokens", id="per-branch-negative-max-total-tokens"),
    pytest.param(("features", "rlm", "budget", "per_branch", "max_total_cost_usd"), -0.01, "/features/rlm/budget/per_branch/max_total_cost_usd", id="per-branch-negative-max-total-cost"),
    pytest.param(("features", "rlm", "blob_store"), "invalid", "/features/rlm/blob_store", id="blob-store-not-object"),
    pytest.param(("features", "rlm", "blob_store", "max_total_bytes"), -1, "/features/rlm/blob_store/max_total_bytes", id="blob-store-negative-max-total-bytes"),
    pytest.param(("features", "rlm", "blob_store", "max_blob_bytes"), -1, "/features/rlm/blob_store/max_blob_bytes", id="blob-store-negative-max-blob-bytes"),
    pytest.param(("features", "rlm", "blob_store", "mvi_excerpt_bytes"), -1, "/features/rlm/blob_store/mvi_excerpt_bytes", id="blob-store-negative-mvi-excerpt-bytes"),
    pytest.param(("features", "rlm", "subcall"), "invalid", "/features/rlm/subcall", id="subcall-not-object"),
    pytest.param(("features", "rlm", "subcall", "max_completion_tokens"), -1, "/features/rlm/subcall/max_completion_tokens", id="subcall-negative-max-completion-tokens"),
    pytest.param(("features", "rlm", "subcall", "timeout_seconds"), -0.1, "/features/rlm/subcall/timeout_seconds", id="subcall-negative-timeout"),
    pytest.param(("features", "rlm", "subcall", "retries"), -1, "/features/rlm/subcall/retries", id="subcall-negative-retries"),
    pytest.param(("features", "rlm", "scheduling"), "invalid", "/features/rlm/scheduling", id="scheduling-not-object"),
    pytest.param(("features", "rlm", "scheduling", "mode"), "parallel", "/features/rlm/scheduling/mode", id="scheduling-invalid-mode"),
    pytest.param(("features", "rlm", "scheduling", "batch"), "invalid", "/features/rlm/scheduling/batch", id="batch-not-object"),
    pytest.param(("features", "rlm", "scheduling", "batch", "enabled"), "yes", "/features/rlm/scheduling/batch/enabled", id="batch-enabled-not-boolean"),
    pytest.param(("features", "rlm", "scheduling", "batch", "max_concurrency"), -1, "/features/rlm/scheduling/batch/max_concurrency", id="batch-negative-max-concurrency"),
    pytest.param(("features", "rlm", "scheduling", "batch", "max_concurrency_per_branch"), -1, "/features/rlm/scheduling/batch/max_concurrency_per_branch", id="batch-negative-max-concurrency-per-branch"),
    pytest.param(("features", "rlm", "scheduling", "batch", "retries"), -1, "/features/rlm/scheduling/batch/retries", id="batch-negative-retries"),
    pytest.param(("features", "rlm", "scheduling", "batch", "timeout_seconds"), -0.1, "/features/rlm/scheduling/batch/timeout_seconds", id="batch-negative-timeout"),
    pytest.param(("features", "rlm", "scheduling", "batch", "fail_fast"), "yes", "/features/rlm/scheduling/batch/fail_fast", id="batch-fail-fast-not-boolean"),
    pytest.param(("features", "rlm", "routing"), "invalid", "/features/rlm/routing", id="routing-not-object"),
    pytest.param(("features", "rlm", "routing", "default_lane"), "fast", "/features/rlm/routing/default_lane", id="routing-invalid-default-lane"),
    pytest.param(("features", "rlm", "routing", "long_context_prompt_chars"), -1, "/features/rlm/routing/long_context_prompt_chars", id="routing-negative-prompt-chars"),
    pytest.param(("features", "rlm", "routing", "long_context_blob_refs"), -1, "/features/rlm/routing/long_context_blob_refs", id="routing-negative-blob-refs"),
    pytest.param(("long_running",), "invalid", "/long_running", id="long-running-not-object"),
    pytest.param(("long_running", "enabled"), "yes", "/long_running/enabled", id="long-running-enabled-not-boolean"),
    pytest.param(("long_running", "reset_policy"), "restart", "/long_running/reset_policy", id="long-running-invalid-reset-policy"),
    pytest.param(("long_running", "budgets"), "invalid", "/long_running/budgets", id="long-running-budgets-not-object"),
    pytest.param(("long_running", "budgets", "total_tokens"), -1, "/long_running/budgets/total_tokens", id="long-running-negative-total-tokens"),
    pytest.param(("long_running", "budgets", "max_total_tokens"), -1, "/long_running/budgets/max_total_tokens", id="long-running-negative-max-total-tokens"),
    pytest.param(("long_running", "budgets", "total_cost_usd"), -0.01, "/long_running/budgets/total_cost_usd", id="long-running-negative-total-cost"),
    pytest.param(("long_running", "budgets", "max_total_cost_usd"), -0.01, "/long_running/budgets/max_total_cost_usd", id="long-running-negative-max-total-cost"),
    pytest.param(("long_running", "episode"), "invalid", "/long_running/episode", id="episode-not-object"),
    pytest.param(("long_running", "episode", "max_steps_override"), 0, "/long_running/episode/max_steps_override", id="episode-non-positive-max-steps"),
    pytest.param(("long_running", "recovery"), "invalid", "/long_running/recovery", id="recovery-not-object"),
    pytest.param(("long_running", "recovery", "backoff_base_seconds"), -0.1, "/long_running/recovery/backoff_base_seconds", id="recovery-negative-base-backoff"),
    pytest.param(("long_running", "recovery", "backoff_max_seconds"), -0.1, "/long_running/recovery/backoff_max_seconds", id="recovery-negative-max-backoff"),
    pytest.param(("long_running", "recovery", "backoff_disable_jitter"), "yes", "/long_running/recovery/backoff_disable_jitter", id="recovery-jitter-not-boolean"),
    pytest.param(("long_running", "recovery", "no_progress_signature_repeats"), -1, "/long_running/recovery/no_progress_signature_repeats", id="recovery-negative-no-progress-repeats"),
    pytest.param(("long_running", "observability"), "invalid", "/long_running/observability", id="observability-not-object"),
    pytest.param(("long_running", "observability", "emit_macro_events"), "yes", "/long_running/observability/emit_macro_events", id="observability-emit-events-not-boolean"),
    pytest.param(("long_running", "reviewer"), "invalid", "/long_running/reviewer", id="reviewer-not-object"),
    pytest.param(("long_running", "reviewer", "enabled"), "yes", "/long_running/reviewer/enabled", id="reviewer-enabled-not-boolean"),
    pytest.param(("long_running", "reviewer", "mode"), "write", "/long_running/reviewer/mode", id="reviewer-invalid-mode"),
    pytest.param(("long_running", "resume"), "invalid", "/long_running/resume", id="resume-not-object"),
    pytest.param(("long_running", "resume", "enabled"), "yes", "/long_running/resume/enabled", id="resume-enabled-not-boolean"),
    pytest.param(("long_running", "resume", "state_path"), 7, "/long_running/resume/state_path", id="resume-state-path-not-string"),
    pytest.param(("long_running", "verification"), "invalid", "/long_running/verification", id="verification-not-object"),
    pytest.param(("long_running", "verification", "tiers"), "invalid", "/long_running/verification/tiers", id="verification-tiers-not-array"),
    pytest.param(("long_running", "verification", "tiers"), ["invalid"], "/long_running/verification/tiers/0", id="verification-tier-not-object"),
    pytest.param(("long_running", "verification", "tiers"), [{}], "/long_running/verification/tiers/0/commands", id="verification-tier-missing-commands"),
    pytest.param(("long_running", "verification", "tiers"), [{"commands": []}], "/long_running/verification/tiers/0/commands", id="verification-tier-empty-commands"),
    pytest.param(("long_running", "verification", "tiers"), [{"commands": ["pytest"], "timeout_seconds": 0}], "/long_running/verification/tiers/0/timeout_seconds", id="verification-tier-non-positive-timeout"),
    pytest.param(("long_running", "verification", "tiers"), [{"commands": ["pytest"], "hard_fail": "yes"}], "/long_running/verification/tiers/0/hard_fail", id="verification-tier-hard-fail-not-boolean"),
]


def _minimal_explicit_v2_document() -> dict[str, Any]:
    return {
        "schema_version": "bb.agent_config_surface.v2",
        "version": 2,
        "workspace": {"root": "."},
        "providers": {
            "default_model": "openai/example",
            "models": [{"id": "openai/example", "adapter": "openai"}],
        },
        "modes": [
            {
                "name": "default",
                "prompt": "config/e4_targets/example/prompts/system.md",
                "tools_enabled": ["read"],
            }
        ],
        "loop": {"sequence": [{"mode": "default"}]},
    }


def _mutate(document: dict[str, Any], path: tuple[str | int, ...], value: object) -> None:
    target: Any = document
    for index, segment in enumerate(path[:-1]):
        next_segment = path[index + 1]
        if isinstance(segment, int):
            target = target[segment]
        else:
            target = target.setdefault(segment, [] if isinstance(next_segment, int) else {})

    final = path[-1]
    if value is _DELETE:
        if isinstance(final, int):
            del target[final]
        else:
            target.pop(final)
    else:
        target[final] = value


@pytest.mark.parametrize("entrypoint", LOADER_ENTRYPOINTS)
@pytest.mark.parametrize(("mutation_path", "invalid_value", "expected_pointer"), INVALID_V2_MUTATIONS)
def test_v2_validation_is_owned_by_draft_2020_12_schema(
    tmp_path: Path,
    entrypoint: Callable[[str], object],
    mutation_path: tuple[str | int, ...],
    invalid_value: object,
    expected_pointer: str,
) -> None:
    """Every invalid v2 family is rejected by the schema before compatibility checks run."""
    document = deepcopy(_minimal_explicit_v2_document())
    _mutate(document, mutation_path, invalid_value)
    config_path = tmp_path / "invalid.v2.yaml"
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        entrypoint(str(config_path))

    assert str(exc_info.value).startswith(f"agent config schema error at {expected_pointer}:")
