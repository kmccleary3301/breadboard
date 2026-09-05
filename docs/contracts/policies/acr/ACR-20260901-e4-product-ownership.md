# ACR-20260901-e4-product-ownership

- `acr_id`: `ACR-20260901-e4-product-ownership`
- `title`: Move active E4 evidence adapters under product ownership
- `author`: Codex (engine PR campaign)
- `date`: 2026-09-01
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

- Classification: `internal`.
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
