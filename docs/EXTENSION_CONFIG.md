# Extension Configuration (Draft)

This document describes the **engine configuration surface** for extensions and hooks.

## Current Config Keys

Extensions are **disabled by default**. Enable them explicitly in the agent config:

```yaml
hooks:
  enabled: true
  ctree_recorder: true
  collect_decisions: false
```

### Supported keys

- `hooks.enabled` (bool): master toggle for hook execution.
- `hooks.ctree_recorder` (bool): enable the C‑Tree recorder hook.
- `hooks.ctree_context` (bool): enable the C‑Tree before-model metadata hook.
- `hooks.collect_decisions` (bool): collect `policy_decisions` emitted by hooks (debug/audit).
- `hooks.extra_hooks` (list): load additional hook objects from import paths (advanced).

Example:

```yaml
hooks:
  enabled: true
  collect_decisions: true
  ctree_recorder: false
  extra_hooks:
    - agentic_coder_prototype.hooks.example_hooks.ExampleBeforeEmitTaggerHook
    - path: agentic_coder_prototype.hooks.example_hooks.ExamplePolicyDecisionHook
      enabled: true
      kwargs: {}
```

## Notes

- Hooks currently emit **legacy phases** (`pre_turn`, `pre_tool`, etc.). Ordering is mapped to canonical phases via `docs/HOOK_PHASE_MAPPING.md`.
- Canonical phases like `before_model`/`after_model`/`before_emit` may be invoked directly; hooks can declare those phases verbatim.
- Plugin manifest → hook loading is not wired yet; `hooks.extra_hooks` is the current mechanism for advanced/custom hooks.
- Deterministic ordering rules are defined in `docs/EXTENSION_MIDDLEWARE_SPINE.md`.

## Status
Draft — update as runtime extension surfaces expand.
