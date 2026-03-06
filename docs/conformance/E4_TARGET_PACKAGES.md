# E4 Target Packages

BreadBoard's versioned E4 snapshot configs are now expected to point at tracked target packages
under `config/e4_targets/`.

Why this exists:

- A tiny overlay file is good for freezing a replay lane, but bad for understanding a target harness.
- The target package is the human-readable dossier: prompts, reference material, common policies,
  and the shared runtime/tool substrate for a particular upstream harness version.
- The versioned `agent_configs/*__<snapshot>.yaml` files remain the machine-facing frozen lanes.

As of the March 6, 2026 reorg, the top-level `agent_configs/` surface is reserved for the
latest public standalone dossiers for the big four harnesses. Those files should be readable
front doors, heavily commented, and should not rely on `extends`.

Current packaged targets:

- `config/e4_targets/codex/0.107.0/`
- `config/e4_targets/claude_code/2.1.63/`
- `config/e4_targets/opencode/1.2.17/`
- `config/e4_targets/oh_my_opencode/3.10.0/`

Codex note:

- the public Codex dossier now includes dedicated exercised sync/async collaboration replay
  fixtures under `docs/conformance/e4_recalibration_evidence/`
- those fixtures were refreshed on 2026-03-06 against Codex CLI 0.110.0 and document the
  current `spawn_agent` / `wait` event shape used by Codex collaboration flows

Design rules:

1. Keep package assets tracked in-repo; do not rely on gitignored `industry_coder_refs/` paths.
2. Keep scenario-specific replay assertions in the versioned overlay config.
3. Keep the package YAML explicit enough that a reader can inspect the core harness substrate
   without chasing hidden defaults.
4. When upstream harnesses move, mint a new package version instead of mutating the old one.
5. Public top-level dossiers should follow
   [E4_DOSSIER_STYLE_GUIDE_V1.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_DOSSIER_STYLE_GUIDE_V1.md)
   and remain explanatory, not just executable.
