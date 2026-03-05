# E4 Cookbook (V1)

Date: 2026-03-05

## Purpose

This document is the practical runbook for taking a target harness from:

1. selected upstream version
2. capture/logging instrumentation
3. fresh golden samples
4. replay-session translation
5. BreadBoard config alignment
6. strict replay verification
7. frozen, versioned E4 snapshot rows

It is written to support the workflow we have actually been using for Codex CLI,
Claude Code, OpenCode, and `oh-my-opencode`.

Use this together with:

- [E4_RECALIBRATION_STATUS_V1.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_RECALIBRATION_STATUS_V1.md)
- [E4_CAPTURE_REFRESH_AND_VERSIONING_V2.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_CAPTURE_REFRESH_AND_VERSIONING_V2.md)
- [E4_TARGET_VERSIONING.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_TARGET_VERSIONING.md)
- [config/e4_target_freeze_manifest.yaml](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/config/e4_target_freeze_manifest.yaml)

## What E4 means here

E4 does not mean “the model says identical words.”

E4 means BreadBoard matches the target harness on the controllable surfaces:

- request/body/tool schema shape
- system prompt and tool-presentation surfaces where applicable
- tool-call parsing
- tool execution semantics
- tool-result formatting
- replay behavior
- workspace side effects
- event ordering and other model-visible interface details

The actual model generations are frozen through captured goldens and replay
fixtures. That is why the pipeline depends on goldens and replay sessions rather
than live head-to-head output comparison.

## Core artifacts

### Target version metadata

The canonical freeze file is:

- [config/e4_target_freeze_manifest.yaml](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/config/e4_target_freeze_manifest.yaml)

Each E4 row should identify:

- harness family
- upstream repo
- upstream commit
- upstream commit date
- release label or snapshot label
- runtime provider/model surface
- calibration anchor class
- scenario id / run id
- tracked evidence paths

### Replay scenario routing

For replay-driven lanes, the central manifest is typically:

- [misc/opencode_runs/parity_scenarios.yaml](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/misc/opencode_runs/parity_scenarios.yaml)

### Batch runner

- [scripts/run_parity_replays.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/scripts/run_parity_replays.py)

### Strict evidence validation

- [scripts/check_e4_target_freeze_manifest.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/scripts/check_e4_target_freeze_manifest.py)
- [scripts/audit_e4_target_drift.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/scripts/audit_e4_target_drift.py)

## The end-to-end workflow

### 1. Pick the exact target harness version

Do not begin by “updating the config.”

Begin by freezing:

- the repo
- the commit
- the release tag if one exists
- the local runtime version string from the binary itself

Examples of what matters:

- Codex CLI: local binary `--version`, plus ref repo head
- Claude Code: local binary `--version`, plus vendor bundle commit
- OpenCode: package version and repo head
- `oh-my-opencode`: package/tag version and repo head

Record this before capture. If you skip this, you will not know what your E4
lane was actually calibrated against.

### 2. Choose the capture strategy for that harness family

The capture mechanism is harness-specific.

#### Codex CLI

Primary approach:

- rollout JSONL / native output capture
- then convert into replay sessions for BreadBoard

Relevant scripts usually include:

- `scripts/capture_codex_golden.sh`
- `scripts/convert_codex_rollout_to_replay_session.py`

#### OpenCode

Primary approach:

- instrumented or exported session dump
- replay-session conversion

Relevant scripts usually include:

- `scripts/capture_opencode_golden.sh`
- `scripts/convert_opencode_export_to_replay_session.py`

#### oh-my-opencode

Primary approach:

- OpenCode-adjacent replay/export path
- scenario-specific replay-session creation

Relevant scripts usually include:

- `scripts/capture_oh_my_opencode_golden.sh`

#### Claude Code

Primary approach:

- harness-specific capture wrapper
- structured provider/network dumps
- normalized turn files
- replay-session conversion

Relevant files:

- [scripts/run_claude_code_logged.sh](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/scripts/run_claude_code_logged.sh)
- [scripts/capture_claude_golden.sh](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/scripts/capture_claude_golden.sh)
- [scripts/convert_claude_logged_run_to_replay_session.py](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/scripts/convert_claude_logged_run_to_replay_session.py)

Important clarification:

- Keep the public doc at the workflow level.
- Internal implementation details for the Claude capture path should live in
  `docs_tmp/`, not the repo docs.
- Do not make transport-layer claims in public docs unless they are necessary
  for operating the workflow.

### 3. Capture the smallest sufficient golden set

Do not start with a massive scenario matrix.

Start with the minimum lanes needed to lock the important surfaces:

- one lightweight ping or compact semantic lane
- one MVI lane
- one or more high-value tool lanes
- one subagent lane if the harness supports it
- one async lane if the harness supports it

Examples:

- Codex CLI:
  - ping
  - MVI patch lane
  - sync subagent lane
  - async subagent lane
- Claude Code:
  - compact semantic ping lane
  - legacy strict replay lanes
  - subagent and async lanes where fixtures exist
- OpenCode:
  - MVI replay
  - glob/grep sentinel
  - patch/todo sentinel
  - toolcall repair sentinel
  - webfetch sentinel
- oh-my-opencode:
  - async subagent replay lane

The capture should produce enough evidence to reconstruct:

- prompt context
- tool calls
- tool results
- final workspace state
- metadata for strict replay comparison

### 4. Normalize and translate into replay sessions

The raw capture is not the final replay asset.

You must translate it into the replay schema BreadBoard uses.

Typical stages:

1. raw provider dump or export
2. sanitization, if needed
3. normalized turn files or session data
4. replay-session JSON
5. optional parity summary or supporting evidence bundle

The replay session should preserve the model-visible tool/result interface. If
you lose tool outputs, tool ids, or call ordering here, the E4 lane will be
fake even if the workspace happens to match.

### 5. Place evidence in stable locations

A recurring source of pain is evidence living only in ignored or temporary
paths.

Do two things:

1. keep the original run artifacts where the capture workflow expects them
2. mirror or copy the minimal canonical evidence into tracked doc locations

Typical tracked evidence locations:

- `docs/conformance/e4_recalibration_evidence/...`
- scenario-specific JSON under `docs/conformance/...`

Use tracked copies for:

- replay sessions
- scenario summaries
- parity summaries
- scenario manifests
- run summaries

This is what lets the freeze manifest survive repo recovery and cleanup.

### 6. Align or create the BreadBoard config

Only after capture and replay assets exist should you align the BreadBoard
config.

Config work usually includes:

- tool schema names and descriptions
- permission mode semantics
- workspace behavior
- tool-result formatting
- replay-specific fixture handling
- reminder / system notice behavior
- subagent surface naming or result formatting

Do not overfit the config to one passing lane if it breaks another surface. The
goal is harness-family parity, not a single green scenario.

### 7. Run strict parity probes

Run replay probes in strict mode. The exact invocation varies, but the pattern
is always the same:

- fail on tool-output mismatch
- preserve the expected replay session and golden workspace
- inspect both summary and per-scenario output

Examples already used in this repo:

- `make e4-postrestore-strict-probe`
- `make e4-claude-legacy-strict-probe`
- direct `python scripts/run_parity_replays.py --scenario ...`

The probe is only good if it is strict enough to fail on the surface you claim
to match.

### 8. Freeze the lane in the manifest

Once the lane is genuinely passing:

1. update `config/e4_target_freeze_manifest.yaml`
2. point it at the exact upstream commit/tag/version
3. point it at tracked evidence paths
4. add or update the versioned snapshot config
5. keep the previous snapshot configs unless there is an explicit reason to
   retire them

This step is where the lane becomes auditable instead of informal.

### 9. Re-run freeze validation and drift audit

Required checks after freezing:

```bash
python scripts/check_e4_target_freeze_manifest.py --strict-evidence --json
python scripts/audit_e4_target_drift.py
```

Interpretation:

- `strict-evidence` tells you whether the manifest points at real evidence
- drift audit tells you whether upstream moved since your freeze

Upstream drift does not invalidate the existing snapshot. It only means the
snapshot is no longer “latest.”

## Versioning policy

When a target harness moves, do not overwrite history casually.

Preferred pattern:

1. keep the base config name for the currently active lane
2. create versioned snapshot configs that encode the target versions
3. update the freeze manifest to map both the base lane and the versioned lane
4. leave older versioned configs in place unless they are known-bad

Examples already in use include tags like:

- `codex0_1070_claude2_1_63_opencode1_2_17_20260305`
- `..._ohmyopencode3_10_0_20260305`

This is the right direction because these harnesses move frequently.

## Harness-specific notes

### Codex CLI

What usually matters most:

- rollout event extraction
- `apply_patch` and shell-output fidelity
- subagent sync/async artifacts

Risk:

- upstream moves quickly, so refreshes can stale quickly

### Claude Code

What usually matters most:

- `claude-code-logged` wrapper health
- provider dump integrity
- conversion into replay session
- system reminder surfaces
- permission semantics
- subagent replay fixtures

Risk:

- easiest lane to misremember
- transport detail should be documented as fetch/SSE based unless new evidence
  proves otherwise

### OpenCode

What usually matters most:

- export/session conversion
- sentinel lanes for specific tools and repairs
- parity scenario routing

Risk:

- lots of targeted replay lanes, so scenario wiring drift is common

### oh-my-opencode

What usually matters most:

- version/tag pinning
- async subagent behavior
- keeping the replay scenario pointed at tracked evidence

Risk:

- repo layout or upstream naming can differ from OpenCode enough to break
  assumptions in capture scripts

## Common failure modes

### Evidence exists only in ignored locations

Fix:

- copy canonical evidence into tracked `docs/conformance/...` locations

### Scenario file points at stale or missing replay session

Fix:

- repoint the scenario manifest to the tracked replay session
- rerun the strict replay lane

### Config passes one lane but regresses another

Fix:

- do not bless immediately
- run the minimal representative bundle for that harness family

### Drift audit shows red right after refresh

Fix:

- verify whether upstream simply advanced after capture
- if yes, keep the snapshot and document that it is frozen against the earlier
  head

### Claude runs capture provider dumps but replay conversion is weak

Fix:

- inspect the converter first
- the wrapper may be fine while the translation layer is wrong

## Minimal checklist for adding or refreshing an E4 lane

- [ ] Pull or update the target harness ref repo.
- [ ] Record repo, commit, release/tag, and local runtime version.
- [ ] Choose the smallest sufficient calibration scenarios.
- [ ] Capture fresh artifacts with the correct harness-specific logger/export path.
- [ ] Sanitize and normalize the output if needed.
- [ ] Convert the capture into replay-session assets.
- [ ] Mirror canonical evidence into tracked `docs/conformance/...` paths.
- [ ] Align or create the BreadBoard config.
- [ ] Run strict replay probes and inspect failures.
- [ ] Update `parity_scenarios.yaml` if the lane is replay-driven.
- [ ] Update `config/e4_target_freeze_manifest.yaml`.
- [ ] Add a new versioned snapshot config.
- [ ] Run `check_e4_target_freeze_manifest.py --strict-evidence`.
- [ ] Run drift audit.
- [ ] Update `E4_RECALIBRATION_STATUS_V1.md`.

## Practical rule of thumb

If someone asks, “how did we get to E4?”, the honest answer should be:

“By freezing a specific upstream harness version, capturing real golden
artifacts from that version, translating them into replay fixtures, aligning
BreadBoard’s controllable surfaces until strict replay probes passed, and then
recording the result in the freeze manifest with tracked evidence.”

If that sentence is not true for a lane, it is not an E4 lane yet.
