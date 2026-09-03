# Independent review — campaign specification prototype

Ticket: `bb-inj5.8`
Date: 2026-09-02
Candidate: `docs/plans/dsh_donor_campaign/evidence/07_CAMPAIGN_SPEC_PROTOTYPE.md`
Reviewed SHA-256: `8cbdbfbdeba61e37af992f1cfac401d934dfa8be7b50984f1a9602e198984584`
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

Binding renewal (2026-09-02, correction atop PR head `5f6ebbb1`)
independently recomputed the candidate digest above and revalidated the raw
DAG. Runtime repair, generation, and annotation gates have reachable terminal
outcomes; `SEVEN-PIECE-AUDIT` requires `WF-IMPLEMENT`; and the substrate
command requires the complete public rollout through E4. FT-01 retains every
retry in append-only attempt streams with closed fail-closed classifications,
complete permission/cardinality evidence, authenticated process identity, and
test-only L1/L2 writes. The required initial loopback listener now has a closed
top-level host/port schema. Installed backend and TUI-build revisions must
equal the pinned heads; ancestor-only identities stop as `INFRASTRUCTURE_RED`.
FT-06's B inventory input is committed and bound by its file digest, so a clean
checkout can perform the required comparison. The raw validator passed 50
acyclic nodes, 623/623 unique routes, phases 0–12, six packets, and zero
unconditional seams. The package validator passed 14 exact evidence copies,
13 relative links, six guard pairs, and no findings. The renewal found
P0/P1/P2/P3 = 0/0/0/0; the prior substantive approval remains valid at this
exact binding.

Independent renewal verdict: **APPROVED**.
P0/P1/P2/P3: **0/0/0/0**.
Confidence: 0.99.

Historical fresh-context dry-read renewal: **PASS**, P0/P1/P2/P3 = **0/0/0/0**, unresolved policy questions: none. Raw verdict: `raw/bb-inj5.8/dry-read.txt`. That append-only record binds the earlier `5c8b389b…` candidate; the exact-candidate renewal above reviewed the later binding and gate-correction deltas, found no new policy question, and carries the substantive dry-read PASS forward without rewriting historical raw evidence.

## Gate G-H

**PASS.** The reviewed candidate is complete enough to present to Kyle. It preserves pinned heads, current owners, approved laws, the six evidence-only/no-new-seam packet boundaries, guarded conditional promotion, Phase 20 freeze, honest substrate fog, and separately reviewable engine/provider/SDK/TUI/installed/E4 boundaries. It does not claim implementation or current substrate runnability.

## Post-contract escalation review

This is not a second renewal. The single renewal budget ended at line 83. Later automated PR review identified publication-blocking executable-contract defects after Gate G-H had closed. Section D §114 requires a split or escalation instead of a third round. Kyle's later DSH donor implementation mandate authorizes correcting every substantive review finding and requires a new exact-artifact independent review before merge; this section records that bounded escalation and reopens only the affected incorporated packet and routing clauses.

Escalation review date: 2026-09-03. Reviewed prototype SHA-256 `f69d100faaa4bd63a0e4c88080ccffdd9e003ddb8aba82f3e38723db64dec6d0`; pinned ENGINE/TUI heads unchanged.

The escalation review independently verified that the prototype preserves FT-01's qualifying L2 exception, incorporates the corrected FT-03 contract by normative exact-section link, opens `RT-REPAIR` only for `KNOWN_DIVERGENCE`, joins coherent evidence with FT-04 at `RT-REPLAY`, and closes both RT nodes for `PRODUCT_RED` or another terminal defect. Raw DAG validation passes 50 acyclic nodes and 623 unique routes; prototype validation passes 19 checks; package validation passes 16 exact evidence, fixture, and escalated-review copies, 13 links, six guard pairs, and no findings.

Independent escalation verdict: **APPROVED**.
P0/P1/P2/P3: **0/0/0/0**.
Confidence: 0.99.

Gate G-H remains **PASS** on the exact prototype digest above.

## Post-contract escalation review — reproducible fixture and inventory closure

Final escalation review date: 2026-09-03. Reviewed prototype SHA-256 `f2665cd1fc0ff4f8573756941150913f2b871f7ac7835be6de3ea71f6eb63f59`; pinned ENGINE/TUI heads unchanged.

The independent final read verified the exact FT-01 2/16 schedule and retry scope; authenticated writable L1/L2 boundaries; terminal generation prerequisites and activation; H/I ordering and seven-piece gate; FT-03's product-adapter capture and typed stop wrapper; and FT-06's executable extractor against the generated fixed point without modifying the historical baseline. The source package contains 22 exact evidence copies; the self-contained publication package requires 27 exact inputs, including the donor inventory, exact-artifact bindings for every blocking G-A through G-H review, and the executable DAG validator with its machine inputs.

Independent final escalation verdict: **APPROVED**. P0/P1/P2/P3: **0/0/0/0**. Confidence: 0.99.

Gate G-H remains **PASS** on the exact prototype digest above.

## Post-contract escalation review — repair-success predicate

Final exact review date: 2026-09-03. Reviewed prototype SHA-256 `8cbdbfbdeba61e37af992f1cfac401d934dfa8be7b50984f1a9602e198984584`; pinned ENGINE/TUI heads unchanged.

The independent exact read verified that the prototype now matches the machine DAG: `RT-REPLAY` opens after `KNOWN_DIVERGENCE` only when `RT-REPAIR` explicitly succeeds and proves the divergence absent. Unknown, killed, exhausted, product-red, and every other typed-red repair outcome leaves replay and every dependent node closed. The exact machine validator passes 50 acyclic nodes and 623 unique routes.

Independent final escalation verdict: **APPROVED**. P0/P1/P2/P3: **0/0/0/0**. Confidence: 0.99.

Gate G-H remains **PASS** on the exact prototype digest above.
