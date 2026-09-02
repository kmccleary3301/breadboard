# Campaign evidence and approval contract

Ticket: `bb-inj5.4`
Status: APPROVED — Workstream D gate passed
Session: 1

## Control panel

- North star: make evidence support, retries, review, approvals, and completion mechanically decidable by a new supervisor.
- Current claim: every evidence, retry, review, approval, staleness, and completion rule is explicit; only Kyle may accept a P2.
- Latest product signal: A and B each needed one evidence-only review renewal; the bounded correction rule found real anchor defects without expanding product scope.
- Current red class: none.
- Next cheapest discriminator: none; later workstreams now apply this contract.
- Active external blocker: none.
- Stop or escalation: reopen only through PROC-STALE or PROC-AMEND if a binding input or material rule changes.

DECISION THIS STAGE CAN MOVE: the exact evidence, retry, review, approval, and completion rules governing every later campaign decision.
EVIDENCE THAT MOVED IT: binding authority text, Kyle's recorded P2-authority answer, and the fresh-context PASS.
EVIDENCE PRODUCED: authority-derived support ladder, proof matrix, budgets, review/staleness rules, approval matrix, session protocol, amendment protocol, and six mechanical consistency cases.
PROVENANCE-ONLY ARTIFACTS: session provenance and historical owner-decision attempt records in `raw/bb-inj5.4/`.

## Gate card and nonclaims

- Claim: a fresh supervisor can classify support, select the smallest proof, stop retries, identify stale evidence, identify required approval, and decide campaign completion without inventing policy.
- Denominator: nine evidence-contract checklist items D1-D9 and every action class in the approval matrix.
- Discriminating power: a fresh-context reader applies the contract to one donor gap, one product defect, one evidence defect, one public-schema proposal, one compatibility deletion, and one completion claim; any conflicting answer is P1.
- Oracle: the authority order in the campaign plan, `STANDING_DOCTRINE.md`, ENGINE charter/freeze/amendments/registry, recorded Kyle answers, and the eight blocking gates.
- Cost: at most two live sessions; one question at a time; one fresh-context consistency read; no independent review.
- Retry: only after a changed authority, a new owner answer, or a diagnosed inconsistency.
- Stop: every material choice explicit and consistency read has no P0/P1. Otherwise block D with the exact unanswered choice.
- Fallback: retain higher-precedence authority, mark the conflicting lower rule inapplicable, and use PROC-AMEND; never weaken a gate.
- Nonclaims: D does not choose semantic laws, donor promotions, tranche packets, product fixes, or implementation interfaces.

## Canonical terms

- **Evidence**: an observation or deterministic record that can move a named campaign decision under an admitted gate.
- **Assurance**: useful confidence that is not one of the eight blocking-gate oracles; it never substitutes for evidence.
- **Provenance**: identity of bytes, source, revision, environment, and method. Provenance can establish identity, never behavior.
- **Observation**: behavior actually exercised at the named seam with raw output and cleanup proof where work was owned.
- **Contract**: intended observable behavior and its authority; source presence alone does not create one.
- **Gate**: one blocking decision rule from plan section 5.2, with claim, oracle, cost, and stop.
- **Owner**: the current symbol/module or governance authority that controls the behavior or decision; it is not a name inferred from prose.
- **Consumer**: a current callsite, generated binding, contract, client, profile, or external interface that depends on the owner.
- **Promotion**: inclusion in the workstream DAG or an implementation packet because of an observed gap, proven simplification, or measured win.
- **Approval**: Kyle's authorization for a decision class reserved to him. Under Kyle's 2026-09-02 instruction, an ask-tool auto-selection counts as his answer; silence outside that mechanism, precedent, and tool access do not.
- **Stale**: evidence whose named input, head, artifact, oracle, or material decision changed.
- **Completion**: all section-19 conditions, all eight gates, linked score 1000/1000, committed spec, and closed tracker map; score alone is not completion.

## Binding rules and resolved authority conflicts

1. Higher authority wins: Kyle's latest instruction; active instruction files; ENGINE governance; standing doctrine; campaign plan; tracker; claim indexes.
2. Assessment prose and historical handoffs are evidence leads only. Current truth comes from pinned heads, current consumers/contracts/tests, and observed installed behavior.
3. Product runtime is engine + v2 config/compiler + `/v1` session API + SDK/TUI/CLI. E4 is internal conformance evidence and never product truth.
4. The Phase 20 freeze is active. No new schema family, SDK package, lane kind/lane, ledger, scoreboard, or campaign evidence form without the freeze's named amendment route.
5. First-tranche packets always state `NO PUBLIC SCHEMA OR API CHANGE`, `NO NEW SEAM`, and freeze impact `none`.
6. A generic noun may be vocabulary; it owns behavior only after the section-6.1 module test. One adapter means a hypothetical seam. Similarity to DSH is never promotion evidence.
7. Doctrine says exactly four artifact roles; plan says at most four. Applied rule: combine charter and decision when the ticket names one decision artifact; raw store and review/consistency output remain within the cap. Never create a fifth artifact.
8. Doctrine says one binding independent review per stage; plan, as later and campaign-specific authority, reserves independent review for A/B/C/F/H, fresh-context consistency for D/E/G, scripts for I.
9. Doctrine says a material input change stales review. Plan narrows this: invalidate only affected decisions/rows under PROC-STALE; prose-only edits do not stale a review.
10. The freeze calls `BB_RS_PROGRESS.json` the only Phase 20 scoreboard. Campaign scoring remains in this pre-existing execution plan and final completion audit; no repository scoreboard is created.

No unresolved authority conflict or delegation gap remains.

## Support-claim ladder

Support is monotonic only for the exact claim and scope stated; evidence for a narrower claim does not automatically support a broader one.

| Level | Support | What it permits | What it cannot claim |
| ---: | --- | --- | --- |
| 0 | `UNVERIFIED` or rumor | Record a question or search lead | current behavior, gap, parity, promotion |
| 1 | Pinned provenance | Identify source/artifact/head/environment | behavior or correctness |
| 2 | Source/interface presence | Say an interface or owner exists | that it works, has consumers, or satisfies donor semantics |
| 3 | Current consumer + contract + focused test | `SATISFIED` for that bounded behavior | installed composition, cross-client parity, measured win |
| 4 | Observed public/installed seam | Classify actual behavior, failure, cleanup, or divergence | universal behavior beyond scenario denominator |
| 5 | Interface-level behavior lock | Retain an observable contract against plausible regression | parity with another implementation unless compared |
| 6 | Deterministic differential/conformance result | Scoped parity or exact reconstruction against independent oracle | untested inputs or statistical population claims |
| 7 | Pre-registered measured scenario | Promotion for measured win; report effect/interval or deterministic exact result | different scenario, environment, or substrate claim |

### Required support by claim type

| Claim | Minimum support |
| --- | --- |
| Donor `SATISFIED` | Level 3: symbol owner, consumer/contract, focused evidence at pinned head |
| Donor `PARTIAL` | owner evidence plus exact missing behavior; no promotion implied |
| Donor `ABSENT` | defined consumer universe searched at pinned heads; known blind spots stated |
| Product defect | Level 4 reproduction at public/installed seam + intended contract; then PROC-DEFECT |
| Contract gap | observed ambiguity or divergence plus proof no governing contract resolves it; decide in D/E |
| Simplification | every affected consumer named; survivor/lock named; fewer interface facts, one authority removed, or zero-consumer seam deleted |
| Measured win | Level 7: pre-registered section-6.6 scenario, metric, independent oracle, denominator/environment |
| Parity | Level 6 deterministic mapping over declared scope; mismatches and exclusions explicit |
| Completion | G-Score 1000 plus every section-19 deliverable/gate and committed spec; no score substitution |

## Minimum proof by task type

| Task | Blocking proof | Assurance that must not block |
| --- | --- | --- |
| Research/provenance | primary source/revision/license; denominator and extraction rule where exhaustive | extra source summaries, redundant hashes |
| Reproduction/defect | actual public or installed seam, raw sequence, independent oracle, cleanup/liveness | source tests that do not exercise composition |
| Behavior lock | deterministic interface-level test; observable contract; plausible bug fails; independent oracle; isolated | internal-unit duplication or snapshot of incidental formatting |
| Structural refactor | pre-existing behavior lock; module/interface/seam contract; caller migration set; focused post-change proof | line counts, broad full-suite reruns without a risk claim |
| Migration | source/survivor authorities, every consumer, clean cutover, compatibility/public approval, parity lock | shim accumulation or dual authority called “temporary” |
| Deletion | zero consumers or completed migration; registry/freeze disposition; survivor lock; deletion reduces interface facts/authority | raw line deletion count |
| Performance | profile/benchmark first; pre-registered metric/scenario/environment; interval for stochastic results | unprofiled micro-optimizations or one noisy timing |
| Developer experience | one bounded outsider task with completion/error metric and fixed inputs | author opinion or documentation volume |
| Client parity | authoritative operation/event catalog; same inputs; deterministic cross-client diff; exclusions | matching method names alone |
| Interface existence | one smoke at the actual interface | repeated smoke/soak |

Instrument selection is first-match: conformance for authoritative deterministic mappings; pre-sized estimate for stochastic effects/rates; assertion for invariants; one smoke for existence; saturation curve for capacity. If two instruments appear necessary, split into two questions.

## Budgets, reds, retries, and stop rules

Ticket session caps: A 2; B 4; C 3; D 2; E 3; F 2; G 2; H 2; I 1. Exceeding a cap triggers a Kyle checkpoint stating what user-visible decision changed; it does not silently extend scope.

Only gates G-A, G-B, G-C, G-D/E, G-F, G-H, G-I, and G-Score block. Independent review: A/B/C/F/H one review plus at most one material-change renewal. D/E/G one fresh-context consistency read reporting P0/P1 only. I runs the named scripts once each. A third review round is prohibited: split or escalate.

Every red is classified before action:

- `product`: record reproduction, invoke PROC-DEFECT, never patch in campaign.
- `contract`: resolve in D/E; do not manufacture tests for unspecified behavior.
- `evidence`: repair only record/provenance/anchor.
- `infrastructure`: record blocker; choose cheaper discriminator.
- `stale`: invalidate smallest affected decision and rebuild once.
- `unknown`: diagnose; never label timeout pass or infrastructure without evidence.

A retry requires new bytes, oracle, environment, or written mechanism diagnosis. Same bytes/oracle/environment is prohibited. Maximum three attempts per typed defect; after the third, retain the strongest claim, record conditions/unblocker, and stop.

## Reviewer contract

Reviewer inputs, in order: bounded charter; raw store; artifact path; pinned heads and candidate digest. Reviewer reconstructs counts/anchors before reading conclusions. It never edits candidate/raw bytes and never self-approves.

Required output:

1. date, candidate digest, ENGINE/TUI heads;
2. raw-first method and exact denominators;
3. sampled anchors/symbols and exclusions;
4. findings by P0/P1/P2/P3 with file/line, mechanism, affected claim, correction condition;
5. verdict `APPROVED` or `CHANGES_REQUESTED`;
6. renewal section, if used, preserving the initial verdict.

Severity:

- P0: evidence fabrication, security/data-loss hazard, wrong baseline, or completion claim invalid at campaign scope. Blocks.
- P1: material correctness, coverage, owner, gate, freeze, or executable-spec defect. Blocks.
- P2: bounded precision/maintainability defect that does not falsify a gate; it must be fixed or explicitly accepted by Kyle. Acceptance records finding, scope, rationale, and expiry/revisit condition.
- P3: nonblocking note; never conditions approval.

Staleness: head/source/denominator/oracle/material disposition, law, packet, or verdict change invalidates only citing decisions and any review of them. Candidate prose, spelling, links that do not alter evidence, and tracker notes do not stale a decision review. A stale review cannot approve until the smallest affected renewal runs.

## Approval matrix

| Action | During this campaign | Required authority |
| --- | --- | --- |
| Read pinned repos/public primary sources | allowed | supervisor under current task |
| Write `docs_tmp/` campaign artifacts/raw stores | allowed within active ticket/artifact cap | supervisor |
| Edit ENGINE/TUI evidence worktrees | prohibited; they stay clean | not available in campaign |
| Product/runtime/SDK/TUI implementation | prohibited | future campaign after this map |
| Create first-tranche public schema/API or new seam | prohibited | cannot be approved inside first tranche |
| Later public schema/API/config/persistent-format change | plan/amendment only, no implementation here | Kyle explicit + `SPEC_AMENDMENTS.md`/freeze route |
| Compatibility deletion | plan only after every consumer/survivor/lock is named | Kyle explicit; implementation outside campaign |
| New schema family, SDK package, lane, ledger, scoreboard | frozen | failing consumer/correctness case or Kyle sign-off recorded in amendment; still outside first tranche |
| Network research | public primary sources allowed | supervisor; no credentials/secrets |
| Secrets/credentials access | prohibited absent exact need and explicit instruction | Kyle explicit per secret/use |
| Local final-spec worktree and docs commit | allowed only in I | existing plan authorization; record commit hash |
| Push, PR, merge, release, publication, deployment | prohibited | Kyle explicit future authorization |
| Child Beads issue closure | allowed after its gate/checklist/notes | supervisor |
| Final child and map closure | only after section 19 and G-I/G-Score | supervisor under completion contract |
| Slurm campaign jobs | standing authority exists, but DSH wayfinding has no admitted Slurm proof/job | no job unless a later admitted campaign explicitly requires it |

Tool capability is never approval. A clean command is evidence only for the claim it exercises.

## Session, tracker, resumption, and amendments

- One child may be `in_progress`. A session runs from claim to close or block; compaction does not end it.
- At session start/resume: re-read goal/plan/doctrine/governance; confirm heads/clean status; run integrity; query Beads; refresh control panel; open one raw provenance block.
- Beads is authoritative local tracker. Claim, append notes, close child, then append map decision. If unavailable, classify infrastructure; never shadow-track.
- Block only on external input or service after all reachable work; name exact missing prerequisite and attempted routes.
- PROC-STALE invalidates only citing rows/decisions/reviews. Re-pin versus hold is explicit if main advances.
- PROC-DEFECT records reproduction and asks Kyle: outside fix now, first-tranche hold, or known divergence. Campaign never patches.
- PROC-AMEND records a plan defect/new sharp question, adds a child only when none owns it, rewires dependencies, bumps plan revision, transfers points without changing total 1000, and reruns integrity.
- PROC-PARK records opportunity/source/current owner without implementation.
- No deliverable is complete until affected callsites/tests/docs are intentionally covered, named gate passes, evidence links score, tracker closes, and final completion conditions hold.

## Owner decisions and rejected alternatives

### Q1 — P2 acceptance authority

**DECIDED by Kyle, 2026-09-02:** only Kyle may accept a P2 instead of fixing it. Acceptance must name finding, scope, rationale, and expiry/revisit condition in the ticket artifact or tracker. Kyle additionally directs that ask-tool auto-selections always count as his answer. Rejected: supervisor self-acceptance (not independent) and repository-owner acceptance (symbol ownership is not campaign governance authority).

## Fresh-context consistency read

`raw/bb-inj5.4/consistency-read.txt` applied the frozen contract to six cases: PARTIAL donor ownership, unspecified intended behavior, invalid review anchor, first-tranche public schema, compatibility deletion with consumers, and 1000 points with an uncommitted spec. Every case produced one mechanical classification, proof/action, approval, and retry/stop decision. P0/P1 findings: none. Verdict: PASS.

## Closure

G-D PASS. A new supervisor can classify evidence, stop retries, invalidate stale work, identify approval needs, and distinguish score from completion without unstated policy. Workstream D used one live session and one fresh-context consistency read; independent review was neither required nor run. No production code, public contract, schema, API, compatibility surface, or repository head changed.
