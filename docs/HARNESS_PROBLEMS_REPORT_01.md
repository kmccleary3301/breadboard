# Harness Problems Report 01 — Observed Issues, Causes, and Targeted Mitigations

This report catalogs issues observed in recent per_turn runs using GLM‑4.6 and DeepSeek v3.2‑exp, analyzes likely causes in the harness (config, templates, and runtime wiring), and proposes minimal, surgical mitigations without expanding scope.

## TL;DR
- Runs stalled in exploration and never produced patches or tests.
- Strict plan constraints from TPSL were not effectively enforced at run time.
- Per‑turn prompts appended a legacy "FUNCTIONS" catalog each turn, overshadowing stricter templates.
- Models attempted to read system headers outside the workspace, then idled.

We added two small code improvements in this sprint (provider HTML retries; Makefile preflight), and propose minimal config/template tweaks to strengthen plan discipline and early build motion—without major runtime rewrites.

## Evidence (from logs)
- GLM (per_turn): logging/20251008-005200_agent_ws_opencode_openrouter_glm/conversation/conversation.md
  - Turn traces show repeated exploration; later a read of a system path fails; no patches or Makefile.
- DeepSeek (per_turn): logging/20251008-005230_agent_ws_opencode_openrouter_dee/conversation/conversation.md
  - Multiple plan `list_dir`, one `run_shell (pwd && ls)`, no file creation or tests.
- Per‑turn prompt artifacts show a large, generic function catalog ("SYSTEM MESSAGE - AVAILABLE TOOLS <FUNCTIONS> …") appended each turn rather than strict plan catalogs.

## Root Causes
1) TPSL per‑turn enforcement not active in per_turn_append
   - In per_turn_append mode, the runtime appends a legacy composite caller prompt each turn (the "FUNCTIONS" catalog). This dilutes plan constraints and exposes editing + bash tools even on plan turns.
   - The stricter TPSL plan templates compile, but their per‑turn content is not re‑applied each turn in per_turn_append.

2) Mode gating likely not applied to the per‑turn catalog
   - The plan mode in V2 config limits tools to read/list. However, the appended per‑turn content listed edit and bash tools even on early turns—signaling the legacy path ignored plan filtering for the per‑turn message.

3) Workspace scope confusion for reads
   - Models attempted to read system headers (`/usr/include/...`), which are not within the workspace, leading to errors and idling.

## Already Landed (this sprint)
- Provider HTML retry/backoff: agentic_coder_prototype/provider_runtime.py
  - Retries 2x on HTML payloads (short backoff) before surfacing a structured error.
- Makefile preflight: agentic_coder_prototype/execution/enhanced_executor.py
  - Blocks `make*` until a Makefile exists with `all`, `test`, and `clean` targets.
- Builder prompt tweak: de‑emphasize `pipefail`, prefer `set -e`/`bash -lc`.

## Minimal Mitigations (this PR)
1) Config: prefer compiled+persistent per‑turn for OpenRouter GLM and DeepSeek
   - Change `prompts.tool_prompt_mode` to `system_compiled_and_persistent_per_turn` for:
     - agent_configs/opencode_openrouter_glm46_c_fs_perturn_append.yaml
     - agent_configs/opencode_openrouter_deepseek_v32exp_c_fs_perturn_append.yaml
   - Rationale: reduces heavy legacy per‑turn catalogs; keeps a concise per‑turn availability list + compiled system text including plan/build guardrails.

2) Config: explicitly include `features: { plan: true }` in derived configs
   - Ensures first turns are plan‑only even if inheritance is ambiguous.

3) TPSL plan templates: strengthen early progression
   - Add clear guidance: after at most 1–2 `list_dir`/`read_file`, begin creating initial project skeleton via patch (Makefile, headers, .c, minimal tests).
   - Emphasize paths must be workspace‑relative; avoid system absolute paths.

4) TPSL build templates: nudge first patch when workspace is empty
   - If no sources present, emit a single OpenCode patch that adds a basic Makefile + minimal C skeleton and stub tests; then iterate.

## Deferred (out of scope for this sprint)
- Runtime change to prefer TPSL per‑turn over legacy catalogs in per_turn_append. We avoid feature creep and rely on the compiled+persistent mode via config instead.
- Per‑turn re‑compilation of mode‑specific TPSL on every step.

## Validation Plan
- TPSL preview: `PYTHONPATH=. python scripts/compile_tpsl_preview.py <config> plan|build`
- Short smoke runs for GLM/DeepSeek with updated configs; check that:
  - Initial per‑turn content is concise availability, not the full "FUNCTIONS" catalog.
  - Plan turns only show read/list; early turn 2–3 transitions toward emitting the first patch.
  - Makefile preflight blocks premature `make` calls until Makefile exists with required targets.

## Next Steps (if needed)
- If models still stall, consider a soft validator that flags non‑progressive plan turns (N > 2) and injects a gentle coaching nudge in the per‑turn prompt.

