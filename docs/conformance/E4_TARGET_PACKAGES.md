# E4 Target Packages

BreadBoard's versioned E4 snapshot configs are now expected to point at tracked target packages
under `config/e4_targets/`.

Why this exists:

- A tiny overlay file is good for freezing a replay lane, but bad for understanding a target harness.
- The target package is the human-readable dossier: prompts, reference material, common policies,
  and the shared runtime/tool substrate for a particular upstream harness version.
- The versioned `agent_configs/*__<snapshot>.yaml` files remain the machine-facing frozen lanes.

Current packaged targets:

- `config/e4_targets/codex/0.107.0/`
- `config/e4_targets/claude_code/2.1.63/`
- `config/e4_targets/opencode/1.2.17/`

Design rules:

1. Keep package assets tracked in-repo; do not rely on gitignored `industry_coder_refs/` paths.
2. Keep scenario-specific replay assertions in the versioned overlay config.
3. Keep the package YAML explicit enough that a reader can inspect the core harness substrate
   without chasing hidden defaults.
4. When upstream harnesses move, mint a new package version instead of mutating the old one.
