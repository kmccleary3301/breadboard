# Kernel Semantics Roadmap

This directory is reserved for the semantic dossiers that will define each shared kernel contract family in human-readable form.

Each dossier should answer:

- what the contract is for
- what is and is not in scope
- what must be identical across conforming engines
- what may remain engine-private
- what evidence and fixtures validate the contract
- what known ambiguity still exists

---

## Intended dossier set

1. `run_request_and_context_v1.md`
2. `backbone_api_v1.md`
3. `kernel_event_v1.md`
4. `session_transcript_v1.md`
5. `provider_exchange_v1.md`
6. `support_claim_and_projection_profile_v1.md`
7. `workspace_layer_v1.md`
8. `host_kit_v1.md`
9. `ai_sdk_transport_adapter_v1.md`
10. `thin_host_dx_v1.md`
11. `tool_lifecycle_and_render_v1.md`
12. `permission_and_guardrails_v1.md`
13. `middleware_lifecycle_v1.md`
14. `task_and_subagent_v1.md`
15. `checkpoint_and_longrun_v1.md`
16. `replay_session_v1.md`
17. `conformance_evidence_v1.md`
18. `import_export_session_v1.md`
19. `projection_adapter_boundaries_v1.md`
20. `terminal_sessions_v1.md`
21. `tool_bindings_and_effective_surfaces_v1.md`

---

## Editorial rules

Each dossier should clearly mark:

- shared semantics
- engine-private implementation room
- projection-only concerns
- hybrid/delegated service concerns

Each dossier should also identify:

- current owners in Python
- planned owners/bindings in TypeScript
- current tests/evidence
- remaining ambiguity

---

## Relationship to machine-readable contracts

Human-readable dossiers here should be paired with machine-readable artifacts under:

- `contracts/kernel/`

The dossier explains the semantics. The schema validates the shape. The fixture bundle proves the claim.

---

## First tranche status

The first drafted dossiers are:

- `kernel_event_v1.md`
- `session_transcript_v1.md`
- `tool_lifecycle_and_render_v1.md`

The second tranche now adds:

- `run_request_and_context_v1.md`
- `provider_exchange_v1.md`
- `permission_and_guardrails_v1.md`
- `middleware_lifecycle_v1.md`
- `task_and_subagent_v1.md`
- `checkpoint_and_longrun_v1.md`
- `replay_session_v1.md`
- `conformance_evidence_v1.md`

The V2 tranche now adds:

- `execution_capability_and_placement_v1.md`
- `sandbox_envelopes_v1.md`
- `distributed_task_descriptor_v1.md`
- `transcript_continuation_patch_v1.md`
- `unsupported_case_v1.md`
- `execution_driver_policy_and_evidence_v1.md`

The first Phase 14 tranche now adds:

- `terminal_sessions_v1.md`
- `tool_bindings_and_effective_surfaces_v1.md`

The coordination follow-on tranche now adds:

- `coordination_signals_v1.md`
- `coordination_reviews_v1.md`
- `coordination_directives_v1.md`
- `coordination_intervention_v1.md`
- `coordination_verification_v1.md`

The first V3 tranche now adds:

- `backbone_api_v1.md`
- `support_claim_and_projection_profile_v1.md`
- `workspace_layer_v1.md`
- `host_kit_v1.md`
- `ai_sdk_transport_adapter_v1.md`

## Status snapshot

- drafted and scaffolded:
  - `run_request_and_context_v1.md`
  - `backbone_api_v1.md`
  - `kernel_event_v1.md`
  - `session_transcript_v1.md`
  - `provider_exchange_v1.md`
  - `support_claim_and_projection_profile_v1.md`
  - `workspace_layer_v1.md`
  - `host_kit_v1.md`
- `ai_sdk_transport_adapter_v1.md`
- `thin_host_dx_v1.md`
- `tool_lifecycle_and_render_v1.md`
  - `permission_and_guardrails_v1.md`
  - `middleware_lifecycle_v1.md`
  - `task_and_subagent_v1.md`
  - `checkpoint_and_longrun_v1.md`
  - `replay_session_v1.md`
  - `conformance_evidence_v1.md`
  - `execution_capability_and_placement_v1.md`
  - `sandbox_envelopes_v1.md`
  - `distributed_task_descriptor_v1.md`
  - `transcript_continuation_patch_v1.md`
  - `unsupported_case_v1.md`
  - `terminal_sessions_v1.md`
  - `tool_bindings_and_effective_surfaces_v1.md`
- intentionally deferred to a later tranche:
  - `import_export_session_v1.md`
  - `projection_adapter_boundaries_v1.md`
