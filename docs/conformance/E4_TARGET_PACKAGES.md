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

- `config/e4_targets/codex/0.107.0/` — package snapshot for the GPT-5.1 Codex Mini lane family; newer Codex evidence keeps its own version label instead of renaming this directory.
- `config/e4_targets/claude_code/2.1.63/`
- `config/e4_targets/opencode/1.2.17/`
- `config/e4_targets/oh_my_opencode/3.10.0/`

Codex version labels:

- package snapshot: `config/e4_targets/codex/0.107.0/`
- collaboration replay evidence: Codex CLI 0.110.0 fixtures under
  `docs/conformance/e4_recalibration_evidence/codex_subagent_*_20260306_v0110/`
- accepted C4 lane pin: Codex CLI 0.139.0 with model `gpt-5.5`, recorded in
  `docs/conformance/e4_lane_inventory.json` and `config/e4_target_freeze_manifest.yaml`

Design rules:

1. Keep package assets tracked in-repo; do not rely on gitignored `industry_coder_refs/` paths.
2. Keep scenario-specific replay assertions in the versioned overlay config.
3. Keep the package YAML explicit enough that a reader can inspect the core harness substrate
   without chasing hidden defaults.
4. When upstream harnesses move, mint a new package version instead of mutating the old one.
5. Public top-level dossiers should follow
   [E4_DOSSIER_STYLE_GUIDE_V1.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_DOSSIER_STYLE_GUIDE_V1.md)
   and remain explanatory, not just executable.
