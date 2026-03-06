# E4 Dossier Style Guide (V1)

Date: 2026-03-06

## Purpose

This document defines what a public top-level E4 dossier in `agent_configs/`
should look like.

The goal is simple:

- a reader should be able to open a single top-level E4 config and understand
  the soul of the target harness
- the file should be executable as configuration
- the file should also act as a reverse-engineered public dossier

This is the standard that prevents the repo from drifting back toward tiny,
opaque pointer files.

Use this together with:

- [E4_TARGET_PACKAGES.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_TARGET_PACKAGES.md)
- [E4_COOKBOOK_V1.md](/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo/docs/conformance/E4_COOKBOOK_V1.md)

## The three-layer model

An E4 harness publication should be split mentally into three layers:

### 1. Public dossier

Location:

- top-level `agent_configs/<target>_<version>_e4_<date>.yaml`

Purpose:

- human front door
- heavily commented
- standalone
- readable line by line

This file should answer:

- what is this harness?
- what makes it feel like the target?
- what policies matter?
- what public surfaces are actually frozen?
- where does the evidence live?

### 2. Target package

Location:

- `config/e4_targets/<target>/<version>/`

Purpose:

- tracked prompt bundle
- tracked references
- common shared substrate for that target/version

### 3. Replay and evidence bundle

Location:

- `docs/conformance/e4_recalibration_evidence/...`

Purpose:

- reproducible proof
- replay session
- scenario manifest
- parity summary
- minimal canonical workspace evidence

This layer proves the public claim.

## Hard rules for public dossiers

### Rule 1: top-level dossiers must be standalone

They should not use:

- `extends`

### Rule 2: comments must explain meaning, not repeat syntax

Comments should explain:

- why a field exists
- what product behavior it controls
- whether it is core harness identity or lane-specific calibration

### Rule 3: every major section should answer \"why does this exist?\"

At minimum:

- `workspace`
- `providers`
- `provider_tools`
- `prompts`
- `tools`
- `loop` / `modes`
- `guardrails`
- `permissions`
- `completion`
- `concurrency`
- `multi_agent` when relevant
- `replay`

### Rule 4: if a surface is not frozen, say so plainly

Do not imply broader parity than the evidence supports.

### Rule 5: public evidence paths must be tracked in repo

Do not point public dossiers at:

- gitignored scratch paths
- ephemeral local run directories

Prefer:

- `docs/conformance/e4_recalibration_evidence/...`

## Recommended dossier anatomy

### 1. Header block

Include:

- target name
- target version
- calibration note
- standalone note
- target-package location

### 2. Small ASCII dossier map

Summarize the harness backbone in 3-5 lines.

### 3. Reading guide

Explain:

- what this file is
- what lives in the adjacent package
- whether this is broad substrate or a narrow replay lane

### 4. Section-level commentary

Each major section should explain meaning, not just labels.

### 5. Small diagrams where behavior is non-obvious

Good uses:

- plan -> build loop
- reminder/cache/retry lifecycle
- parent/subagent topology
- background task vs subagent split

## Field legend

### `providers`

Interpret as:

- runtime/provider contract
- not merely a model id

### `provider_tools`

Interpret as:

- how the harness exposes tools to the model

### `prompts`

Interpret as:

- how the harness teaches itself to the model

### `tools.registry.paths`

Interpret as:

- schema catalog boundary

Public dossiers should explain:

- where the real schemas live
- what the effective visible tool surface is
- why the catalog is not being inlined

### `features`

Interpret as:

- which public behaviors this lane is actually claiming

### `multi_agent`

Interpret as:

- visible orchestration contract

The dossier should say:

- whether this is inferred or exercised
- what the spawn tool is called
- whether async is enabled
- how joins happen
- what workspace sharing looks like

### `replay`

Interpret as:

- the public parity claim boundary

This section should tell the reader:

- which tool outputs matter
- whether tool names are preserved
- how strict the replay expectation is

## Minimum bar for future refreshes

Any new top-level E4 dossier should:

1. be standalone
2. include a header block, dossier map, and reading guide
3. include section-level commentary for every major harness area
4. point only at tracked evidence for public claims
5. explain omitted or not-yet-frozen surfaces honestly

If a future refresh does not satisfy those five points, it is incomplete.
