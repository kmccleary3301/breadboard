# ACR-20260901-e4-product-ownership

- `acr_id`: `ACR-20260901-e4-product-ownership`
- `title`: Keep E4 evidence and compiler admission under product ownership
- `author`: Codex (engine PR campaign)
- `date`: 2026-09-01
- `updated`: 2026-09-15
- `status`: implemented

## 1) Problem Statement

Active E4 registry entries and product evidence flows load runtime implementations from `scripts.*`. That reverses the intended dependency direction: product behavior depends on command wrappers, workspace evidence can resolve at import time, and adapter ownership is split between runtime and compatibility paths. The cutover touches kernel danger-zone registry, lock, evidence, and CLI surfaces and therefore requires an explicit architecture and compatibility decision.

## 2) Scope and Surfaces

- Product owners under `breadboard.product.evidence.e4`, including session replay, primitive projection, lane inventory, accepted-artifact materialization, support claims, catalog references, and active adapters.
- Active implementation paths in `contracts/kernel/registries/e4_adapters.v1.json`.
- Regenerated `oh_my_pi_p6_6_task_job_subagent` lane lock.
- Thin compatibility commands and imports under `scripts/e4_parity` and `scripts/replay_session_from_records.py`.
- Product E4 CLI wiring and focused ownership, registry, compiler-capture, and lane tests.
- Danger-zone: yes.
- Outside scope: changing accepted evidence bytes, fabricating absent governed evidence, widening adapter activation, or changing public SDK/session contracts.

## 3) Coupling and Generalization Impact

- Dependency direction after cutover: compatibility scripts depend on product owners; product owners do not depend on script implementations.
- Approved exception: lazy promoted-binding refresh imports in `breadboard.product.evidence.e4.run_lane` remain explicit compatibility integration points.
- Workspace evidence resolution is lazy through the selected checkout root and fails closed when external evidence is unavailable.
- Active adapter lookup rejects inactive and deprecated registry entries.
- Provider- or target-specific behavior remains behind named E4 adapter interfaces; no new dependency enters the kernel or public SDK boundary.
- Coupling risk: medium. The change moves substantial implementation ownership but retains adapter inputs, outputs, registry identities, and compatibility commands.

## 4) Change Classification

- Classification: `breaking`.
- The original ownership move was internal. The current classification includes the compiler 1.2 identity migration in Section 11; Sections 1–10 retain their original tranche scope.
- Compatibility window: command modules and import paths remain thin forwarders; direct Pi P5 adapter `--help` behavior remains supported.
- Clean cutover: active registry implementations point directly to product owners. Script modules must not remain alternate owners.
- No intentional public wire-format, accepted-evidence, or lane-result change.

## 5) Evidence and Validation Plan

Required gates:

- Import and resolve every active registry implementation in an isolated subprocess without relying on import-time workspace evidence.
- Verify inactive and deprecated entries cannot be selected for capture.
- Compare moved declarations against their source modules so functions, classes, and constants are not lost.
- Exercise compatibility imports and direct command help paths.
- Regenerate and byte-check the affected lane lock with the product compiler.
- Regenerate the script index after ownership moves.
- Run the focused product-owner and Pi P5 capture tests.
- Run the E4 battery, fixed-point clean-checkout gates, danger-zone ACR guard, and fresh exact-head independent review.

Current focused evidence:

- Product-owner and Pi P5 gate: 20 passed after the protected-main restack.
- Product lane-lock compiler check: `matches=True`.
- Known broad compiler-capture failures are limited to governed evidence files absent from this checkout; they must not be suppressed or replaced with fabricated fixtures.

## 6) Rollout Plan

1. Merge only after the corrected head descends from protected `main`, CI is green, and exact-head review reports no P0-P2 findings.
2. Preserve thin script commands for operators while treating product modules as the sole implementation owners.
3. Monitor registry import, fixed-point, and E4 battery gates on the merged commit.
4. Do not promote, rewrite, or regenerate accepted evidence as part of this ownership change.

## 7) Rollback Plan

- Revert the PR as one ownership tranche, restoring the prior registry, lock, product, and compatibility-module state together.
- Re-run active-registry import, lane-lock, E4 battery, and fixed-point checks after rollback.
- Preserve accepted evidence and external workspace artifacts unchanged; rollback must not regenerate them.

## 8) Approvals

- Kernel/danger-zone review: required on the exact PR head.
- Independent ownership review: required on the exact PR head.
- Owner approval: granted for the campaign; merge still requires all exact-head CI and review gates to be green.

## 9) Checkout-boundary correction — 2026-09-05

E4 path resolution now uses the declared workspace root rather than assuming
every engine checkout is its sibling. Candidate capture and regeneration use
the same product-owned resolver. C4 scratch capture stays inside its candidate
checkout instead of relying on an ambient repository registry.

Candidate validation materializes the canonical comparator registry inside the
validation checkout and rejects stale or symlinked registry destinations.
Progress references resolve at the checker boundary and reject traversal,
escaped symlinks, and derivative evidence aliases under pin-policy v2.
Regeneration watches the declared workspace, while the read-only explain command
does not require external evidence configuration.

Promotion uses the same candidate-to-accepted mapping for literal and glob
writes. Progress evidence retains its workspace-relative contract, including
archived checkout paths; a checkout-relative decoy cannot replace those bytes.
The shared resolver's explicit workspace namespace validates that authority and
rejects absolute paths, traversal, and symlink escape without probing other roots.
Strict pin policy matches derivative roots through any workspace prefix, for
both the supplied reference and its resolved target. Archive names do not turn
generated reports into primary evidence.

The correction changes code and its boundary tests, not accepted evidence.
Missing fixtures are recovered from the governed input bundle, the pinned source
archive, and the retained integration checkout. Existing accepted bytes are not
overwritten or invented. Full E4 and fixed-point CI remain the landing gates;
missing or stale historical custody is classified separately from product reds.

## 10) Promotion destination-parent boundary correction — 2026-09-05

Promotion keeps candidate paths lexical so legitimate candidate symlinks can
be dereferenced and accepted leaf symlinks can be replaced. Before staging any
write, candidate-to-accepted mapping resolves the destination parent and requires
containment within the intended checkout or declared workspace root, before
discovery can traverse it. Each promotion and rollback operation rechecks that
containment before mutating its destination.
Rollback restores dangling leaf symlinks too: their directory entries are accepted
state even when their targets do not exist.

A nested destination-parent symlink that resolves outside its authorized root
therefore fails closed before promotion side effects. This is a bounded
non-hostile-filesystem guarantee; check-then-use validation does not claim
race-free protection against an active hostile path swap.

## 11) Compiler-owned target admission — 2026-09-15

### Decision and scope

Keep target lowering in the existing compiler, not a second supplier-specific
compiler or an opaque supplier loop. The approved compatibility cutover adds
closed v2 target/configuration contracts and typed headless materialization
inputs. The unchanged v1 Pi `0.57.1` and Oh My Pi `16.2.13` resources retain their
accepted bytes and behavior. New supplier versions require sibling identities
and their own source, capture, replay and live evidence.

The compiler owns ordered input identity, lowering, source closure and compiled
locks. Omission remains distinct from an explicit empty string or null;
source-owned rendering defaults do not rewrite the supplied-input frame.
Unsupported renderers, semantic policies and runtime capabilities fail
admission rather than acquire a fallback implementation.

The existing lock owner exports `copy_harness_json`; compiler consumers use that
operation without a private alias or a second copy implementation. Installed
packaging includes the public RL harness namespace and its required resources,
not research RL siblings. Wheel provenance includes the actual installed source
boundary. False EvoLake live entries are retired without rewriting historical
records or claiming that a bootstrap result proves replay.

### Credential and compatibility boundaries

The real headless entrypoint verifies the target and pinned compiler inputs
before provider or composition credential access. It uses the same explicit
composition-reference bytes for preflight and composition loading, then checks
the pinned manifest set before starting the service. A rejected target must not
depend on readable credentials.

Compiler 1.2 changes implementation/provenance identities. Historical fixture
inputs remain frozen; implementation-independent comparisons do not assert that
old and new compiler identities are equal. The existing Phase20 amendment and
content pins record the authorized schema/configuration scope without changing
the historical freeze baseline.

### Evidence, rollout and rollback

Candidate `72ed11162cd8316a02a2b8b1f4c0c598fd992897` passed 1,057 focused
compiler/target/product-spine/headless tests with ten skips, three clean
built-artifact tests, and 49 SDK tests. Source and installed manifest, bundle,
closure and lock bytes match. Actual installed entrypoint probes reject input
and version mismatches before credential activity; admitted pins reach only a
guarded credential boundary. No credential contents or runtime were consumed.
These are compiler/installed-boundary proofs, not supplier or live qualification.

The independent Standards and Spec reviewers found no blocking source defect
at that candidate. [PR 125](https://github.com/kmccleary3301/breadboard/pull/125)
passed the protected Linux product-spine lanes; its architecture guard correctly
required this changed decision record. The amended head still requires current
CI and a fresh exact-head attestation. No prior verdict is transferred silently.

Kyle approved the public compiler cutover and protected promotion route on
2026-09-14, then one bounded credential repair and additional review round on
2026-09-15. This record adds no review rounds, private implementation authority,
supplier/runtime acceptance, package-publication authority or custody cleanup.
The existing broader lifecycle and fixture-scanner residuals remain assigned
to their owning packets and block any claim that depends on them.

Rollback selects the prior intact compiler, engine distribution, schemas,
resources and locks as one compatible tuple. Do not combine old compiler bytes
with newly generated locks or delete accepted targets, failed candidates,
review records or custody. Later profile promotion and final merged installation
retain their separate required gates.

## 12) Target parsing and installed-contract correction, 2026-09-15

PR125's later review and E4 Battery found gaps beyond the earlier credential
repair. Kyle approved one bounded correction wave and one further independent
review round at `2026-09-15T19:36:46.668573Z`. The prior three rounds remain
consumed. This approval also permits a bounded evaluator-input diagnosis but
not replacement of its trust anchors.

All target configuration parsing now uses the existing bounded compiler parser.
V1 explicitly retains the historical YAML scalar dialect, including `off`,
while byte, depth, node, duplicate-key and alias checks remain enforced.
Ordinary config and v2 target parsing retain strict JSON-compatible scalars.
Recursive value-schema checks no longer depend on an optional parent
`required` declaration.

New v2 descriptor asset hashes use the canonical `sha256:` prefix. V1 target
and index hashes retain their accepted form. The bounded JSON Schema vocabulary
uses exact-pointer allowances for `minLength`, `maxLength`, `minItems`, `maxItems`
and `additionalProperties` under `$/$defs/value_schema/properties`. No unrelated
property or digest check is exempted. The Phase20 amendment records the revised
unpublished schema pin without modifying the historical baseline.

The installed dependency contract explicitly requires `pydantic>=2.13.5,<3`.
Earlier wheel metadata admitted Pydantic 1. Pydantic 2.5 supplies `JsonValue`, but
an actual clean consumer at 2.5 failed public API forward-reference resolution.
The supported floor is the already-exercised 2.13.5 assembly, not a claim of the
oldest version on which an individual import exists.

The evaluator environment includes the digest of `pyvenv.cfg`. An actual
same-Python comparison of uv0.12.7 and0.12.14 showed that the generated file
changes with uv's version. The workflow restores the historical producer
version0.12.7; environment, Python and Docker trust anchors and their assertions
remain unchanged. Only a fresh Linux measurement can establish full recovery.

The installed API reproductions, source boundary regressions and final
exact-artifact proofs govern this correction. Earlier candidate test counts and
review verdicts are historical evidence, not acceptance of the revised head.
Protected CI, independent review and all later runtime/recipient gates remain
mandatory; the rollback and custody rules in section11 are unchanged.

## 13) Installed upstream notice resources, 2026-09-15

The installed wheel must retain applicable notices for copied historical target
assets and the standalone author-worker helper's bundled dependencies. This is
an additive packaging correction, not a compiler, runtime or license-policy
change. The helper notice is an execution-namespace resource and therefore
remains subject to the danger-zone decision and review gate.

Keep the Pi `0.57.1` and Oh My Pi `16.2.13` notices under
`config/e4_targets/notices`, outside every indexed or model-visible target asset.
Their pinned npm archives match the existing integrity, hash and size anchors
but omit a root LICENSE. Retain the root license text from each exact recorded
upstream source commit; this does not establish recovery of a historical source
archive.

Package `node/THIRD_PARTY_NOTICES.txt` beside the existing helper. It retains the
four distinct original license bodies for ajv `8.18.0`/`8.20.0`,
fast-deep-equal `3.1.3`, json-schema-traverse `1.0.0` and fast-uri
`3.1.0`/`3.1.7`, including the latter's BSD-3-Clause notices. Refresh this resource
when the locked bundled dependency closure changes. Existing explicit
setuptools package-data declarations remain the sole distribution owner.

Kyle's controlled internal-copy authority does not create a first-party MIT
license or public-release permission. These upstream notices grant no additional
rights over other BreadBoard files. All nine existing target resources, their
index/descriptor identities, and the helper bytes remain unchanged.

The initial notice candidate passed the existing built-wheel resource regression.
A fresh installed consumer retrieved the notices at their recorded hashes,
verified target assets and compiled the default profile with source checkout and
network access denied. Original upstream notice bodies remain verbatim in the
wheel. Require current exact-head review and protected CI after this decision
record changes; earlier candidate acceptance is not transferred silently.

Native author-worker execution, supplier/system dependency notices, independent
final-recipient acceptance and whole-campaign completion remain separate gates.
Rollback selects a previously accepted intact distribution and its permissions
and notices; never remove required notices from an otherwise retained artifact.
No custody deletion or review-budget extension is authorized by this record.
