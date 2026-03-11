# Tool Bindings And Effective Surfaces V1

## Purpose

This dossier defines the shared contract family for:

- canonical tool bindings
- environment-aware availability
- effective tool surfaces

It exists because BreadBoard already has multiple tool ingress paths, but not yet one unified semantic model that explains:

- what a tool is
- where it can run
- how it is exposed
- why it is or is not available in a given environment

## Contract role

The tool-binding family must describe:

- canonical tool identity
- environment selector / availability conditions
- binding kind (`host`, `sandbox`, `service`, `mcp`, `delegated`, `provider_hosted`)
- policy and evidence defaults
- provider/model exposure rules
- the effective resolved tool surface for one session/profile/context

## Shared semantics that must be frozen

### 1. Canonical tool truth is separate from binding

Tool identity and schema are not the same thing as execution locus.

### 2. Binding is first-class

The kernel must be able to express:

- what satisfies a tool
- what it requires
- what fallback bindings exist
- what risk/evidence profile applies

### 3. Availability is contextual

Support depends on:

- workspace
- execution profile
- image/features
- provider
- service reachability
- policy

### 4. Effective tool surface is inspectable

The system should be able to say:

- which tools are currently in scope
- which bindings won
- which tools were hidden or degraded and why
- what provider/model projections were derived

### 5. MCP and provider-native tools are ingress/projection paths

They are not canonical schema ownership.

## Non-goals

This dossier does not require:

- plugin/package provenance to become kernel truth
- automatic installers to be first-class semantics
- provider-native wire payloads to be author-owned truth

## Immediate schema implications

The first-pass schema set should likely include:

- `bb.environment_selector.v1`
- `bb.tool_binding.v1`
- `bb.tool_support_claim.v1`
- `bb.effective_tool_surface.v1`

## Relationship to existing contracts

- builds directly on `bb.tool_spec.v1`, `bb.tool_call.v1`, `bb.tool_execution_outcome.v1`, and `bb.tool_model_render.v1`
- reuses `bb.execution_capability.v1` and `bb.execution_placement.v1`
- should feed into Backbone `SupportClaim` and Workspace shaping, not replace them
- should be made visible in public dossiers/configs via explicit `tool_packs:` and `tool_bindings:`
  sections rather than being left as hidden runtime convention

## Current implementation notes

The first shipped Phase 14 tranche now includes:

- kernel-core effective tool surface resolution using environment selectors
- Backbone exposure for building and resolving effective tool surfaces
- dossier/config support for `tool_packs:` and `tool_bindings:`
- conformance fixtures for:
  - a minimal effective tool surface
  - a visible terminal-session-backed effective surface
  - a hidden terminal-tool surface when the environment does not support those bindings

Phase 14 now also includes the first Codex-shaped effective tool-surface fixture for the
background-terminal pair:

- visible `exec_command`
- visible `write_stdin`
- explicit binding ids
- explicit projection profile

This is still parity-prep evidence, not a frozen E4 claim.

Visibility note:

- a tool can be fully bound and operational but still hidden from the model
- a tool can fall back from one binding to another while preserving the same canonical tool id
- terminal-session-backed tools are the clearest current example of this, because their visibility
  depends on execution profile and driver support rather than only on catalog presence
