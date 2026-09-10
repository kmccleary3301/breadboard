# Agent configuration catalog

The six top-level files dated `2026-09-10` are the current E4 catalog. The date is the BreadBoard catalog refresh date, not an upstream capture date. Each file preserves the version and claim boundary of the latest accepted source in `docs/conformance/e4_lane_inventory.json` or, for oh-my-opencode, the pinned freeze-manifest replay row.

| Family | Current file | Upstream identity | Scope |
|---|---|---|---|
| Codex CLI | `codex_0-139-0_gpt55_e4_9-10-2026.yaml` | Codex CLI 0.139.0, `gpt-5.5` | Runnable BreadBoard capture/replay profile. Acceptance is limited to the read-only capture probe; this is not a full Codex parity claim. |
| Claude Code | `claude_code_2-1-63_e4_9-10-2026.yaml` | Claude Code 2.1.63 capture package | Runnable standalone BreadBoard dossier for the accepted static-package/replay surface. |
| OpenCode | `opencode_1-2-17_e4_9-10-2026.yaml` | OpenCode 1.2.17 | Runnable standalone BreadBoard dossier for the accepted static-package/replay surface. |
| oh-my-opencode | `oh_my_opencode_3-10-0_e4_9-10-2026.yaml` | oh-my-opencode 3.10.0 at commit `5137df72d8fab3fec609c82f91387db8e3b13825` | Runnable standalone BreadBoard dossier for the frozen Phase 8 async/subagent replay surface. |
| Oh My Pi | `oh_my_pi_16-2-13_e4_9-10-2026.yaml` | `@oh-my-pi/pi-coding-agent@16.2.13` at commit `5356713eae60e67ee64d9b02e3b5e377d248ee7f` | `bb.e4.target_config.v1` catalog projection. Load the canonical installed target package; do not pass this file to the BreadBoard agent-config CLI. |
| Pi | `pi_0-57-1_e4_9-10-2026.yaml` | `@mariozechner/pi-coding-agent@0.57.1` | `bb.e4.target_config.v1` catalog projection. Load the canonical installed target package; do not pass this file to the BreadBoard agent-config CLI. |

## Run and inspect

The four BreadBoard agent profiles support the existing TUI config path:

```bash
node tui_skeleton/dist/main.js doctor --config agent_configs/codex_0-139-0_gpt55_e4_9-10-2026.yaml
node tui_skeleton/dist/main.js run \
  --config agent_configs/claude_code_2-1-63_e4_9-10-2026.yaml \
  --workspace ./agent_ws \
  "Describe this repository."
```

Those commands may contact the configured provider. The Codex file is intentionally narrow: its accepted evidence covers one read-only capture probe, not collaboration, write execution, or full-session parity.

The TUI defaults to the current Codex profile. Use `--config` or `BREADBOARD_DEFAULT_CONFIG` to select another profile.

Oh My Pi and Pi use the installed-target API. This reads the hashed descriptor and declared assets without invoking a provider:

```bash
python - <<'PY'
from breadboard_engine.e4_targets import load_e4_target

for target_id in ("oh-my-pi@16.2.13", "pi@0.57.1"):
    target = load_e4_target(target_id)
    print(target_id, target.descriptor["execution"])
PY
```

Canonical target packages live under `config/e4_targets/`. Historical frozen dossiers are under `deprecated/`; ATP and Hilbert comparator arms are under `research/`; scenario-specific and supporting profiles remain under `misc/`.

## Claim boundaries

- A current filename does not make an old capture current upstream behavior.
- `config/e4_targets/codex/0.107.0/` remains the historical GPT-5.1 package. The accepted Codex 0.139.0 probe uses the separately tracked 0.139.0 prompt asset.
- Oh My Pi and Pi package descriptors are the machine authority. Their top-level files expose the package config for readers but do not replace `target.json` or `config/e4_targets/index.json`.
- Frozen evidence and declarations retain their original logical paths and hashes. `deprecated/manifest.json` maps relocated inputs to byte-identical archives for E4 readers; active commands use current profiles.
- The LongRun parity audit checks active agent profiles, excludes `deprecated/`, and reports target-config projections separately as non-applicable.
