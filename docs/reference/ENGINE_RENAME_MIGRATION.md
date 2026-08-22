# Engine Rename Migration: `agentic_coder_prototype` -> `breadboard_engine`

The canonical internal engine package is `breadboard_engine/`. The historical
name `agentic_coder_prototype` remains importable as a temporary compatibility
namespace. This page is the operator/developer migration reference.

## What changed

- Every first-party module, test, script, config, workflow, and doc reference
  was rewritten to `breadboard_engine` in the R-1 freeze event (five-commit
  structure; merge `2b53f1816`).
- `agentic_coder_prototype/` now contains exactly one file: a shim
  `__init__.py` (generated from `scripts/dev/shim_package_init.template.py`)
  that installs a prefix-wide alias finder and self-replaces with
  `breadboard_engine`.
- The wheel ships both packages; the shim adds no runtime weight beyond the
  finder installation.

## Migrating your code

| Old | New |
|---|---|
| `import agentic_coder_prototype.x.y` | `import breadboard_engine.x.y` |
| `from agentic_coder_prototype.api.cli_bridge.app import app` | `from breadboard_engine.api.cli_bridge.app import app` |
| `python -m agentic_coder_prototype.api.cli_bridge.server` | `python -m breadboard_engine.api.cli_bridge.server` |

Both spellings resolve to the *same module objects* - `sys.modules` carries
both keys per module, so `isinstance`, monkeypatching, and identity checks
work across the boundary during the transition window.

## Legacy-import policy

`BREADBOARD_LEGACY_IMPORTS` controls shim behavior:

- `allow` (default): legacy imports resolve silently.
- `warn`: each legacy root import emits a `DeprecationWarning` once.
- `error`: legacy imports raise `LegacyImportError` naming the canonical
  package. CI lanes run first-party suites in this mode.

## Pickles and historical artifacts

Pickles and recorded artifacts that embed dotted `agentic_coder_prototype.*`
paths load correctly through the alias layer (version-aware resolver in
`breadboard_engine/compat/legacy_names.py`). Frozen evidence surfaces
(`docs/conformance/**`, `config/e4_lanes/**` inputs, archived support claims,
`docs/plans/phase_20_right_shape/SCOUT_FACTS.json`) intentionally retain the
historical spelling and are never rewritten; the resolver keeps them loadable.

## Removal criteria (tracked by the compat-removal issue)

The shim is removed when all of the following hold, measured by
`scripts/dev/audit_engine_rename.py`:

1. Zero `agentic_coder_prototype` occurrences in tracked first-party runtime
   code outside the frozen preserve-byte/compat-resolve classes (currently
   true: the audit census reports only intentional compat-namespace mentions).
2. One full release cycle has shipped with `BREADBOARD_LEGACY_IMPORTS=warn` as
   the default and no external consumer reports.
3. The frozen replay/e4 lanes have a pinned resolver version that no longer
   requires the import-time shim (resolver-only loading).

First artifact containing `breadboard_engine`: wheel
`breadboard_harness_cli-0.0.0` (sha256 `753d0e7a47a7...`) built from merge
`2b53f1816`; no semantic-versioned release exists yet - the merge SHA is the
release anchor to record in the first published changelog.
