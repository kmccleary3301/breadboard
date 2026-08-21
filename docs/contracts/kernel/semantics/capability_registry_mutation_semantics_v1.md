# Capability registry mutation semantics v1

## Purpose

`bb.capability_registry.v1` is the contract for a resolved capability snapshot. The E4 hardening pass adds an optional mutation log to the same contract so a snapshot can explain the deltas that produced it. A registry with no `mutation_log` remains a valid snapshot.

The mutation log is infrastructure evidence. It records registry-shape semantics only; it does not create a new primitive family or a new support claim.

## Snapshot and delta fields

A registry snapshot may now include:

- `registry_revision`: a monotonically increasing revision for the named registry.
- `basis_registry_ref`: the previous snapshot or delta basis, when the snapshot was derived from one.
- `mutation_log`: ordered `mutation_operation` entries.

A mutation operation records the operation, target, phase, source, outcome, and optional before/after refs. The final `capabilities` array remains the resolved snapshot. Consumers that only need the current surface can ignore `mutation_log`; consumers that need provenance can replay or inspect it.

## Operation vocabulary

The operation vocabulary covers observed registry behavior without adding a command, hook, plugin, or provider primitive family:

- discovery and registration: `discover`, `register`
- activation state: `activate`, `deactivate`, `enable`, `disable`
- conflict resolution: `shadow`, `override`, `replace`, `remove`, `source_handoff`, `conflict_rejected`
- execution-time effects: `block`, `modify_result`, `inject_context`, `append_state`

Targets use existing capability types where possible (`tool`, `model`, `extension_hook`, `resource_resolver`, `runtime`, `permission`, `storage`) plus registry-scoped nouns (`registry`, `provider`, `slash_command`, `extension_module`, `plugin`). These target nouns identify what changed in the registry; they are not new top-level contracts.

## Worked examples from the Oh-My-Pi source freeze

The examples below are grounded in `docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/`, frozen from `@oh-my-pi/pi-coding-agent@16.2.13`.

### Hook discovery

`packages/coding-agent/src/capability/hook.ts` keys a hook as `type:tool:name` and derives extension ids as `hook:type:tool:name`. A pre-Read hook discovered from `.claude/hooks/pre/read-policy.ts` can be represented as an `extension_hook` capability in the snapshot and a `discover` mutation:

```json
{
  "mutation_id": "mut_001_discover_hook_pre_read",
  "operation": "discover",
  "target_type": "extension_hook",
  "target_ref": "hook:pre:Read:policy",
  "phase": "discovery",
  "source": {
    "source_type": "runtime_probe",
    "source_ref": "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/capability/hook.ts:1-40"
  },
  "outcome": "applied",
  "affected_capability_ids": ["cap_omp_hook_pre_read_policy"]
}
```

The capability remains host-only if it intercepts host execution rather than exposing a model-callable tool.

### Custom command override

`packages/coding-agent/src/extensibility/custom-commands/loader.ts` loads bundled commands first, then allows user or project commands to replace a bundled command with the same name. That is an `override` on target type `slash_command`:

```json
{
  "mutation_id": "mut_004_override_bundled_green_command",
  "operation": "override",
  "target_type": "slash_command",
  "target_ref": "slash-command:green",
  "phase": "resolution",
  "source": {
    "source_type": "runtime_probe",
    "source_ref": "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/extensibility/custom-commands/loader.ts:1-240"
  },
  "outcome": "applied",
  "before_ref": "bundled:slash-command:green",
  "after_ref": "project:slash-command:green"
}
```

A conflict between two non-bundled commands is recorded as `conflict_rejected` with outcome `rejected`.

### Custom tool conflict

`packages/coding-agent/src/extensibility/custom-tools/loader.ts` seeds its seen-name set with built-in tool names. A custom tool named `edit` collides with the built-in tool and is rejected before it becomes a capability:

```json
{
  "mutation_id": "mut_003_reject_tool_edit_conflict",
  "operation": "conflict_rejected",
  "target_type": "tool",
  "target_ref": "tool:edit",
  "phase": "resolution",
  "source": {
    "source_type": "runtime_probe",
    "source_ref": "docs_tmp/phase_15/source_freezes/oh_my_pi_main_latest/packages/coding-agent/src/extensibility/custom-tools/loader.ts:1-260"
  },
  "outcome": "rejected",
  "affected_capability_ids": []
}
```

`affected_capability_ids` is empty because the rejected tool never enters the resolved capability snapshot.

### Provider and extension mutations

The same log shape covers provider and extension registry behavior in the source freeze:

- Extension registered tools are inserted into the session tool registry by name; a same-name tool is an `override` on `tool` during `activation`.
- Cursor-mode setup removes the `edit` tool from the active registry; that is a `remove` on `tool:edit` with outcome `removed`.
- Extension provider registration with a `models` list replaces current runtime overlays for that provider; that is a `replace` on `provider`.
- Provider transport-only registration updates the runtime provider override; that is an `override` on `provider`.
- Disabled extensions add ids to the disabled-extension set; re-enabling removes them. These are `disable` and `enable` operations on `extension_module` or `plugin` targets.

## Shared semantics

- `capabilities` is the resolved snapshot after all listed operations.
- `mutation_log` is optional and ordered. The schema validates each entry shape, not replay equivalence.
- `basis_registry_ref` and per-operation `basis_ref` identify the prior snapshot or registry revision when available.
- `before_ref` and `after_ref` identify registry-local objects, source objects, or capability ids. They are references, not embedded copies.
- `details` is source-specific. Cross-engine consumers should rely on `operation`, `target_type`, `target_ref`, `phase`, `source`, `outcome`, and `affected_capability_ids` first.

## Fixture expectations

The fixture `conformance/engine_fixtures/capability_registry/mutation_fixture.json` validates a registry with a mutation log. The invalid fixture `invalid_mutation_operation_fixture.json` checks that an operation outside the schema vocabulary is rejected.

Existing minimal fixtures and examples stay valid because all new fields are optional.
