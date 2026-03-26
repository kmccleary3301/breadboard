# DARWIN Policy Registry V0

This registry freezes the initial versioned policy objects DARWIN may reference in Phase-1.

## Policy classes

- topology families
- memory policies
- budget classes
- promotion policies
- safety policies

## Versioning rules

- IDs are immutable once referenced by claim-bearing artifacts.
- New semantics require a new versioned `policy_bundle_id`.
- Parent references remain explicit.
- Experimental bundles may exist in `draft` or `shadow` state, but only `approved` bundles are claim-eligible.

The machine-readable source of truth is `registries/policy_registry_v0.json`.
