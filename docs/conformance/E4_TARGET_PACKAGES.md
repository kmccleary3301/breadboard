# E4 target packages

BreadBoard publishes six current E4 family entries at the top level of
`agent_configs/`. The `2026-09-10` filename date records the catalog refresh, not the
upstream capture date. Exact source versions and claim limits remain explicit in each
file and in `agent_configs/README.md`.

## Current sources

| Family | Accepted source | Package or source asset |
|---|---|---|
| Codex CLI | 0.139.0 with `gpt-5.5`; read-only capture probe | `config/e4_targets/codex/0.139.0/` |
| Claude Code | 2.1.63 static-package/replay capture | `config/e4_targets/claude_code/2.1.63/` |
| OpenCode | 1.2.17 static-package/replay capture | `config/e4_targets/opencode/1.2.17/` |
| oh-my-opencode | 3.10.0, commit `5137df72d8fab3fec609c82f91387db8e3b13825` | `config/e4_targets/oh_my_opencode/3.10.0/` |
| Oh My Pi | `@oh-my-pi/pi-coding-agent@16.2.13`, commit `5356713eae60e67ee64d9b02e3b5e377d248ee7f` | `config/e4_targets/oh_my_pi/16.2.13/` |
| Pi | `@mariozechner/pi-coding-agent@0.57.1` | `config/e4_targets/pi/0.57.1/` |

The Codex labels identify different evidence scopes. The 0.107.0 directory is the
historical GPT-5.1 package snapshot; 0.110.0 collaboration fixtures are historical
replay evidence; 0.139.0 with GPT-5.5 is the accepted read-only capture lane. None may
be relabeled as another.

## Package forms

Oh My Pi and Pi are installed target packages. Their `target.json` files use
`bb.e4.target.v1`; their `harness.yaml` files use `bb.e4.target_config.v1`. Load them
through `breadboard_engine.e4_targets.load_e4_target`. Their top-level catalog files
are readable target-config projections, not BreadBoard agent-config CLI inputs.

Codex, Claude Code, OpenCode, and oh-my-opencode use standalone BreadBoard agent
configs backed by tracked prompt/reference packages. The Codex 0.139.0 package
intentionally contains only the exact prompt source needed by the accepted probe; it
does not fabricate an installed-target descriptor or a broad parity profile.

## Rules

1. Keep package assets tracked in-repo; do not rely on ignored source trees.
2. Preserve historical dossiers byte-for-byte under `agent_configs/deprecated/`.
3. Keep scenario-specific replay assertions in overlays under `agent_configs/misc/`.
4. Mint a new package version when upstream source changes.
5. State capture-only, replay-only, and unsupported surfaces without broadening them.
6. Follow [the dossier style guide](E4_DOSSIER_STYLE_GUIDE_V1.md) for public files.
