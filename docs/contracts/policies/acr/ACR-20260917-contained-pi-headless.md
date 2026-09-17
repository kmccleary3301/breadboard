# ACR-20260917-contained-pi-headless

- `acr_id`: `ACR-20260917-contained-pi-headless`
- `title`: Bind pinned Pi tools to contained installed headless execution
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-17
- `status`: implemented

## 1) Problem Statement

The installed headless producer could not execute the accepted Pi tool implementation inside the supplied Apptainer image. Its composition lacked the measured native binding, and the existing process backend needed explicit outer-containment admission. Installed execution also exposed defects in executable discovery, mode preservation, Linux sealing constants, nullable tool budgets, stdin handling, and final JSON publication.

The required outcome is a contained producer that invokes the pinned tools, preserves real workspace effects, publishes the native result and patch, and reports cancellation and cleanup truthfully. It is not official benchmark or private training acceptance.

## 2) Scope and Surfaces

- Composition and headless admission in `breadboard.rl.harness`.
- Native Pi dispatch through `pi_tools.mjs`, measured executable bindings and the existing Conductor tool port.
- Materialization, process execution, Docker snapshot acquisition and verifier snapshot handoff.
- Nullable compiled per-turn tool limits and recursive thawing at JSON publication.
- Contract surfaces: composition configuration, native tool bindings, process results, artifact modes and cleanup evidence.
- Kernel danger-zone change? yes
- Outside scope: private wrappers, trainers, reward logic and task providers; provider request-policy changes; new E4 profiles; a complete generic SIF qualification; public package release.

## 3) Coupling and Generalization Impact

No core-to-private-extension dependency is introduced. The native bridge resolves the pinned public Pi implementation through admitted materialized paths. It does not contain a supplier agent loop or obtain additional tool authority.

Outer Apptainer admission is explicit. It does not turn an arbitrary host process into a hardened sandbox, replace measurements with a caller flag, or relax the unchanged default headless rejection of uncontained trusted-process execution.

Mode preservation and stdin collection repair shared process behavior. Native response publication converts immutable runtime containers to JSON-compatible values without changing their nested content. Coupling risk is medium because admission, executable identity, snapshots and cleanup cross several public modules.

The accepted pi@0.57.1 and oh-my-pi@16.2.13 target assets remain unchanged. Runtime source and artifact identities change. No new parity or endpoint capability is inferred.

## 4) Change Classification

- Classification: `additive`

The change also corrects shared lifecycle behavior. Existing target identities and canonical episode envelopes are retained. The new containment selection and measured native bindings are explicit composition inputs. No compatibility alias or private fallback is added.

## 5) Evidence and Validation Plan

The source candidate is `c239f65adb6cc8b6fb9afe607c98682e228da450`, tree `e994fcc1ca2d8bfe118bf00be5a5ae7e26839ccf`. Independent authority and execution reviews accepted that exact candidate. The installed producer completed eight controlled turns using all seven accepted tools, produced a real patch, and passed controlled SIGINT exit-130 and authenticated external cleanup observations.

The retained launch bundle SHA-256 is `5d28f8459030a0066725941df6930257444c7c299dc213754c6afbc549a8c67f`. Its evidence is producer-scoped. A scripted endpoint does not establish live Qwen, official grading or private policy acceptance.

Protected product, compilation and other applicable CI checks passed at the source candidate. The danger-zone check correctly rejected the missing changed ACR. This record supplies the decision artifact without weakening that check. The exact documentation successor and all required checks must pass before merge. Existing installed proof remains tied to c239f65a; this documentation change does not claim a newly built runtime artifact.

## 6) Rollout Plan

Merge PR128 only after exact-head independent acceptance, required checks and resolution of public review threads. Preserve its ten-commit producer lineage. The provider-enabler work is a separate child packet and does not inherit lifecycle, native-response or official acceptance from this producer.

Final merged installed artifacts and generic SIF worker qualification remain separate campaign gates. Do not publish a package or extend supported-platform claims from this source merge.

## 7) Rollback Plan

Stop use of the affected producer if executable admission, containment, snapshot fidelity, publication or cleanup regresses. Revert the merge as one reviewed unit, preserve the affected artifact and evidence identities, and rebuild consumers only after the replacement passes its own installed proof. Do not fall back to uncontained host execution, mutable native binaries or fabricated cleanup success.

Rollback does not authorize deleting retained campaign or recipient custody.

## 8) Approvals

Kyle's accepted execution handoff authorizes public implementation, PRs and protected-gated merges. It does not grant official verifier, private policy or recipient acceptance.

`PiNativeAuthorityReview2` and `PiNativeExecutionReview2` accepted source c239f65a. Acceptance of this documentation successor remains pending. Final decision is withheld until its exact-head review and required CI pass.
