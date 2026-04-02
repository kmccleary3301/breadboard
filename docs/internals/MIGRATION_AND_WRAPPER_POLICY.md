# Migration And Wrapper Policy

This note defines how BreadBoard uses compatibility wrappers during repo cleanup.

The goal is simple:

- improve naming and package topology aggressively
- preserve behavior and import compatibility while the cleanup is in flight
- avoid letting wrappers become silent permanent debris

## When wrappers are allowed

Wrappers are allowed when a move improves the canonical structure and at least one of these is true:

- tracked code still imports the old path
- tests or monkeypatches target the old module path directly
- scripts or config still depend on the old path
- we need a bounded migration window before deleting the old name

Wrappers are not a substitute for choosing a canonical home. Every wrapper must point at one clear destination.

## Wrapper classes

BreadBoard uses four wrapper categories.

### Import-path compatibility wrapper

Use this when the old import path should continue to resolve to the new canonical module during migration.

Typical form:

- true module alias via `sys.modules[__name__] = canonical_module`
- used when identity matters for monkeypatching, test probes, or module-level state

### Legacy namespace shim

Use this when a historical or confusing name still needs to exist for a while, but the real implementation has been quarantined elsewhere.

### Deprecated alias

Use this when the old name is still acceptable short term, but the new name is the only intended canonical one.

### Migration-only wrapper

Use this when the old path should disappear after one cleanup cycle once internal callers have been updated and validation has passed.

## Lazy vs eager behavior

Choose wrapper mechanics intentionally.

Use a true alias wrapper when:

- tests monkeypatch module globals on the old path
- callers depend on module identity
- the module owns mutable runtime state

Use a lighter re-export wrapper only when:

- callers only import symbols
- module identity does not matter
- private underscore exports are not part of compatibility expectations

## Private and underscore compatibility

If tracked callers or tests import underscore-prefixed names from the old path, the wrapper must preserve that behavior explicitly.

Do not assume wildcard imports are enough. If private compatibility matters:

- alias the whole module, or
- explicitly re-export the required underscore names

## Expected wrapper lifetime

Wrappers are transitional by default.

## Retirement states

Every wrapper family should be tracked in one of these states:

### `active wrapper`

- old path still exists for compatibility
- canonical path exists
- migration is still in progress

### `canonicalized, retirement-ready`

- docs and tracked code prefer the canonical path
- focused compatibility validation is green
- the old path still exists only to protect a bounded migration window

### `deprecated with removal target`

- the old path emits an explicit deprecation signal or is otherwise clearly in
  retirement
- a removal checkpoint or next cleanup gate is recorded

### `retired`

- the old wrapper path is removed or reduced to a documented hard break after
  the migration policy has been satisfied

Promotion between states must be evidence-based, not rhetorical.

They should remain only until all of the following are true:

- tracked internal imports prefer the canonical path
- focused compatibility tests are green
- docs and scripts no longer present the old path as primary
- the old path is no longer needed for an active migration window

To promote a wrapper family into retirement-ready, all of the following must be
true:

- canonical docs and examples lead with the new path
- tracked internal callers mostly use the canonical path
- the wrapper validation matrix is green for that slice
- the old path is no longer the default path in onboarding or operator docs

To promote a wrapper family into deprecated-with-removal-target, all of the
following must be true:

- it already qualifies as retirement-ready
- the remaining need for the old path is narrow and explicitly understood
- there is a recorded removal trigger or next checkpoint

Retirement triggers are recorded per moved slice in:

- [BREADBOARD_WRAPPER_LEDGER_2026-04-02.md](/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/repo_hygiene/BREADBOARD_WRAPPER_LEDGER_2026-04-02.md)

Validation coverage is recorded in:

- [BREADBOARD_WRAPPER_VALIDATION_MATRIX_2026-04-02.md](/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/repo_hygiene/BREADBOARD_WRAPPER_VALIDATION_MATRIX_2026-04-02.md)

The current root-authority state is recorded in:

- [BREADBOARD_ROOT_AUTHORITY_INVENTORY_2026-04-02.md](/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/repo_hygiene/BREADBOARD_ROOT_AUTHORITY_INVENTORY_2026-04-02.md)
