# BreadBoard Contract Conventions

Generated for BB-ER Wave 0 on 2026-07-03.

## Canonical schema identity

New kernel schemas use absolute `$id` values under `https://breadboard.dev/contracts/kernel/schemas/` and keep the checked-in filename equal to the terminal `$id` path segment.

## Primitive boundary

A primitive is a schema-backed record finalized at the `finalize_record` boundary.

Runtime/config primitives, E4 evidence-pack records, program-management records, and registries may share the `bb.*` contract namespace, but docs must name the sub-pack. Program-management records such as `bb.er.progress.v1` live in the `program` pack, not in the runtime primitive language.

Schema pack ownership is recorded in `contracts/kernel/packs.v1.json`. Pack ownership is total and exclusive: every top-level `contracts/kernel/schemas/*.schema.json` file appears in exactly one pack, while payload adjunct schemas under `contracts/kernel/schemas/payloads/` remain implicitly kernel-owned.

## Version field

New record schemas expose `schema_version` with a `const` value matching the contract name, for example `bb.e4.support_claim.v2`. Shared `$defs` schemas such as `bb.kernel.common.v1` and `bb.e4.common.v1` may define no root record type; usage examples exercise their definitions rather than a finalized record.

## Hash and reference strings

New contracts use lowercase SHA-256 strings with the `sha256:` prefix. Content-bearing references use `path#sha256:<hex>`. Ledger row references use `path#row_id#sha256:<hex>`.

`bb.effective_config_graph.v1.graph_hash` is a frozen legacy preimage: canonical JSON with `indent=2`, `separators=(",", ": ")`, a trailing newline, and `graph_hash` set to `null` before hashing. `finalize_effective_config_graph` preserves that preimage for accepted E4 evidence compatibility while still routing validation through `finalize_record`.

### Stable-subset catalog binding

Support claims bind to `docs/conformance/e4_artifact_catalog.json` through stable-subset catalog bindings, never through a whole-file catalog hash. `support_claim.v2` uses `catalog_binding.catalog_hash` set to the catalog's `integrity.stable_entries_hash`: `sha256:` over canonical JSON bytes for catalog entries classified as stable by explicit role-keyed rules (lane role suffixes and curated `e4_static:*` role IDs). `support_claim.v3` and `v4` use segment bindings: `segment_id`, `segment_hash`, and `shared_segment_hash`; `v4` also carries `catalog_revision`. This avoids a no-fixed-point claim<->catalog cycle: a whole-file catalog hash would include the claim hash, while the claim would also embed that catalog hash.

The catalog `revision` is versioned by the same role-keyed stable evidence subset and does not change for claim-derived entry churn.

## Time

New timestamps use RFC 3339 date-time strings and the common `timestamp_utc` definition. Fixtures use `2026-07-03T00:00:00Z` for deterministic examples.

## Object strictness

New schemas set `additionalProperties: false` for record objects unless a metadata or extension bag is deliberately declared.

## V2 kernel record dialect

- snake_case everywhere; `schema_version` const; root `"additionalProperties": false`.
- Shared shapes via $ref to bb.kernel.common.v1.schema.json#/$defs/{digest_sha256,hash_ref,row_hash_ref,
  timestamp_utc,identifier,actor_ref,visibility,visibility_paths,evidence_refs,metadata,argv_command}.
- `metadata` ($ref common) is the ONLY open extension point on a closed record.
- IDs (event_id, run_id, session_id, call_id, ...): `{"type": "string", "minLength": 1}` (do not force
  lowercase identifier on runtime IDs).
- Every nested object literal is closed (`additionalProperties: false`) unless it IS the metadata ref or
  an intrinsically open payload explicitly named by the spec.
- $id URL: https://breadboard.dev/contracts/kernel/schemas/<filename>
- Each v2 or later file carries "$comment": "Supersedes <prior id>. <prior version> is frozen for accepted evidence. Migration map: docs/contracts/CONTRACT_CONVENTIONS.md#contract-migrations."

### Registry convention

Identifier sets that are expected to grow across harnesses live in `contracts/kernel/registries/*.v1.json` under the `bb.registry.v1` shape. Schemas may keep string patterns for local shape, but membership is enforced by the generator or gate that consumes the record. Adding a harness family, event kind, or contract family is a registry data change, not a schema enum edit.

`bb.e4.common.v1` is the E4-pack shared definitions schema. It is not finalized as a record; E4 contracts reference its `$defs` for exact scope tuples, artifact references, catalog snapshot bindings, typed assertions, and captured-target provenance.

### Contract migrations

### v1-to-v2 runtime records

| v1 schema | v2 schema | status |
| --- | --- | --- |
| `bb.kernel_event.v1` | `bb.kernel_event.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.session_transcript.v1` | `bb.session_transcript.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.tool_call.v1` | `bb.tool_call.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.tool_execution_outcome.v1` | `bb.tool_execution_outcome.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.tool_model_render.v1` | `bb.tool_model_render.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.tool_spec.v1` | `bb.tool_spec.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.coordination_delegated_verification_reference_slice.v1` | `bb.coordination_slice.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.coordination_intervention_reference_slice.v1` | `bb.coordination_slice.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.coordination_longrun_reference_slice.v1` | `bb.coordination_slice.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.coordination_multi_worker_reference_slice.v1` | `bb.coordination_slice.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |
| `bb.coordination_reference_slice.v1` | `bb.coordination_slice.v2` | v1 frozen (accepted evidence); v2 canonical for new producers |

### E4 evidence-pack migrations

| prior schema | next schema | status |
| --- | --- | --- |
| `bb.e4.support_claim.v2` | `bb.e4.support_claim.v3` | v2 frozen for accepted claims; v3 uses registry-shaped `target_family` and segment catalog bindings for new claims after WS-F production flips |
| `bb.e4.support_claim.v3` | `bb.e4.support_claim.v4` | v3 frozen until sanctioned regeneration; v4 stores exact-scope identity only under `scope`, rejects root duplicates, uses registry-shaped `excluded_families`, and keeps segment catalog bindings plus `catalog_revision` |
| `bb.e4.lane_def.v1` | `bb.e4.lane_def.v2` | v1 remains loadable through normalized compatibility views; v2 lifts `run`, `provenance`, and `acceptance` out of metadata and adds adapter capture plus typed comparison assertions |
| `bb.e4.artifact_catalog.v1` | `bb.e4.artifact_catalog.v2` | v1 accepted catalog remains readable; v2 is sharded by segment and content-addressed binding |
| `bb.e4.lane_inventory.v1` | `bb.e4.lane_inventory.v2` | v1 frozen; v2 uses registry-shaped `target_family` and optional `lane_def_ref` |
| `bb.e4.target_coverage.v1` | `bb.e4.target_coverage.v2` | v1 frozen; v2 uses registry-shaped `target_family` |

#### Visibility mapping

| v1 visibility | model_visible | provider_visible | host_visible |
| --- | --- | --- | --- |
| `model` | true | true | true |
| `host` | false | false | true |
| `audit` | false | false | false |

Derivation rules: transcript-visible iff model_visible; host surfaces iff host_visible; audit-only = all false (event log/evidence retention only).

#### Actor mapping

| v1 kernel_event actor | common actor_ref.actor_kind |
| --- | --- |
| `engine` | `system` |
| `provider` | `provider` |
| `tool` | `service` |
| `subagent` | `subagent` |
| `human` | `user` |
| `service` | `service` |

Payload discrimination convention: closed envelopes carry `payload`/`content` plus a REQUIRED sibling `payload_schema_version`/`content_schema_version` (`string|null`) naming the `bb.*` schema the open payload conforms to; consumers resolve via the schema registry; null means free-form.

Migration law: published v1 referenced by accepted evidence never changes incompatibly; enum edits/renames/required-additions REQUIRE a v2 file; additive OPTIONAL fields allowed on v1.

Kind taxonomy rule: kernel event `kind` uses dotted lowercase families. The single source of truth is the `kernel_event_kinds` registry under `contracts/kernel/registries/`; underscore kinds are the frozen v1 dialect.

## Metadata

New E4 contracts use `bb.kernel.common.v1#/$defs/metadata` for open metadata instead of creating schema-specific arbitrary bags.

## Visibility vocabulary

New contracts use the canonical common visibility triple (`model_visible`, `provider_visible`, `host_visible`) plus `redaction_state`; older enum dialects are frozen legacy and must not be copied into new fields.

## Legacy allowlist

Legacy v1 contracts that predate these conventions are listed in `contract_conventions_allowlist.json`. The allowlist is explicit technical debt, not a rule override for new schemas.

Current allowlist entries: 33.
