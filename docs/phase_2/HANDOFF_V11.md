# Handoff V11 — KyleCode (Config‑Driven Dialects, TPSL, and Ray Isolation)

> This document is a complete, self‑contained handoff for the engineer who will continue this work. It explains the project, what was implemented in this iteration, what problems were fixed, the current state of the system, and what to do next. It assumes no insider context beyond access to the repository and general engineering acumen.

## 1) Project Overview

KyleCode is a configurable research platform for agentic coding. It aims to reproduce and A/B test behaviors found in modern IDE assistants (OpenCode, Claude Code, Cursor, etc.) while keeping all important choices in declarative YAML. The long‑term goal is to optimize across providers, tool calling modes, and prompt strategies with repeatable telemetry and logs.

The current focus is a repeatable “C filesystem” build & test task (OpenCode filesystem benchmark):
- Plan in a read‑only pass
- Build in small, safe increments using a diff dialect (OpenCode patch)
- Validate with make/tests under a Ray‑managed sandbox
- Produce readable logs and a durable run directory for analysis

Key principles:
- Everything configurable (providers, dialects, tool sets, modes, concurrency rules)
- Text‑based tool calling and provider‑native tool calling both supported
- Logging v2: clean transcript + structured artifacts per run
- Tool Prompt Synthesis Layer (TPSL): templates generate the “how to call tools” catalogs

## 2) Repository Landmarks

- `agentic_coder_prototype/` — Core runtime and execution path
  - `agent_llm_openai.py` — Main agent loop and provider invocation
  - `provider_runtime.py` — OpenAI/Anthropic/OpenRouter runtimes and error surfacing
  - `compilation/system_prompt_compiler.py` — V2 prompt compilation & TPSL integration
  - `compilation/v2_loader.py` — V2 config loader with `extends`, validation, normalization
  - `dialects/` — Textual dialect parsers (OpenCode patch, bash blocks, unified diff, pythonic)
  - `execution/` — Enhanced executor, validation, concurrency
  - `logging_v2/` — Per‑run directories, transcript/artifact writers, raw API dumps
- `implementations/` — Templates and tool catalogs
  - `system_prompts/opencode/` — Builder/plan guardrail text
  - `tool_prompt_synthesis/` — Jinja2 TPSL templates (pythonic, unified_diff, opencode_patch)
- `implementations/tools/defs_oc/` — OpenCode‑compatible YAML tool definitions (bash/read/patch/etc.)
- `agent_configs/` — YAML configurations (Schema V2)
- `scripts/` — Helpers for analysis
  - `log_reduce.py` — Summarize run logs
  - `compile_tpsl_preview.py` — Render TPSL outputs from config
- `logging/` — Per‑run directories (Logging v2)
- `tests/` — Project test suite (scoped to `tests/` by default)

## 3) What We Implemented in V11

1. Config‑driven prompt delivery & dialect selection
   - Runtime now reads `prompts.tool_prompt_mode` directly from V2 configs; switch between `system_compiled_and_persistent_per_turn` and `per_turn_append` purely via YAML.
   - Tool catalogs respect V2 `tools.registry.include`. Plan/build allowlists are authoritative.
   - Plan/build mode‑specific dialects selected via TPSL (pythonic for plan; OpenCode patch for build).

2. TPSL templates for OpenCode + strict plan
   - OpenCode patch templates (system + per‑turn) describe single‑patch‑per‑turn, shell sequencing, and finalization rules.
   - Strict plan templates (system + per‑turn) force “tools‑only” behavior (read_file / list_dir) in plan.
   - Both compiled and per‑turn append variants provided for A/B.

3. Ray isolation & smoke‑run ergonomics
   - Runtime/tests start a local Ray head (`address=local`) to avoid attaching to a shared cluster.
   - Test discovery limited to `tests/`; industry reference suites skipped by default.
   - Added scripts to preview TPSL output and reduce logs quickly.

## 4) Changes (file‑by‑file)

Runtime & CLI
- `agentic_coder_prototype/agent.py`
  - `_resolve_tool_prompt_mode()` reads `prompts.tool_prompt_mode` (or legacy `prompt.mode`).
  - Passes `tool_prompt_mode` to `run_agentic_loop` in `run_task` and `interactive_session`.
  - Ray init uses `ray.init(address="local", include_dashboard=False)`.
- `cli_run_openai_agent.py`
  - Prefers `prompts.tool_prompt_mode` over legacy `prompt.mode`.

Prompt compilation & tool selection
- `agentic_coder_prototype/agent_llm_openai.py`
  - `_tool_defs_from_yaml()` prefers V2 `tools.registry.include` (fallback to legacy only if needed).

TPSL templates (new)
- `implementations/tool_prompt_synthesis/opencode_patch/system_full.j2.md`
- `implementations/tool_prompt_synthesis/opencode_patch/per_turn_short.j2.md`
- `implementations/tool_prompt_synthesis/pythonic/per_turn_plan_strict.j2.md`
- `implementations/tool_prompt_synthesis/pythonic/system_plan_strict.j2.md`

Updated/added configs (high‑value set)
- `agent_configs/opencode_grok4fast_c_fs_v2.yaml` — compiled catalogs; plan pythonic; build OpenCode patch; trimmed registry/allowlists; plan disables `mark_task_complete`.
- OpenRouter OpenAI (tool‑trained cheap models):
  - `opencode_openai_gpt5nano_c_fs_compiled.yaml`
  - `opencode_openai_gpt5mini_c_fs_compiled.yaml`
  - `opencode_openai_gpt5mini_c_fs_perturn_append.yaml`
- OpenAI direct (smoke only):
  - `opencode_openai_gpt4o_mini_c_fs_compiled.yaml`
  - `opencode_openai_gpt4o_mini_c_fs_plan_strict_compiled.yaml`
  - `opencode_openai_gpt4o_mini_c_fs_perturn_append.yaml`
- OpenRouter non‑OpenAI (text only):
  - `opencode_openrouter_glm46_c_fs_perturn_append.yaml`
  - `opencode_openrouter_deepseek_v32exp_c_fs_perturn_append.yaml`

Scripts
- `scripts/compile_tpsl_preview.py` — Render system/per‑turn catalogs for a config mode without calling providers.
- `scripts/log_reduce.py` — Summarize per‑turn tool calls/results; surface validation/provider errors.

Tests
- `pytest.ini`: `testpaths = tests`
- `tests/conftest.py`: sets `RAY_ADDRESS=local`, `RAY_DASHBOARD_PORT=8299`, adds `tool_calling/` to `sys.path`, skips industry references by default.

## 5) What We Ran (signals)

OpenRouter OpenAI (cheap tool‑trained)
- **gpt‑5‑mini (per_turn_append)** — Immediate tool calls (create file, bash). Validator flagged combined `mark_task_complete` as expected.
- **gpt‑5‑nano** — Endpoint sometimes returns HTML; runtime cleanly fails with `ProviderRuntimeError`. When healthy, patch + bash attempts are observed.

OpenRouter GLM / DeepSeek (text only)
- **GLM 4.6** — Plan: `list_dir`, followed by shell inspection.
- **DeepSeek v3.2‑exp** — Plan: `list_dir` calls.

OpenAI direct (gpt‑4o‑mini)
- Not a good coding model for this task; often ignores plan strictness. Deprioritized.

## 6) Logging & Debugging

Per‑run structure:
- `conversation/conversation.md` — transcript
- `raw/requests`, `raw/responses` — exact payloads (keys redacted)
- `prompts/` — compiled system/per‑turn and TPSL catalogs
- `provider_native/` — native tools schemas/calls/results
- `artifacts/` — tool summaries/diffs
- `errors/` — turn‑scoped provider/validation errors

Commands:
- `python scripts/log_reduce.py logging/<run> --tool-only --turn-limit 20`
- `python scripts/log_reduce.py logging/<run> --show-diffs --skip-tree --turn-limit 5`
- `PYTHONPATH=. python scripts/compile_tpsl_preview.py agent_configs/<cfg>.yaml plan|build`

## 7) How to Run

Prereqs
- `.env`: `OPENROUTER_API_KEY` and/or `OPENAI_API_KEY`
- Conda env: `ray_sce_test`

Cheap tool‑trained smoke test
- `conda run -n ray_sce_test python -u main.py agent_configs/opencode_openai_gpt5mini_c_fs_perturn_append.yaml --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 3`

Text‑only routes
- `conda run -n ray_sce_test python -u main.py agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 3`
- `conda run -n ray_sce_test python -u main.py agent_configs/opencode_openrouter_deepseek_v32exp_c_fs_perturn_append.yaml --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 3`

OpenRouter Grok (when endpoint is healthy)
- `conda run -n ray_sce_test python -u main.py agent_configs/opencode_grok4fast_c_fs_v2.yaml --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md --max-iterations 5`

Reduce logs
- `latest=$(ls -1 logging | sort | tail -n 1)`
- `python scripts/log_reduce.py "logging/$latest" --tool-only --turn-limit 20`

## 8) Known Issues & Fixes

Improved
- Clear surfacing of provider HTML responses as `ProviderRuntimeError`
- Mode‑specific TPSL catalogs; OpenCode patch workflows in build; strict plan templates
- V2 `registry.include` honored for tool catalogs; mode allowlists enforced
- Ray isolation to avoid shared cluster collisions

Outstanding
- OpenRouter intermittent HTML payloads (retry/backoff would help)
- Makefile preflight guard (only run `make` once targets exist)
- Strengthen shell guidance (`set -e`, avoid `pipefail`)
- Strict “tools only” compiled plan may still be ignored by some small models — per_turn_append recommended

## 9) Next Steps

1. Add a short retry strategy in `provider_runtime.py` for HTML responses (detect `<html>`); 1–2 backoff retries.
2. Add a Makefile preflight rule in enhanced executor (deny `run_shell` of `make*` until `Makefile` exists with `all/test/clean`).
3. Update builder text to discourage `pipefail`; suggest `set -e` or `bash -lc` when needed.
4. Keep two configs per model (compiled strict vs `per_turn_append`) and bench across providers (Grok, Sonnet 4.5, gpt‑5‑mini/nano, Qwen 3 coder, GLM 4.6, DeepSeek).
5. Optionally add a “plan prose suppression” validator for first N plan turns (soft fail with coaching).

## 10) Appendices

TPSL quick check (no provider):
- Plan: `PYTHONPATH=. python scripts/compile_tpsl_preview.py agent_configs/opencode_grok4fast_c_fs_v2.yaml plan`
- Build: `PYTHONPATH=. python scripts/compile_tpsl_preview.py agent_configs/opencode_grok4fast_c_fs_v2.yaml build`

Skipping non‑project tests by default:
- `pytest.ini` restricts to `tests/`
- `tests/conftest.py` skips `industry_coder_refs/` (opt‑in via `INCLUDE_INDUSTRY_REFS_TESTS=1`)

Environment variables:
- `OPENROUTER_API_KEY`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_TITLE`
- `OPENAI_API_KEY`
- `RAY_SCE_SKIP_LSP=1` to skip LSP‑heavy tests

This handoff (V11) captures the current development direction: drive behavior via configs and templates, keep providers swappable, and ensure logs provide fast, actionable insight. Remaining code changes are surgical (provider retries; make preflight), while most experimental work should proceed through YAML + Jinja.

