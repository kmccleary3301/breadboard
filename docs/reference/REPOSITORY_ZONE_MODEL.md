# Repository Zone Model

This page defines the stable meaning of BreadBoard's top-level repository zones.

The goal is simple:

- make the top level readable
- make canonical ownership explicit
- stop forcing new contributors to infer which root is product, substrate,
  extension, or support machinery

## Zone Summary

| Root | Zone type | What it is | Canonical use |
|---|---|---|---|
| `breadboard/` | `public product surface` | core product-facing Python package for sandboxing and related host-visible runtime helpers | public-facing Python module area when the package already lives there |
| `agentic_coder_prototype/` | `transitional internal runtime substrate` | the canonical internal engine and orchestration substrate currently carrying the majority of runtime authority | canonical home for new internal engine/runtime code during the current cleanup phase |
| `breadboard_sdk/` | `SDK surface` | Python SDK package area | stable SDK-facing code and interfaces |
| `breadboard_ext/` | `extension space` | extension and experimental lane for non-core product surfaces | bounded extension work that should not claim canonical engine authority |
| `sdk/` | `SDK and host surface` | TypeScript SDKs, host layers, transport adapters, and execution drivers | canonical home for TS, host, and driver work |
| `tool_calling/` | `tooling/support surface` | tool-dialect examples and related support assets outside the internal runtime substrate | examples, demos, and externalized tool-calling support work |
| `scripts/` | `tooling/support surface` | setup, release, ops, migration, and research scripts | operator and maintainer tooling, organized by category |
| `docs/` | `docs surface` | durable tracked documentation | public docs, reference docs, internals, and archive according to taxonomy |

## Practical Meaning

### `breadboard/`

Treat `breadboard/` as a legitimate public product package area.

This is where product-facing Python helpers belong when they are already clearly
part of the BreadBoard product identity, especially around sandboxing and
related execution surfaces.

### `agentic_coder_prototype/`

Treat `agentic_coder_prototype/` as the transitional internal runtime
substrate.

That means:

- it is still the canonical home for most internal runtime authority today
- it is not the polished public brand we want to foreground
- new internal engine/runtime code should prefer its canonical subpackages
- a future mass rename is a separate migration event, not part of the current
  cleanup tranche

### `breadboard_ext/`

Treat `breadboard_ext/` as extension space, not as a second canonical runtime
root.

Work that lands here should be clearly extension-oriented and should not muddy
the question of where the main runtime authority lives.

### `sdk/` and `breadboard_sdk/`

These are SDK surfaces, not cleanup leftovers:

- `breadboard_sdk/` is the Python SDK surface
- `sdk/` is the TypeScript and host-systems surface

If code is fundamentally SDK or host adoption work, it should not drift into the
internal runtime substrate just because that substrate already exists.

### `tool_calling/`

This root is support/tooling surface, not the canonical internal runtime home
for tool-call logic. The canonical internal home is now under:

- `agentic_coder_prototype/tool_calling/`

### `scripts/`

Treat `scripts/` as categorized tooling:

- `dev`
- `ops`
- `migration`
- `release`
- `research`
- `archive`

The point is not just neat folders. The point is making operator and maintainer
entrypoints legible.

### `docs/`

Tracked `docs/` is the durable documentation surface.

Use:

- `getting-started/` for onboarding
- `guides/` for workflows
- `reference/` for stable lookups
- `concepts/` for subsystem explanations
- `internals/` for maintainer-facing tracked material
- `archive/` for tracked historical records

Local planning and execution archaeology still belong in `docs_tmp/`.

## Canonical-New-Code Rule

When adding new code, prefer:

- the canonical existing package for that domain, if one exists
- the documented zone meaning above

Do not create a new top-level authority root unless the work truly introduces a
new long-lived zone.

## Reopen Conditions

The zone model should be reopened only if one of these becomes true:

- the runtime substrate is ready for a deliberate mass rename
- the repo gains a genuinely new top-level product or SDK zone
- extension work starts to overlap with canonical runtime authority again

Until then, this is the stable top-level map.
