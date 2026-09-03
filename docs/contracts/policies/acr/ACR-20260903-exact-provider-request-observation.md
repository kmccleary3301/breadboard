# ACR-20260903-exact-provider-request-observation

- `acr_id`: `ACR-20260903-exact-provider-request-observation`
- `title`: Retain exact provider request identity and cache observations
- `author`: DSH donor implementation campaign
- `date`: 2026-09-03
- `status`: implemented

## 1) Problem Statement

A research run must identify the exact provider request sent after profile, environment, tool, and runtime-specific transforms. Cache telemetry must describe the completed persisted exchange without becoming request authority or recording an observation for a malformed or failed exchange.

## 2) Scope and Surfaces

- Kernel modules touched: provider profiles, OpenAI-compatible request conversion, provider invocation, system-prompt compilation, conductor request dispatch, and provider exchange retention.
- Extension modules touched: none.
- Contract surfaces touched: internal provider request reconstruction and cache-observation records only.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: public schemas, provider retry policy, credential selection, cache control policy, SDKs, TUIs, and new provider routes.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no. The request identity is computed from the runtime-specific final wire body instead of a shared approximation.
- Default endpoint semantics altered: no. Existing provider bodies are retained byte-for-byte; the change adds deterministic observation after the existing transforms.
- Coupling risk score: `high`. Incorrect placement would hash a pre-transform request or acknowledge cache data before the exchange is durable.

## 4) Change Classification

- Classification: `internal`
- Compatibility window: clean cutover; no public request or cache schema changes.
- Required schema/version bumps: none. Public exposure requires the W9 contract-amendment and generated-client gates.

## 5) Evidence and Validation Plan

- Required contract lane tests: profile-bound and unbound Chat/Responses conversion, OpenRouter GPT-5 Responses tool/media conversion, direct/retry/fallback/circuit invocation, prompt compilation, and exact reconstruction fixture.
- Required security tests: credentials and authorization metadata remain absent; malformed finish records and persistence failures produce no cache observation.
- Required evidence: focused combined test run, deterministic reconstruction fixture, exact-head independent review, and protected PR checks.
- Acceptance criteria:
  - Final request identity hashes the exact runtime-specific provider wire body after all transforms.
  - Profile-bound and unbound Chat/Responses routes reconstruct the same bytes that dispatch receives.
  - Cache observations are appended exactly once only after normalization, finish validation, and exchange persistence succeed.
  - Direct, retry, fallback, and circuit paths share the same observation rule.
  - Nested Completion and Responses cached-token fields normalize without widening provider request authority.
  - Secrets and credential material are not retained in request identity or cache observations.

## 6) Rollout Plan

- Rollout phases: merge internal owner behavior through protected checks; expose any public projection only through W9.
- Flags/toggles: none.
- Blast radius constraints: provider request construction and post-persistence observation only.
- Monitoring hooks: exact request fixture, provider runtime tests, provider invoker tests, and downstream installed composition proof.

## 7) Rollback Plan

- Trigger conditions: provider body drift, request-hash mismatch, duplicate or premature cache observation, credential leakage, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: no schema or data migration exists; restore the preceding package artifacts.
- Post-rollback verification: rerun exact request reconstruction and provider invoker/runtime tests.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks required.
- Final decision: protected branch checks and repository merge policy control merge.
