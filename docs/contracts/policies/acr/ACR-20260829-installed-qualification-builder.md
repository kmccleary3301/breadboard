# ACR-20260829-installed-qualification-builder

- `acr_id`: `ACR-20260829-installed-qualification-builder`
- `title`: Installed production qualification builder
- `author`: Codex (RL integration closure)
- `date`: 2026-08-29
- `status`: implemented

## 1) Problem Statement

Production-composition qualification depended on a helper stored under `tests`, so a clean wheel could not construct the same authority graph, registry artifacts, canonical compiler vectors, TLS callback fixture, and secret files used by source-checkout tests and operational preflight scripts. The helper and every required resource need an installed product owner.

## 2) Scope and Surfaces

- Installed `breadboard.rl.harness.qualification` composition builder.
- Packaged canonical compiler vectors and qualification-only TLS resources.
- Exact production loader, compiler, registry, policy callback, verifier, secret, lifecycle, and cleanup inputs.
- Migration of qualification tests and F1 preflight composition construction to the installed module.
- Danger-zone: yes.
- Outside scope: operational certificate issuance, production credential storage, Docker readiness promotion, or use of the fixed qualification certificate outside isolated tests and canaries.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Test to product dependency removed: yes.
- Generalization impact: positive; source-checkout and installed qualification now use one canonical builder and one packaged resource set.
- Coupling risk: medium because generated artifact digests, registries, secret handles, TLS callback authority, and installed runtime measurements must agree.

## 4) Change Classification

- Classification: `additive` product surface plus clean migration of the deleted test helper.
- Compat reviewed: non-breaking for the public harness; internal qualification imports change intentionally.
- No alias or compatibility copy of the test-owned helper remains.

## 5) Evidence and Validation Plan

- Build a wheel and install it into a clean Python 3.11 virtual environment.
- Import `breadboard.rl.harness.qualification` from site-packages while the current directory is unrelated to the repository.
- Materialize the production composition and confirm its reference and profile.
- Ensure every resource opened by the installed builder is declared as package data.
- Preserve exact loader, compiler-vector, policy callback, verifier, lifecycle, cleanup, and unknown-name behavior.
- Generate secret values per materialization and write secret-bearing paths with owner-only permissions.
- Treat the packaged TLS key as a public qualification fixture; generated operational authorities and credentials remain outside this module.
- Run focused qualification, public lifecycle, headless, and F1 callsite checks.
- Require exact-head correctness and security review, the danger-zone ACR guard, and full CI before merge.

## 6) Rollout Plan

1. Merge the installed builder and package resources after exact-head review and CI.
2. Build the final engine wheel from the merged identity.
3. Use the installed builder for the unrelated-directory fake-policy journey and Linux qualification.
4. Extend qualification for the separately reviewed official Docker canary without weakening this fixture's authorities.

No release, signing, notarization, direct `main` push, operational TLS deployment, or readiness promotion is authorized by this record.

## 7) Rollback Plan

- Revert the installed builder and every migrated caller together.
- Do not restore a second divergent product implementation under `tests`.
- Reject qualification receipts whose builder or resource digest references the reverted identity.
- Keep installed qualification false until a replacement wheel passes the same clean-environment proof.

## 8) Approvals

- Qualification correctness reviewer: required on the exact PR head.
- Qualification security reviewer: required on the exact PR head.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge by itself.
