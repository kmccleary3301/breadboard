# Hook Phase Mapping (Draft)

This document maps the **current hook phases** used in the runtime to the **canonical extension phases** defined in `docs/EXTENSION_MIDDLEWARE_SPINE.md`.

## Mapping Table

| Legacy hook phase | Canonical extension phase |
| --- | --- |
| `pre_turn` | `on_turn_start` |
| `post_turn` | `on_turn_end` |
| `pre_tool` | `before_tool` |
| `post_tool` | `after_tool` |
| `completion` | `on_session_end` |
| `ctree_record` | `before_emit` |

## Notes

- Runtime emits **legacy hook phases** (`pre_turn`, `pre_tool`, etc.). The hook manager maps these to canonical phases **for deterministic ordering**.
- Canonical phases like `before_model`/`after_model` are invoked **directly** around model calls (hooks can declare these phases verbatim).
- Unknown phases fall back to `before_emit` ordering and are listed in the hook snapshot for visibility.

## Status
Draft — update as runtime hook coverage expands.
