# Codex CLI E4 Target Package - 0.107.0

This package is the tracked target bundle for the Codex CLI 0.107.0 refresh tranche.

The current BreadBoard E4 Codex lane remains a narrow, evidence-backed calibration for the
surfaces we have strict replay/golden coverage for. This package therefore does two things:

- preserves the exact prompt/tool/runtime bundle used by the current lane
- exposes nearby references for the broader Codex harness substrate so the config is not a
  black box

Important references included here:

- `prompts/gpt_5_1_codex_prompt.md`: local copy of the upstream GPT-5.1 Codex prompt
- `refs/agents_md.md`: AGENTS.md / hierarchical-agent guidance docs
- `refs/execpolicy.md`: execution-policy docs
- `refs/sandbox.md`: sandbox and approvals docs
- `refs/custom_prompts.md`: custom prompts docs
- `templates/collaboration_mode/*`: collaboration-mode templates from the upstream repo
- `templates/agents/orchestrator.md`: orchestrator agent template from the upstream repo

The live calibrated lane still intentionally keeps a small model-visible tool surface
(`shell_command`, `apply_patch`, `update_plan`) because that is the surface covered by the
current golden/replay evidence. This package is the first step toward making that tradeoff
explicit instead of hiding it.

The public top-level dossier now also carries dedicated exercised collaboration evidence:

- `docs/conformance/e4_recalibration_evidence/codex_subagent_sync_20260306_v0110/`
- `docs/conformance/e4_recalibration_evidence/codex_subagent_async_20260306_v0110/`

Those replay fixtures were refreshed on 2026-03-06 against Codex CLI 0.110.0 and preserve the
current Codex collab event shape (`spawn_agent`, `wait`, and downstream command execution).
