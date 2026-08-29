# ACR-20260827-sdk-auth-hardening

- `acr_id`: `ACR-20260827-sdk-auth-hardening`
- `title`: SDK authentication, credential isolation, and configured transport hardening
- `author`: Codex (engine PR handoff)
- `date`: 2026-08-27
- `status`: implemented

## 1) Problem Statement

The accepted D6 client and provider-broker boundary needs race-safe authentication and secret-safe credential handling before broader public operations can consume it. The change touches authentication and CLI-bridge danger-zone paths; an explicit decision is required because concurrency or redaction errors can cause account lockout, stale refresh ownership, or credential disclosure.

## 2) Scope and Surfaces

- SDK login completion, refresh response typing, and configured fetch transport.
- Provider-broker refresh leases, terminal compare-and-set writes, OAuth/store behavior, and credential redaction.
- Generated SDK/OpenAPI changes and focused concurrency/security tests.
- Danger-zone: yes.
- Outside scope: new providers, public operation catalog migration, TUI package pinning, RL amendments, and release publication.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Provider-specific public coupling introduced: no; credentials remain behind provider-broker contracts.
- Default endpoint semantics altered: no intended breaking route change; response typing and failure safety are corrected.
- Coupling risk: high at concurrency and secret-handling seams.

## 4) Change Classification

- Classification: `internal`.
- Compat reviewed: non-breaking.
- Compatibility window: existing public operations remain; unsafe secret echoes and stale refresh-write behavior are not compatibility targets.
- Source provenance: exact original patches are replayed onto the ENG-01 ACR-bearing base; author identity/date and stable patch IDs remain recorded.

## 5) Evidence and Validation Plan

- G2 v12 canonical evidence manifest SHA-256: `425dc55a7d424f7ad7c7f39bfe69eac6e52a9ea295b93a454aca01ab99c33b46`.
- Required gates: focused provider/auth/security tests, SDK build/tests, configured-fetch stability, clean-consumer install, deterministic package output, danger-zone ACR guard, and fresh exact-head security/spec review.
- Acceptance criteria:
  - login completion and refresh terminal writes remain race-safe;
  - credential keys, values, identity fields, scalar echoes, aliases, and alternate representations do not leak;
  - configured transport remains in use;
  - generated SDK/OpenAPI sources remain current.

## 6) Rollout Plan

1. Merge only after ENG-01 and after required CI/review pass.
2. Re-ground this branch on the merged ENG-01 head without changing runtime patches.
3. Merge the typed public-operation seam PR afterward.

No release, signing, notarization, or direct `main` push is authorized by this record.

## 7) Rollback Plan

- Revert the public-operation seam PR first, then this PR.
- Re-run auth contention, credential-redaction, SDK transport, and clean-consumer checks after rollback.
- Do not restore any known secret-echo compatibility path.

## 8) Approvals

- Kernel/security reviewer: required on the exact PR head.
- Contracts reviewer: required on the exact PR head.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge.
