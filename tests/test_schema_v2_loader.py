import os
from pathlib import Path

import pytest

from agentic_coder_prototype.compilation.v2_loader import load_agent_config


def test_v2_loader_extends_and_validation(tmp_path, monkeypatch):
    base = tmp_path / "base_v2.yaml"
    child = tmp_path / "child_v2.yaml"
    base.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
  lsp: { enabled: false }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
tools:
  registry:
    paths: [implementations/tools/defs]
        """.strip()
    )
    child.write_text(
        """
extends: base_v2.yaml
version: 2
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
long_running:
  enabled: true
  reset_policy: fresh
  episode:
    max_steps_override: 5
        """.strip()
    )

    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    os.chdir(tmp_path)
    conf = load_agent_config(str(child))
    assert conf["version"] == 2
    assert conf["tools"]["defs_dir"].endswith("implementations/tools/defs")
    assert conf["long_running"]["enabled"] is True
    assert conf["long_running"]["reset_policy"] == "fresh"
    assert conf["long_running"]["episode"]["max_steps_override"] == 5


def test_v2_loader_legacy_fallback(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("providers: {}\n")
    monkeypatch.delenv("AGENT_SCHEMA_V2_ENABLED", raising=False)
    conf = load_agent_config(str(legacy))
    assert isinstance(conf, dict)


def test_v2_loader_rejects_invalid_long_running_reset_policy(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  reset_policy: invalid_mode
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.reset_policy"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_episode_max_steps_override(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_episode_override_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  episode:
    max_steps_override: -1
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.episode.max_steps_override"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_recovery_backoff_jitter_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_recovery_backoff_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  recovery:
    backoff_disable_jitter: "nope"
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.recovery.backoff_disable_jitter"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_observability_emit_macro_events_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_observability_emit_macro_events_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  observability:
    emit_macro_events: "yes"
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.observability.emit_macro_events"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_verification_tier_timeout(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_verification_tier_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  verification:
    tiers:
      - name: smoke
        commands: ["pytest -q smoke"]
        timeout_seconds: 0
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.verification.tiers\\[0\\].timeout_seconds"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_reviewer_mode(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_reviewer_mode_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  reviewer:
    mode: read_write
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.reviewer.mode"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_recovery_no_progress_signature_repeats(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_recovery_signature_repeat_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  recovery:
    no_progress_signature_repeats: -1
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.recovery.no_progress_signature_repeats"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_resume_enabled_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_resume_enabled_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  resume:
    enabled: "yes"
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.resume.enabled"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_resume_state_path_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_resume_state_path_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  resume:
    enabled: true
    state_path: 123
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.resume.state_path"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_budgets_total_tokens(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_budgets_total_tokens_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  budgets:
    total_tokens: -5
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.budgets.total_tokens"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_long_running_budgets_total_cost_usd(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_budgets_total_cost_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
long_running:
  enabled: true
  budgets:
    total_cost_usd: nope
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="long_running.budgets.total_cost_usd"):
        load_agent_config(str(cfg))


def test_v2_loader_accepts_valid_features_rlm_block(tmp_path, monkeypatch):
    cfg = tmp_path / "valid_features_rlm_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    budget:
      max_depth: 2
      max_subcalls: 32
      max_total_tokens: 120000
      max_total_cost_usd: 3.5
      max_wallclock_seconds: 180
      per_branch:
        max_subcalls: 12
        max_total_tokens: 50000
        max_total_cost_usd: 1.25
    blob_store:
      max_total_bytes: 200000
      max_blob_bytes: 20000
      mvi_excerpt_bytes: 1200
    subcall:
      max_completion_tokens: 800
      timeout_seconds: 45
      retries: 1
    scheduling:
      mode: sync
      batch:
        enabled: false
        max_concurrency: 4
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    conf = load_agent_config(str(cfg))
    assert conf["features"]["rlm"]["enabled"] is True
    assert conf["features"]["rlm"]["budget"]["max_subcalls"] == 32


def test_v2_loader_rejects_invalid_features_rlm_budget_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_features_rlm_budget_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    budget:
      max_subcalls: nope
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="features.rlm.budget.max_subcalls"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_features_rlm_scheduling_mode(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_features_rlm_sched_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    scheduling:
      mode: parallel
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="features.rlm.scheduling.mode"):
        load_agent_config(str(cfg))


def test_v2_loader_accepts_valid_features_rlm_routing_block(tmp_path, monkeypatch):
    cfg = tmp_path / "valid_features_rlm_routing_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    routing:
      default_lane: tool_heavy
      long_context_prompt_chars: 2000
      long_context_blob_refs: 3
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    conf = load_agent_config(str(cfg))
    routing = conf["features"]["rlm"]["routing"]
    assert routing["default_lane"] == "tool_heavy"


def test_v2_loader_rejects_invalid_features_rlm_routing_lane(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_features_rlm_routing_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    routing:
      default_lane: turbo
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="features.rlm.routing.default_lane"):
        load_agent_config(str(cfg))


def test_v2_loader_accepts_valid_features_rlm_batch_retry_fields(tmp_path, monkeypatch):
    cfg = tmp_path / "valid_features_rlm_batch_retry_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    scheduling:
      mode: batch
      batch:
        enabled: true
        max_concurrency: 4
        retries: 2
        timeout_seconds: 30
        fail_fast: false
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    conf = load_agent_config(str(cfg))
    batch = conf["features"]["rlm"]["scheduling"]["batch"]
    assert batch["retries"] == 2
    assert batch["max_concurrency"] == 4


def test_v2_loader_accepts_valid_features_rlm_batch_per_branch_concurrency(tmp_path, monkeypatch):
    cfg = tmp_path / "valid_features_rlm_batch_per_branch_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    scheduling:
      mode: batch
      batch:
        enabled: true
        max_concurrency: 4
        max_concurrency_per_branch: 1
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    conf = load_agent_config(str(cfg))
    batch = conf["features"]["rlm"]["scheduling"]["batch"]
    assert batch["max_concurrency_per_branch"] == 1


def test_v2_loader_rejects_invalid_features_rlm_batch_retries_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_features_rlm_batch_retries_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    scheduling:
      mode: batch
      batch:
        enabled: true
        retries: nope
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="features.rlm.scheduling.batch.retries"):
        load_agent_config(str(cfg))


def test_v2_loader_rejects_invalid_features_rlm_batch_per_branch_type(tmp_path, monkeypatch):
    cfg = tmp_path / "invalid_features_rlm_batch_per_branch_v2.yaml"
    cfg.write_text(
        """
version: 2
workspace:
  root: ./agent_ws
  sandbox: { driver: process }
providers:
  default_model: openrouter/openai/gpt-5-nano
  models:
    - id: openrouter/openai/gpt-5-nano
      adapter: openai
modes:
  - name: build
loop:
  sequence:
    - mode: build
features:
  rlm:
    enabled: true
    scheduling:
      mode: batch
      batch:
        enabled: true
        max_concurrency_per_branch: nope
        """.strip()
    )
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "1")
    with pytest.raises(ValueError, match="features.rlm.scheduling.batch.max_concurrency_per_branch"):
        load_agent_config(str(cfg))
