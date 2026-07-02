# Codex CLI E4 Target Package - package snapshot 0.107.0

This directory is the tracked Codex package snapshot for the GPT-5.1 Codex Mini lane family.
Its path (`config/e4_targets/codex/0.107.0/`) is intentionally stable because in-repo
overlays extend files from this directory.

Version labels in this Codex dossier name different evidence scopes:

- package snapshot: Codex CLI 0.107.0 prompt/policy/runtime substrate in this directory
- collaboration replay evidence: Codex CLI 0.110.0 sync/async fixtures refreshed on 2026-03-06
- accepted C4 lane pin: Codex CLI 0.139.0 with model `gpt-5.5`, recorded in
  `docs/conformance/e4_lane_inventory.json` and `config/e4_target_freeze_manifest.yaml`

The BreadBoard E4 Codex lane remains a narrow, evidence-backed calibration for the
surfaces with strict replay/golden coverage. This package therefore does two things:

- preserves the exact prompt/tool/runtime bundle used by the 0.107.0 package snapshot
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

The public top-level dossier also carries dedicated exercised collaboration evidence:

- `docs/conformance/e4_recalibration_evidence/codex_subagent_sync_20260306_v0110/`
- `docs/conformance/e4_recalibration_evidence/codex_subagent_async_20260306_v0110/`

Those replay fixtures were refreshed on 2026-03-06 against Codex CLI 0.110.0 and preserve the
current Codex collab event shape (`spawn_agent`, `wait`, and downstream command execution).
They do not retitle this package directory and do not replace the later accepted 0.139.0
Codex GPT-5.5 C4 support lane.
