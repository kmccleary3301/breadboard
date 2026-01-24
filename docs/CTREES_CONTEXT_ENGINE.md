# C‑Trees: Context Engine (Engine)

This document describes the **engine-only** “C‑Trees context engine” feature: a deterministic way to compile a
session’s recorded C‑Tree into a small, prompt-safe context note that can be injected into the model request.

Key goals:
- **Deterministic** behavior (stable across replays given the same semantic inputs)
- **Safe-by-default** (avoid leaking secrets / avoid huge payloads)
- **Feature-gated** (no behavior change unless explicitly enabled)

## Current limitations (important)

Today, C‑Tree `message` nodes store a **summary** payload (`role/content/tool_calls/name`) and do **not** preserve all
provider-specific fields (e.g., tool-call linkage fields). Because of this, the context engine should be treated as a
**prompt injection tool** (prepend a system note), not as a full “rebuild the provider message list from scratch”
mechanism.

The `replace_messages` mode exists for experimentation, but is **dangerous** and disabled unless explicitly opted in
(see config below).

## Configuration

All keys below live under the agent config:

```yaml
ctrees:
  context_engine:
    enabled: false                # default: false
    mode: off                     # off | prepend_system | replace_messages
    stage: SPEC                   # RAW | SPEC | HEADER | FROZEN (default: SPEC)
    allow_in_replay: false        # default: false
    dangerously_allow_replace: false  # default: false

    selection:
      kind_allowlist: [message]   # default: [message]
      max_nodes: null             # optional
      max_turns: null             # optional

    collapse:
      target: null                # optional
      mode: all_but_last          # none | all_but_last (optional)

    header:
      mode: hash_only             # hash_only | sanitized_content
      preview_chars: 160          # truncation for sanitized content previews

    redaction:
      secret_like_strings: true   # default: true (applies to injected previews)
```

Notes:
- `enabled: false` and `mode: off` mean **no behavior change**.
- The context engine runs through the engine hook system. You must also enable hooks:

```yaml
hooks:
  enabled: true
  ctree_context_engine: true
```

## What gets injected (prepend_system)

When enabled with:

```yaml
ctrees:
  context_engine:
    enabled: true
    mode: prepend_system
    header:
      mode: sanitized_content
```

the engine injects a deterministic **system** message at the beginning of the provider message list. The system message
includes:
- stable counts (selected/kept/dropped/collapsed)
- a stable `selection_sha256`
- per-message metadata from the compiler’s HEADER stage
  - if `header.mode: sanitized_content`, truncated and redacted previews are included
  - otherwise, only hashes/lengths are included

This injection is intentionally small and safe-by-default.

## Determinism rules

The context engine’s selection hash is computed from a canonical JSON representation of:
- the selected/kept/dropped/collapsed id lists
- stage and selection counts

The underlying C‑Tree hashing/persistence already strips volatile fields (`seq`, timestamps) and redacts obvious secret
keys. The context engine additionally supports “secret-like string” redaction in injected previews.

## Replace mode (dangerous)

`mode: replace_messages` attempts to replace the provider message list. This is **not safe** unless:
- your provider message objects are simple `{role, content, name, tool_calls}` dicts, and
- you have explicitly set `dangerously_allow_replace: true`.

If these conditions aren’t met, the engine falls back to `prepend_system` (best-effort) and records a warning in
provider metadata.
