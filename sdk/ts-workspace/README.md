# @breadboard/workspace

`@breadboard/workspace` is the first public workspace-layer package for the V3 backbone program.

It is intentionally above raw execution drivers and below host-specific integration layers.

This package currently provides:
- workspace capability sets
- execution-profile support checks
- rich public `ExecutionProfile` objects with placement/security/backend hints
- artifact references
- tool-output shaping suitable for model-visible and user-visible views
- terminal-output shaping for chunked session output
- terminal-end shaping so hosts can render artifact/evidence refs and end-state metadata without
  reading raw kernel payloads directly

Example shaping pattern:

- user-visible output may preserve more context and artifact links
- model-visible output should stay shorter and safer
- artifact references should carry the full path or locator for follow-up inspection

It does **not** attempt to own filesystem execution, sandboxing, or host persistence by itself.
Those remain delegated to lower-level runtime and driver packages.
