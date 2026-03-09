# Workspace Layer v1

## Purpose

Workspace is the public integration surface for filesystem/search/exec-facing host concerns above raw drivers.

The V3 goal is to make Workspace the premium TS-facing place where developers reason about:
- capabilities
- execution-profile support
- artifacts
- output shaping

without forcing them to wire driver internals manually.

## Key public nouns

- `Workspace`
- `WorkspaceCapabilitySet`
- `WorkspaceArtifactRef`
- `ToolOutputShaper`

## Current v1 slice

The current implementation slice provides:
- capability sets
- execution-profile support checks
- artifact references
- tool output shaping for user-visible and model-visible views

## Non-goals

Workspace does not currently own:
- full filesystem adapter logic
- search backends
- sandbox lifecycle
- host persistence

Those stay below or beside the Workspace layer.
