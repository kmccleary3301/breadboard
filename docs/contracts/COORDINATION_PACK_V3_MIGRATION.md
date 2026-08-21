# Coordination pack v3 migration

`bb.coordination_pack.v3` is the write shape for grouped coordination evidence.

Existing v1 reference-slice schemas stay valid for accepted evidence. Existing `bb.coordination_slice.v2` records stay valid and can be embedded in the v3 pack under `records.slices`.

New writers should emit the v3 pack when a run needs to carry dialect coverage, source slice references, or migration status in one envelope. Consumers should keep reading v1 and v2 inputs during replay and filtering. Promotion code should prefer v3 when both a pack and raw slices are present.

Deprecation markers live in the schema `$comment` and the example `migration.deprecation_notice`; no source evidence is rewritten during migration.
