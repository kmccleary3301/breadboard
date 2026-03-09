# @breadboard/workspace

`@breadboard/workspace` is the first public workspace-layer package for the V3 backbone program.

It is intentionally above raw execution drivers and below host-specific integration layers.

This package currently provides:
- workspace capability sets
- execution-profile support checks
- artifact references
- tool-output shaping suitable for model-visible and user-visible views

It does **not** attempt to own filesystem execution, sandboxing, or host persistence by itself.
Those remain delegated to lower-level runtime and driver packages.
