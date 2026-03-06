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
