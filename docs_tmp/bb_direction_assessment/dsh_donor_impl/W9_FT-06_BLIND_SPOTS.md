# W9.1 / FT-06 blind spots

`interface_inventory` is a deterministic structural report, not a source of truth or a merge gate. The following limits are deliberate:

1. Dynamic imports, runtime entry-point lookup, plugin discovery, and reflection are not proven by the static scan.
2. Owner attribution is limited to registry consumer/implementation references plus top-level Python declarations in those files and named campaign owner paths. A symbol absent from a parseable source file is reported as an unresolved owner path, not inferred.
3. Schema fields are top-level `properties` and `required` declarations. Fields introduced through `$ref`, conditional schemas, generated code, or runtime validation are not expanded.
4. Event coverage reports authoritative kernel registry entries and statically declared engine `EventType`/`EventKind` values. It does not claim runtime emission, delivery, or fail-closed behavior.
5. TUI consumers are quoted event/schema literal references and exact SDK symbol references in `tui_skeleton/src` and the paired repository's `packages/{coding-agent,agent,tui}/src`; dynamic construction, `.omp` command/skill content, and vendored/build/generated trees are excluded.
6. SDK exports are the Python package `__all__`, the TypeScript `sdk/ts/src/index.ts` re-exports, and generated public-surface manifest operation IDs. Package-manager export maps and dynamic re-exports are not evaluated.
7. Compatibility surfaces are registry lifecycle/tier rows, public operation catalogs, path-scoped compatibility signals, and exact `durable_reconfigure` line references. This does not prove that every old consumer has migrated or that deletion is safe.
8. Installed-product behavior and E4 conformance are outside this structural entry point; they require their owning focused or integrated proof.

The excluded paths and static method are emitted in the JSON's method/consumer-root fields so a later run can be diffed without turning this artifact into a second authority.
