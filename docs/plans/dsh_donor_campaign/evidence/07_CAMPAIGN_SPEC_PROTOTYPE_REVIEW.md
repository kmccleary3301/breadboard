# Independent review — campaign specification prototype

Ticket: `bb-inj5.8`
Date: 2026-09-02
Candidate: `docs/plans/dsh_donor_campaign/evidence/07_CAMPAIGN_SPEC_PROTOTYPE.md`
Reviewed SHA-256: `e01c1fc0dc111cfa32904ef90879d7b1142dbf0118ac8103c38ac0b5614e2f36`
ENGINE: `b3cacc7356244253305f8a6f84308a993485bfe2`
TUI: `73d6e6f55a238fc9ff0486bbcc9ecffe85705715`

## Charter and method

Raw-first independent review. Inputs, in order: H bounded charter; `raw/bb-inj5.8/`; candidate path; pinned heads and candidate identity; then approved A–G artifacts, goal/plan, doctrine, direction charter, and freeze policy. The reviewer recomputed denominators before reading conclusions and made no edits.

Blocking standard: P0/P1 block; P2 fixed or Kyle-accepted; P3 nonblocking. Gate G-H additionally requires a separate fresh-context dry read that can start the first packet without policy questions.

## Recomputed denominators

- Donor items: 615 = 43 law + 7 noun + 4 protocol + 485 donor-section + 26 schema + 50 closure items.
- Seeds: 8; total ledger/DAG rows 623.
- Routing: 623/623 unique; 177 satisfied + 4 superseded + 10 rejected + 29 research-only + 398 deferred + 5 row-promoted = 623. Promoted rows are S1, S2, S4, S7, S8; FT-02/03 are law-promoted.
- Closure groups: 50; DSH phases: 13 (0–12).
- Laws: 54 = 38 normative + 11 narrowed + 2 hypothesis + 1 rejected + 2 governed elsewhere.
- Primitive/protocol verdicts: 11 = 7 nouns + 4 protocols.
- First-tranche packets: 6. Campaign tickets: 9.

Deterministic H validation independently reports seven reviewed source artifacts, 623 unique routes, 13 phases, six packets, nine tickets, and honest `NOT RUNNABLE` substrate status.

## Initial review

Verdict: **CHANGES_REQUESTED**. P0/P1/P2/P3 = 0/2/4/0 after duplicate finding deduplication.

P1:

1. Condensed DAG omitted FT-04/05/06 predecessors required for replay, world, annotation, and child branches.
2. Review policy omitted bounded-charter/raw-first inputs and denominator/anchor recomputation before conclusions.

P2:

1. Packet summaries omitted required plan-section-17 fields, especially repository/head/output/review/rollback/max-claim bindings for FT-02–06.
2. Binding four-artifact roles were absent from evidence procedure.
3. The 50-group route lacked the exact Part X → B Coverage → row-routing chain.
4. The unrun 14/16 S2 cells were incorrectly called rejected rather than `UNVERIFIED`/deferred.

The no-context dry read separately began at P0/P1/P2/P3 = 0/6/1/0. Its findings exposed insufficient FT-01 inputs, fixture, comparator, write, lock, branch, authority, retry, cleanup, and invalidation detail.

## Renewal

One material renewal corrected every initial finding and all consistency defects found while reading the corrected runbook:

- restored exact DAG conjunctions and public/generated/client order;
- made review raw-first and preserved exactly four artifact roles;
- anchored all denominators and closure groups; restored 14 unrun S2 cells to `UNVERIFIED`/deferred;
- incorporated the full packet-contract envelope and exact later-packet links/outputs;
- made FT-01 self-contained: fixed fixture bytes/IDs, error classes, deadlines, typed append-only records, content identities, independent comparators, receipt rules, branch precedence, nullable receipt identities, process authority, cleanup/finalization, preflight-abort encoding, lock audit, and invalidation;
- restored exact Phase 20 schema exceptions and existing-schema tightening allowlist route.

Binding renewal (2026-09-02, correction atop PR head `f46b7565`)
independently recomputed the candidate digest above and revalidated the raw
DAG. `GEN-INTERNAL` and `GEN-ACTIVATION` now have reachable terminal repair,
implementation, or evidence-backed `NOT-TAKEN` outcomes; annotation owner
selection admits the existing Session; and `SEVEN-PIECE-AUDIT` requires
`WF-IMPLEMENT`, not merely the child pilot. The raw validator passed 50
acyclic nodes, 623/623 unique routes, phases 0–12, six packets, and zero
unconditional seams. The package validator passed 14 exact evidence copies,
13 relative links, six guard pairs, and no findings. The renewal found
P0/P1/P2/P3 = 0/0/0/0, so the prior review's substantive approval remains
valid at this exact binding.

Independent renewal verdict: **APPROVED**.
P0/P1/P2/P3: **0/0/0/0**.
Confidence: 0.99.

Historical fresh-context dry-read renewal: **PASS**, P0/P1/P2/P3 = **0/0/0/0**, unresolved policy questions: none. Raw verdict: `raw/bb-inj5.8/dry-read.txt`. That append-only record binds the earlier `5c8b389b…` candidate; the exact-candidate renewal above reviewed the later binding and gate-correction deltas, found no new policy question, and carries the substantive dry-read PASS forward without rewriting historical raw evidence.

## Gate G-H

**PASS.** The reviewed candidate is complete enough to present to Kyle. It preserves pinned heads, current owners, approved laws, the six evidence-only/no-new-seam packet boundaries, guarded conditional promotion, Phase 20 freeze, honest substrate fog, and separately reviewable engine/provider/SDK/TUI/installed/E4 boundaries. It does not claim implementation or current substrate runnability.
