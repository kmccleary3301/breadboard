# E4 target packages

Each directory under `config/e4_targets/` is a tracked, version-pinned source package.
The current human entry points are the six dated files in `agent_configs/`.

Three package forms exist:

- Oh My Pi 16.2.13 and Pi 0.57.1 have `bb.e4.target.v1` descriptors, indexed by
  `index.json`, plus `bb.e4.target_config.v1` harness assets.
- Mini SWE Agent 2.4.6 has a `bb.e4.target.v2` descriptor and
  `bb.e4.target_config.v2` configuration. Its headless profile uses the native
  `bash` tool, an owned Python runtime and an explicit non-streaming Chat provider.
  The package does not by itself establish runtime qualification or source parity.
- Codex 0.139.0, Claude Code 2.1.63, OpenCode 1.2.17, and oh-my-opencode 3.10.0
  retain the prompt/reference packages used by BreadBoard agent configs. Do not invent
  target descriptors for these families until the installed-target loader supports them.

Package assets are tracked in-repo. Current configs must not depend on ignored
`industry_coder_refs/` or `other_harness_refs/` trees.

Historical package versions remain immutable. In particular,
`config/e4_targets/codex/0.107.0/` is the GPT-5.1 package snapshot; the accepted
Codex CLI 0.139.0 / GPT-5.5 capture uses `codex/0.139.0/`.
