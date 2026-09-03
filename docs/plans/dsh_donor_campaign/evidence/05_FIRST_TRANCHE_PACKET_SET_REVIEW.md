# Independent G-F review — CHANGES_REQUESTED

Review date: 2026-09-02. Frozen candidate SHA-256 `5a9d1178da50c04d1fecaaddd19a7954bd3c73d8d251a04dad2dc4620562c9b1`. ENGINE `b3cacc7356244253305f8a6f84308a993485bfe2`; TUI `73d6e6f55a238fc9ff0486bbcc9ecffe85705715`.

## Recomputed coverage

Six of six selected packets: FT-01 through FT-06. Section-7 cases 4/4; S8 capabilities 3/3; S7 consumers 4/4; F6 inventory families 8/8. C's bounded matrix is 16 cells with 2 unique characterized cells, not the candidate's claimed 3. No sampled public/schema/API migration, compatibility removal, new seam, production fix, ledger, scoreboard, or non-none freeze impact entered the set. All six packets carry `NO PUBLIC SCHEMA OR API CHANGE` and `NO NEW SEAM`.

## P0

None.

## P1

1. **S2 denominator.** FT-01 counts unchanged explicit retry as a third fault/timing cell. It is a within-cell observation of `replace authority × before submit`. Required: 2/16, 14 remaining.
2. **FT-04 parity fixture.** Public Python/TS consume `bb.public_session_event.v1`; canonical TS consumes the E4 stable-cursor envelope; TUI consumes normalized `LoggedSessionEvent`. One raw envelope cannot exercise all four. Required: one semantic unknown-required-event case with explicit per-surface mappings and typed expectations, or split proofs.
3. **FT-05 executable criteria.** The three S8 fixtures and pass/partial/fail criteria are unspecified, leaving Code Mode, sidecar, and durable-child policy to the executor. Required: exact authored documents/fields and first-missing-fact criteria, with runtime lineage facts separated from compile expressibility.
4. **Lock budget.** The declared global cap of three can be exceeded by FT-01, FT-02, FT-03, and FT-05. Required: one mechanically enforced allocation, a learned-behavior row per lock, and six named holds per retained lock.
5. **Section-17 fields.** All six packets need explicit approval nodes, freeze amendment route, permissions, and blast radius.
6. **Per-packet provenance.** Every characterization packet needs a first-action provenance block and linked base-branch proof, not only set-level heads.

## P2

1. Split adapter count from consumers in the promotion matrix and record an explicit count/method for every row.
2. Narrow FT-03 to one reconstruction characterization unless an ablation pair is pre-registered. Explicitly forbid using `ProviderExchangeV2.request` or recorded outbound bytes as reconstruction inputs.

## P3

None.

## Verdict

`CHANGES_REQUESTED`, confidence 0.98. P1 findings block Gate G-F. One material renewal is permitted after correction. The review becomes stale on any packet, disposition, or head change.

## Renewal review — APPROVED

Renewal date: 2026-09-02. Renewed candidate SHA-256 `abcb7369d1bb13f7ce42b24a4491b746dd5319eb5edacaf277fd82b919446701`. Pinned heads unchanged.

The renewal verified all eight corrections: S2 is 2/16 and retry remains inside the replacement cell; FT-04 carries one semantic required-event fixture with explicit public Python/TS, canonical TS, and TUI mappings; FT-05 carries exact independent fixtures and E-law-derived verdict criteria; the seven learned rows admit exactly three globally allocated locks with six named holds and zero new locks elsewhere; all 6/6 packets include first-action provenance, permissions, blast radius, approval nodes, amendment route, exact-artifact binding, compatibility impact, proof, stop, rollback, and failure branch; the promotion matrix separates consumers from adapter counts or justified `N/A`; FT-03 claims one reconstruction only and forbids retained request/capture bytes as inputs.

Final prohibited-work audit: 6/6 packets are evidence-only with freeze impact `none`; no product implementation, public/schema/API change, compatibility removal, new seam/module/dispatch layer, SDK package, lane, ledger, or scoreboard entered the tranche.

Residual findings: P0 0, P1 0, P2 0, P3 0.

Verdict: `APPROVED`, confidence 0.99. Gate G-F passes. Future packet observations remain unverified; this review approves their contracts and scope only.

## Second renewal review — APPROVED

Renewal date: 2026-09-02. Exact packet SHA-256 `b11afb51d72ea2597042b404dcfa6204474a4b0997ddbf9ecd6fbab14525ba3e`; reviewed PR head `10ae04c5`; pinned ENGINE/TUI heads unchanged.

The independent renewal revalidated FT-02's fixed four-case barrier, exact commands and payloads, pinned ENGINE working directory and provenance, 30-second case deadlines, 240-second total deadline, observations, and cleanup contract. FT-03 now binds immutable fixture `ft03-request-v1` at SHA-256 `e8cfac26efd0210997ecc31241d95d805d656b0436f2ccd42f3b58523da4dcd2`, with exact prompt-section, tool-schema, history, default, adapter-override, canonicalization, and independent held-out-oracle inputs. FT-01 permits only qualifying test-only L1/L2 changes, including the named separate-TUI installed-product test for L2; production ENGINE and TUI bytes remain forbidden.

Residual findings: P0 0, P1 0, P2 0, P3 0.

Verdict: `APPROVED`, confidence 0.99. Gate G-F passes on the exact packet digest above.
