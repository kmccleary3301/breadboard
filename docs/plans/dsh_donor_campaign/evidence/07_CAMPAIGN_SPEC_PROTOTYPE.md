# BreadBoard DSH donor campaign specification prototype

Ticket: `bb-inj5.8`
Status: APPROVED — G-H passed; Kyle approved versioning
Version: prototype 0.1

## Control panel

- North star: hand a new supervisor an implementation-ready, evidence-gated plan whose first packet can start without policy invention and whose later work converges toward one research substrate without weakening the Lock.
- Current claim: the draft composes all approved A–G decisions; deterministic trace validation, no-context dry read, and independent review pass; Kyle approved it for documentation-only versioning in I.
- Latest product signal: permission cancellation is coherent; engine replacement yielded one generic transport failure with no observed duplicate admission, while one recovered durable turn was absent from the selected logical Session journal.
- Current red class: none. Final dry read and independent renewal both report P0/P1/P2/P3 = 0/0/0/0.
- Next cheapest discriminator: Workstream I creates the separate spec worktree, copies reviewed evidence, resolves links, runs integrity/freeze checks, and commits locally.
- Active external blocker: none.
- Stop or escalation: any P0/P1 blocks; P2 is fixed or Kyle-accepted; product defects use PROC-DEFECT; surface/seam/freeze changes use PROC-AMEND.

DECISION THIS STAGE CAN MOVE: whether this draft is complete and executable enough to become the versioned campaign specification.
EVIDENCE THAT WOULD MOVE IT: dry-read reconstruction of the first packet; independent traceability/freeze/consistency review; Kyle approval.
EVIDENCE PRODUCED SO FAR: approved A–G artifacts, 615/615 donor extraction, 623/623 current-state and DAG routing, two bounded fault classifications, 54 law verdicts, 11 primitive/protocol verdicts, six packet contracts, a 50-node acyclic DAG, deterministic prototype validation, one passing dry read, one approved independent renewal, and Kyle's versioning approval.
PROVENANCE-ONLY ARTIFACTS PRODUCED SO FAR: `raw/bb-inj5.8/` contains two session provenance blocks, deterministic validation, dry-read verdict, and owner decision.

## Bounded charter

- **Claim:** a fresh implementation supervisor can start FT-01 and route every possible result without asking for unstated product policy.
- **Denominator:** 615 donor items, eight campaign seeds, 50 closure groups, 623 ledger/DAG rows, 54 semantic laws, seven nouns, four protocols, nine campaign tickets, six first-tranche packets, 13 DSH phases, seven substrate pieces, and every engine/SDK/TUI/installed/E4 boundary named below.
- **Discriminating power:** deterministic completeness and dry-read checks need no statistical interval. The independent reviewer must find zero P0/P1; every P2 is fixed or explicitly accepted by Kyle.
- **Oracle:** reviewed A–G decisions and raw stores at pinned heads; this document's traceability table; F packet oracles; G graph validator; freeze policy/checker.
- **Cost:** H itself performs one dry read and one independent review, with one permitted renewal if decision-bearing bytes change; H runs no product. After H/I close, this specification separately authorizes FT-01's two bounded provider-free product observations under its packet budget.
- **Retry:** only after corrected draft bytes or a written mechanism diagnosis. Unchanged bytes get no second review.
- **Stop/fallback:** blocking review finding keeps H open and produces the smallest correction. A missing product fact remains a named gate; it is never filled by specification prose.
- **Nonclaims:** no implementation, runtime substrate, public surface, compatibility migration, PR, merge, release, publication, or approval for a later conditional seam.

## 1. Mission, authority, and baselines

Deliver a versioned implementation handoff for BreadBoard's engine, public SDKs, `tui_skeleton`, separate `breadboard-tui`, installed product, and E4 conformance consumer. The campaign is exhaustive in disposition and selective in execution. Similarity to DSH is never promotion evidence.

Authority, highest first:

1. Kyle's latest explicit instruction.
2. Active harness and repository instruction files.
3. ENGINE `docs/DIRECTION_CHARTER.md`, Phase 20 freeze/amendments, contract-tier registry, and freeze checker.
4. workspace `docs/STANDING_DOCTRINE.md`.
5. campaign execution plan and this approved specification when committed.
6. Beads map and ticket graph.
7. source assessments and handoffs as evidence leads only.

Pinned evidence roots:

| Root | Path / revision | Rule |
| --- | --- | --- |
| WORKSPACE | `/Users/kylemccleary/projects/breadboard` | Tracker and campaign evidence only; historical snapshot directories are never product evidence. |
| ENGINE | `/Users/kylemccleary/projects/breadboard-dsh-baseline-b3cacc73` at `b3cacc7356244253305f8a6f84308a993485bfe2` | Read-only, clean baseline. |
| TUI | `/Users/kylemccleary/projects/breadboard-tui-dsh-baseline-73d6e6f5` at `73d6e6f55a238fc9ff0486bbcc9ecffe85705715` | Read-only, clean baseline. |
| Python | `/opt/homebrew/bin/python3.11` | Never use `/usr/bin/python3`. |

Head movement invokes PROC-STALE. The later committed specification lives in a separate ENGINE worktree and branch; evidence roots remain clean.

## 2. Target substrate and honest current boundary

The desired composition has seven pieces:

1. durable logical Session events as sole runtime truth; pure projections and as-of lineage;
2. exact provider-request reconstruction with requested/effective/default provenance;
3. immutable Lock-compatible generations pinned by Sessions, with explicit quiescent adoption;
4. local/container/Ray/Slurm execution worlds preserving Session semantics modulo declared world fields;
5. immutable annotation sidecars bound to message and trajectory identity;
6. durable child Sessions and workflows surviving process death with exactly one settlement;
7. lossless compaction preserving exact effective context after any number of compactions.

Target acceptance shape, not an approved public interface:

```console
bb research compare --definition EXPERIMENT.json --world WORLD.json --generation GENERATION.json --projection PROJECTION.json --compare E,E_PRIME
```

Acceptance requires Definition/Lock digest equality, declared world-mask event equivalence, quiescent generation adoption, replay-versus-durable-state equality, held-out provider-request byte equality, immutable annotation identity, exactly-one child settlement, and pre/post-compaction exact reconstruction.

**Current state: NOT RUNNABLE.** Definition/Lock owners exist; request reconstruction, event truth, generation behavior, and expressiveness have first-tranche evidence packets. General projection ownership, execution-world equivalence, annotation ownership, durable children/workflows, compaction, and the command owner remain gated fog. The specification plans toward the vertical slice; it does not manufacture missing owners.

## 3. Frozen donor source and reuse

- Primary source: first-party `deepseek-ai/deepseek-harness`, tag `dsh-v0.1.0-rc.8`, commit `141eb6fef83422698aef7a981029e843e8161534`.
- Frozen denominator: N = 615 explicit donor proposals in `DONOR_ITEMS.yaml`.
- Family coverage: Part IV laws, seven nouns, four protocols, sections 15–50, section 49 schema proposals, and all 50 closure groups.
- Reuse: pinned repository source/docs are MIT; copies or substantial portions retain copyright and permission notice. Third-party notices, vendored/generated/dependency content retain their own terms.
- Synthesized inventory entries without exact item-level primary anchors are idea-only. No silent donor upgrade or inferred anchor.

## 4. Current-state disposition and promotion rule

At pinned heads, 615 donor rows plus eight seeds produce 623/623 unique dispositions:

| Status | Count | Current effect |
| --- | ---: | --- |
| SATISFIED | 177 | Keep existing owner/contract. |
| PARTIAL | 343 | Missing behavior is named; no automatic implementation. |
| ABSENT | 58 | Complete consumer search found no owner; still not automatically promoted. |
| RESEARCH_ONLY | 29 | Requires pre-registered metric and kill. |
| REJECTED | 10 | Reopen only through PROC-AMEND. |
| SUPERSEDED | 4 | Keep replacement; delete old path only at zero consumers. |
| CONTRADICTED | 2 | Strong current invariant/counterevidence wins unless amendment. |

Every one of the 50 closure groups maps to at least one ledger row. Sixteen rows proposed a module/seam; eight had at least two current adapters, but adapter reality alone did not authorize implementation.

Promotion requires exactly one of:

- observed gap at a stable product seam;
- proven simplification: smaller enumerated caller interface, duplicate authority removed with all consumers migrated, or zero-consumer seam/adapter deletion;
- measured win on a pre-registered research scenario.

No line-count win, resemblance argument, generic noun, or generated report can substitute.

## 5. Observed fault boundary

| Observation | Classification | Current claim | Consequence |
| --- | --- | --- | --- |
| Permission prompt cancellation | OBSERVED coherent behavior | one deny, one cancelled turn, no gated tool result, quiescent client | FT-01 may retain L2 only if the six-holds audit proves the installed composition gap. No permission abstraction. |
| Engine replacement before submit | KNOWN DIVERGENCE plus bounded unknowns | first submit returned generic HTTP failure; no observed duplicate/admission conflict; unchanged explicit retry produced one durable turn; recovered turn absent from selected logical journal; stable retry identity UNVERIFIED | FT-01 characterizes journal/admission truth. It does not repair the defect or assert generic HTTP text. |

Only 2/16 S2 cells are characterized. FT-01 covers replacement-before-submit and permission-after-dispatch/before-receipt; retry is an observation inside the replacement cell. The remaining 14 cells stay `UNVERIFIED` and deferred; a later packet may admit only a cell that discriminates a named decision. The full matrix is not a first-tranche deliverable.

## 6. Normative semantic laws

The reviewed law bundle contains 54/54 verdicts: 38 NORMATIVE, 11 NARROWED, two HYPOTHESIS, one REJECTED, two ALREADY GOVERNED ELSEWHERE. Exact per-law owners, evidence, consumers, acceptance, and migration implications are in reviewed evidence `04_NORMATIVE_SEMANTIC_LAWS.md#Verdict table`.

The implementation supervisor must preserve these controlling laws:

### Session and delivery

- Stable Session/input/message/turn identity; ordinary accepted send creates at most one turn.
- Admission is atomic, durable, and idempotent. Retry returns the same disposition or typed conflict.
- Turn/model-step boundaries and cancellation convergence are durable facts.
- Session construction stays private until setup succeeds; disposal reaches quiescence.
- Current ordinary `session.send_input` semantics remain normative. `followup`/`steer`/`inject` and positive batching remain hypothesis pending a named consumer and, if applicable, design-it-twice/freeze amendment.

### Domain operations and authority

- Admission, authority, cancellation, retention, release, and rendering remain domain-owned.
- Visibility/scope do not confer authority; safety denials are monotonic.
- Start and settlement are distinct; each attempt has exactly one terminal result.
- Cancellation completes only after the capability owner terminates/joins.
- Canonical value, model render, and UI render are separate review surfaces.
- No universal Operation, Capability, Resource, lease, or cancellation behavior owner.

### Events, projections, and compaction

- Immutable logical Session facts are sole runtime truth. Retained state, projections, caches, clients, and E4 are subordinate.
- Semantic validation stays with the emitting domain.
- Replay is faithful or typed failure; unknown required events fail unless a governing registry marks them ignorable.
- Projection folds are synchronous/pure; cache state is disposable; derived facts cite source range and projector version.
- Compaction retains reconstructible raw facts and exact effective context.
- A universal projection registry remains hypothesis until a common caller, two real adapters, deletion payoff, three designs, and Kyle approval exist.

### Lock, generations, worlds, annotations, and children

- Definition → effective Lock → Session remains fixed authority; event facts never reverse it.
- Candidate setup precedes publication; drain closes admission before teardown; lifecycle owners prove idempotent quiescence.
- Session pins an exact Lock-compatible generation by default. Explicit adoption occurs only at a quiescent turn boundary, emits old/new identity, reason, and effective sequence, and starts a new trajectory segment.
- Execution worlds preserve Session semantics modulo declared world fields.
- Annotations are immutable sidecars citing message/trajectory identity, author, generation, and payload.
- Durable children are ordinary durable Sessions with parent/root lineage and one terminal settlement.
- In-process implementation reload is REJECTED.

### Provider requests and uncertainty

- Exact request reconstruction composes Session restore, immutable artifacts, effective Lock, prompt compiler, provider request/invoker, and non-request exchange metadata. Captured request bytes and retained `ProviderExchangeV2.request` are forbidden reconstruction inputs.
- Each effective field cites requested/default/adapter source.
- Model-visible contributions are ordered and named.
- Cache observations are facts, not validity claims. External-world claims need independent observation. Unknown remains unknown; timeout is never a pass.

## 7. Primitive and seam audit

No primitive verdict creates a module or seam.

| Candidate | Verdict |
| --- | --- |
| Definition | vocabulary; existing harness compiler and `EffectiveHarnessLock` own it |
| Entity | shallow behavior generalization; rejected |
| Event | vocabulary; domain event owners retain validation |
| Projection | pure-fold vocabulary/pattern; registry is hypothesis |
| Capability | vocabulary/interface role; universal owner is shallow |
| Operation | vocabulary plus terminal-outcome law only; universal pipeline rejected |
| Resource | identity vocabulary; universal behavior owner rejected |
| Scope/binding protocol | vocabulary; compiler/Lock/runtime registries own behavior |
| Policy/authority protocol | vocabulary; domain authorities remain monotonic |
| Lease/lifecycle protocol | vocabulary; each resource owner proves cleanup |
| Message/delivery protocol | vocabulary; timing semantics remain Session/coordination-owned |

Existing deep seams: public Session events/restore; provider exchange/adapters; permission authority; Definition compiler/effective Lock. Execution-world, projection-registry, annotation, child, and workflow seams remain conditional and each has a design-it-twice predecessor.

## 8. Evidence, review, and approval contract

### Support ladder

For the exact stated claim: hypothesis < source-supported < deterministically characterized < independently reviewed < owner-approved where policy is involved. Narrow evidence never widens itself.

### Instrument choice

1. Authoritative deterministic mapping → conformance/differential comparison; no statistics.
2. Stochastic treatment/population rate → pre-sized estimate with interval.
3. Safety/compatibility invariant → one assertion/vertical case per learned behavior.
4. Interface existence → one smoke.
5. Capacity → saturation curve.

Split questions that need two instruments.

### Gate charter

Before a gate runs, record claim/denominator, discrimination, independent oracle, cost, retry transition, stop/fallback, and nonclaims. One raw provenance block per ticket/session. Hashes prove identity only.

Exactly four artifact roles bind each stage: (1) charter before outcomes; (2) one append-only raw store, including the single provenance block per session; (3) one decision artifact, which may contain the charter as this specification does; and (4) one independent review artifact. No fifth artifact, second ledger, manifest, digest file, or summary authority. If provenance-only outputs outnumber decision evidence, stop and cut them.

### Reds and retries

| Red | Response |
| --- | --- |
| Product | Record minimal reproduction; PROC-DEFECT; never patch in this campaign. |
| Contract | Stop against laws; resolve via owner/amendment, not tests. |
| Evidence | Repair harness/oracle only. |
| Infrastructure | Record blocker; use cheapest local discriminator. |
| Stale | Invalidate only citing decisions/reviews; rebind once. |
| Unknown | Run one discriminating diagnostic; do not relabel. |

A rerun requires new bytes, oracle, environment, or mechanism diagnosis. Maximum three attempts per typed defect. Timeout remains UNKNOWN.

### Review and owner authority

- Reviewer inputs, in order: bounded charter; append-only raw store; candidate artifact path; pinned heads and candidate digest. The reviewer recomputes denominators and samples anchors before reading conclusions; it does not receive or trust the author's summary and never edits candidate/raw bytes.
- Review is exact-artifact bound. P0/P1 block. P2 is fixed or accepted only by Kyle with finding, scope, rationale, and revisit condition. P3 never blocks.
- H requires both a no-context dry read and one raw-first independent review. One material renewal is permitted after corrected bytes; unchanged bytes get no repeat.
- Ask-tool auto-selection counts as Kyle's answer.
- Evidence-only packets at existing owners need no prior Kyle approval. Retaining a behavior lock requires all six named fields to pass.
- Guarded conditional policy: normative contradiction plus evidence may admit an internal no-surface packet at an existing owner. Every new seam, public surface, compatibility deletion, or freeze change returns to Kyle.

## 9. Freeze and public/client order

Phase 20 freezes new schema families under `contracts/kernel/schemas/` except the three named Phase 20 families `bb.e4.lane_manifest.v1`, `bb.e4.lane_lock.v1`, and `bb.contract_tiers.v1`; freezes new SDK packages, lane kinds/lanes, ledgers, scorecards, and campaign snapshot forms. Constraint tightening of an existing schema is permitted only when a plan packet requires it and `scripts/check_phase20_freeze.py` is updated in the same commit with the packet ID and expected post-change content hash under `SPEC_AMENDMENTS.md` AM10. The first tranche has freeze impact `none`, no public schema/API change, no new seam, and no compatibility deletion.

A later public fact requires a named failing consumer/correctness case and Kyle sign-off in `SPEC_AMENDMENTS.md`. Order is strict:

1. amendment and exact allowlist/hash;
2. contract/schema;
3. canonical generator;
4. Python and TypeScript SDKs in parallel;
5. `tui_skeleton` and separate BreadBoard TUI in parallel;
6. installed composition;
7. E4 conformance as consumer, never product truth.

Migration must name old/new producers and consumers, bounded window, evidence, rollback, and zero-consumer deletion proof. No compatibility shim or dual write is assumed.

## 10. First implementation tranche

Exactly six unconditional packets. Recommended execution: FT-01 first; FT-04 may use an isolated lane while FT-01 exclusively owns the installed-product slot; FT-02/03/05 then run in isolated ENGINE lanes; FT-06 runs after all five focused reports; the evidence gate is serial. Global permanent-lock cap: three for seven learned behaviors—L1 journal completeness, L2 installed permission composition, L3 request-byte equality. FT-02/04/05/06 retain zero new permanent tests.

### Complete packet-contract incorporation

The FT-01 runbook below is complete and runnable from this specification alone; its linked F section is audit provenance, not an execution prerequisite. For FT-02 through FT-06, the exact reviewed sections in `05_FIRST_TRANCHE_PACKET_SET.md` are normative packet contracts and must be read before those later packets. Workstream I copies that reviewed artifact into the committed specification's `evidence/` directory and rewrites links relative to the final spec. Any synopsis/link mismatch is stale and stops the affected later packet.

Every packet therefore includes the plan-section-17 fields: promotion basis/substrate; module/seam record; dependency/test strategy; repositories and pinned heads; first provenance action; inputs; exact owners/consumers/files; scope/non-goals; public/freeze impact; untouched-base order and clean cutover; generated/contracts/fixtures/docs; proof and broader gate; risk/permissions/blast radius/budget/retry/kill; rollback/review/approval/exact-artifact binding; maximum support claim; and unlocked branch. Common invariants: no production edit, new seam, public/schema/API change, compatibility deletion, SDK package, lane, ledger, scoreboard, or retained temporary extraction framework. ENGINE/TUI heads are those in section 1. Reports write under `docs/plans/dsh_donor_campaign/evidence/first_tranche/`; raw evidence writes only under its packet's sibling `raw/FT-0N/`; temporary files live under one fresh `/private/tmp/breadboard-dsh-FT-0N-<run-id>/` root and are removed after capture.

### FT-01 — recovery truth and admission differential

- **Promotion/substrate:** C's held journal divergence and admission/retry contract gap; advances durable-event truth and exact recovery. No module. Existing `SessionService.send_input`, `Session.restore`, `/v1 session.events`, and installed client binding remain the seam.
- **Execution inputs:** this specification; the exact ENGINE/TUI heads in section 1; a freshly resolved installed `bb`; ENGINE `tests/product/runtime/test_session_kernel.py` and `tests/test_cli_bridge_service_prewarm.py`; TUI `packages/coding-agent/src/breadboard/e4-agent-stream.test.ts` and `packages/coding-agent/test/breadboard/installed-product-journey.py`. Sections 5–9 above contain the controlling laws, oracle, red, retry, review, and freeze policy. `02_FAULT_BOUNDARY_EVIDENCE.md` and its raw store, `04_NORMATIVE_SEMANTIC_LAWS.md`, and the F packet are audit provenance copied beside the final spec; FT-01 execution does not depend on reading or interpreting them.
- **Fresh identity record:** define `run_id` once as UTC basic timestamp plus the FT-01 executor PID; then the fresh root is exactly `/private/tmp/breadboard-dsh-FT-01-{run_id}` and the harness path is exactly `{fresh-root}/ft01_harness.py`. Before outcomes, write `raw/FT-01/provenance.json` once with ENGINE/TUI paths, exact heads and empty status, Python path/version, installed product/version/distribution/backend/TUI-build identities, proof the installed backend commit equals the pinned ENGINE head and the installed TUI-build commit equals the pinned TUI head, installed `bb` path, harness path/content identity, fixture ID, exact argv/environment-key names, run ID/start time, extraction paths/method, temp root/listener, and processes already owned at that point. Build or resolve the installed composition from the pinned roots before the case; if either identity is unequal, record a non-started `INFRASTRUCTURE_RED` attempt and run no case command. Later/replacement process identities belong in the case records and final cleanup record; provenance is never rewritten. No secret values.
- **Allowed writes:** `evidence/first_tranche/FT-01_RECOVERY_TRUTH.md`; append-only `evidence/first_tranche/raw/FT-01/{provenance.json,replacement-attempts.jsonl,permission-attempts.jsonl,cleanup.json,lock-audit.json}`; the disposable `{fresh-root}/ft01_harness.py`; and, only after a six-holds PASS, focused L1/L2 additions in the existing recovery/session-runtime test suites, including the named separate-TUI installed-product test for L2. Each case-attempt record has `record_version`, `run_id`, integer `attempt` in 1–3, `case`, `session_id`, `input_id`, `client_message_id`, `turn_ids`, `event_ids`, `admission_ids`, `client_receipt_class`, `process_authority`, `observations`, `primary_classification`, and `cleanup_ref`. Each retry appends one new JSONL record; no prior attempt is overwritten. No production TUI file, production ENGINE file, contract, generator, SDK, public schema, or compatibility-path file may change; a product defect routes to PROC-DEFECT and a public need routes to PROC-AMEND.
- **Writable lock worktrees:** the pinned ENGINE/TUI roots above remain read-only evidence baselines. Before writing a qualifying L1 or L2 test, create a clean writable sibling worktree from the corresponding pinned head on disposable branch `dsh-ft01-l1-{run_id}` or `dsh-ft01-l2-{run_id}` at `/Users/kylemccleary/projects/breadboard-dsh-ft01-l1-{run_id}` or `/Users/kylemccleary/projects/breadboard-tui-dsh-ft01-l2-{run_id}`. `lock-audit.json.target_path` must name an existing recovery/session-runtime test-suite path in that writable worktree. Retained locks keep the test-only worktree/branch for later review and landing; failed or not-proposed locks remove it. The packet never writes either pinned baseline or any production file.
- **Harness command contract:** before observation, write `{fresh-root}/ft01_harness.py` to implement exactly the procedures and packet-local record contract below, record its content identity, then invoke `/opt/homebrew/bin/python3.11 {fresh-root}/ft01_harness.py --bb {resolved-installed-bb} --engine-root /Users/kylemccleary/projects/breadboard-dsh-baseline-b3cacc73 --tui-root /Users/kylemccleary/projects/breadboard-tui-dsh-baseline-73d6e6f5 --temp-root {fresh-root} --output {committed-spec-root}/docs/plans/dsh_donor_campaign/evidence/first_tranche/raw/FT-01 --case replacement-before-submit`, then the same command with `--case permission-after-dispatch`, then `/opt/homebrew/bin/python3.11 {fresh-root}/ft01_harness.py --validate-before-cleanup {output}`, then `/opt/homebrew/bin/python3.11 {fresh-root}/ft01_harness.py --finalize-cleanup {output}`. Braced values are bound once in `provenance.json`; none are policy choices. The harness may compose existing journey utilities but may not execute another S2 cell, contact a provider/network, read credentials, or add production instrumentation.

#### FT-01 fixed fixture, record format, and deadlines

Both cases use the installed synthetic route `cli_mock/reference`, loopback-only networking, no credentials, and exact UTF-8 prompts. Case A owns the initially empty `{fresh-root}/replacement-before-submit/`; case B owns the initially empty `{fresh-root}/permission-after-dispatch/`; the harness remains at `{fresh-root}/ft01_harness.py` across both invocations. Case A uses approval mode `yolo`, pre-turn prompt `Create and validate the deterministic bubble sort fixture.`, recovery prompt `Prove the recovered engine can execute the deterministic validation.`, and client message ID `ft01-recovery-{run_id}`. Case B uses approval mode `write`, prompt `Create and validate the deterministic bubble sort fixture.`, client message ID `ft01-permission-{run_id}`, and must reach `BreadBoard permission request · run_shell`; `write` may complete before the gated `run_shell`, which is why file existence is not the shell oracle. Any model route, prompt byte, approval mode, client message ID, or expected permission label difference is stale/evidence red, not an alternate fixture.

Packet wall budget is 360 seconds. Per case: installed readiness/authority ≤40s; case-A synthetic pre-turn ≤30s, old-process death/listener closure ≤10s, first submission plus replacement identity ≤45s, optional unchanged retry terminal ≤45s; case-B permission prompt ≤45s and rejection terminal ≤30s; extraction ≤15s/case; process and case-subdirectory cleanup ≤10s/case; final harness/root cleanup ≤10s. The harness enforces each deadline. A deadline expiry records the named step and `UNKNOWN`, stops that case, and never triggers unchanged retry or a longer wait. Case B starts only after case A's owned processes are dead and its case subdirectory is removed.

The three JSON records use UTF-8, sorted keys, compact JSON values, and one trailing newline. `replacement-attempts.jsonl` and `permission-attempts.jsonl` are append-only streams with one compact sorted-key JSON object and newline per attempt; attempt numbers are sequential integers 1–3 and prior records are immutable. `record_version` is integer `1`, not a product schema. Every replacement/permission attempt record requires `record_version`, `run_id`, `attempt`, `case`, boolean `started`, `primary_classification`, nullable `preflight_blocker`, and relative `cleanup_ref`. When `started=true`, it additionally requires nonempty `session_id` and `client_message_id`; `input_id` as a nonempty string only after a SubmitReceipt supplies it and otherwise `null`; `turn_ids`, `event_ids`, and `admission_ids` as string arrays; `client_receipt_class` from `accepted`, `typed-pre-admission-rejection`, `ambiguous-transport`, `duplicate-or-conflict`, `typed-reject`, `cancelled`, `absent-public-receipt`, or `unknown`; a `process_authority` object; and an `observations` object. When preflight fails before the case starts, `started=false`, identifiers are `null`/empty arrays, `preflight_blocker` contains one nonempty typed reason, and no process termination is attempted.

`primary_classification` is closed to `STALE_RED`, `INFRASTRUCTURE_RED`, `EVIDENCE_RED`, `CONTRACT_RED`, `KILLED`, `UNKNOWN`, `PRODUCT_RED`, `KNOWN_DIVERGENCE`, `COHERENT`, or `UNVERIFIED`. A `started=false` preflight record may use only the first six values; behavior classifications require `started=true`. Any other value fails validation.

For `started=true`, `process_authority` requires `old` and `new` objects. Each has `pid` as a positive integer or `null`, and `start_token`, `engine_instance`, `boot`, and `launch` as nonempty strings or `null`; all five are nonnull when that authority was authenticated and all are null when no complete authenticated tuple exists. It also requires `listener` with nonempty `host`, integer port 1–65535, and boolean `open_before`/`open_after`; and `active_authority_before`/`active_authority_after` as nonempty strings or `null`. Case A requires `old` nonnull. Its `new` is nonnull after authenticated replacement readiness; all-null is valid only with (a) `UNKNOWN` after the readiness deadline, (b) `COHERENT` after typed pre-admission rejection with zero durable append, (c) `PRODUCT_RED` after accepted/success/other terminal behavior with no authenticated authority, or (d) `EVIDENCE_RED` when pre-dispatch synchronization invalidates the cell. Case B requires `old` nonnull and permits `new` all-null when no replacement occurred. Every started branch runs authenticated cleanup; any later authenticated process is added to `cleanup.json`.

For either case to classify `COHERENT`, the old authority must be authenticated: its PID, start token, engine instance, boot identity, and launch identity are all nonnull and match the owned process. Case A may dispatch only after that exact owned authority is proven dead and absent. An incomplete or mismatched old tuple is `INFRASTRUCTURE_RED`; the harness does not signal that PID.

When `started=true`, each replacement attempt record's `observations` requires `logical_turn_ids` and `durable_turn_ids` string arrays; `logical_event_order_by_turn` whose keys equal exactly `logical_turn_ids` and values are string arrays; `durable_terminal_by_turn` whose keys equal exactly `durable_turn_ids` and values are strings; integer `accepted_count_for_client_message_id`; boolean `retry_performed`; boolean or null `retry_bytes_equal` (true when retried, null otherwise); booleans `cell_valid`, `typed_pre_admission_rejection`, `journal_set_equal`, `journal_order_equal`, `duplicate_or_conflict`, `terminal_mismatch`, `stale_authority_reachable`, and `workspace_finalized`; and `artifacts` as a sorted array of relative finalization paths. Each permission attempt record's `observations` requires `event_types` in observed order; integer `approval_requested_count`, integer `approval_resolved_count`, integer `approval_resolved_reject_count`, boolean `approval_pair_matches`; nonnegative integer `terminal_cancelled_turn_count`; booleans `request_seen`, `waiting_seen`, `client_decision_submitted`, `resumed`, `typed_pre_admission_rejection`, `duplicate_or_conflict`, `terminal_resolution_committed`, `client_quiescent`, `process_quiescent`, `stale_authority_reachable`, and `workspace_finalized`; nullable `permission_id`; string or null `terminal_turn_status`; nonnegative integer `gated_result_count`; and `artifacts`. Together with top-level `client_receipt_class`, these fields encode every Case B `COHERENT` predicate for each attempt; validation requires all three approval counts and `terminal_cancelled_turn_count` equal one, a matching pair, and terminal status `cancelled`.

For permission `COHERENT`, validation additionally requires `request_seen`, `waiting_seen`, `client_decision_submitted`, `resumed`, `terminal_resolution_committed`, `client_quiescent`, and `process_quiescent` true; `gated_result_count=0`; and `client_receipt_class` equal to `typed-reject`, `cancelled`, or `absent-public-receipt`. The absent-receipt class is valid only with the two quiescence booleans true. Unknown keys or any other value combination fail closed.

`provenance.json` requires `record_version:1`, nonempty `run_id`, `roots` with absolute string paths for engine/TUI/spec/temp/output, `heads` with the two exact 40-hex revisions, `clean` booleans both true, and `python` path/version strings. Its `installed` object requires resolved executable path, product version, distribution ID, backend commit equal to `heads.engine`, TUI-build commit equal to `heads.tui`, `engine_head_exact:true`, `tui_head_exact:true`, and lowercase 64-hex SHA-256 values for the executable bytes, distribution artifact, runtime bundle, and TUI build artifact; a label without its content identity is invalid. Its `harness` object requires path, harness content SHA-256, fixture ID, fixture-route/config SHA-256, argv string array, and environment-key-name string array. It also requires `started_at_utc`, extraction-method string array, and `initial_owned_processes` array of objects with positive integer PID, nonempty start token, role, and case. `cleanup.json` is written once after both cases and requires `record_version:1`, `run_id`, `completed_at_utc`, a `cases` object with both case names, and for each: every owned PID/start-token pair including replacements, booleans `all_dead`, `listener_closed`, `authority_absent`, `case_root_removed`, and nonnegative `elapsed_seconds`; it also requires final booleans `harness_removed`, `fresh_root_removed`, and `output_preserved`. Any false cleanup boolean is `INFRASTRUCTURE_RED`.
The top-level `listener` object is required and closed: `host` is a nonempty string and `port` is an integer from 1 through 65535. It names the initial loopback listener before any case outcome.

`lock-audit.json` requires `record_version:1`, `run_id`, and keys `L1` and `L2`. Each lock has `target_path`; `disposition` from `retained`, `deleted`, `not-proposed`; and exactly the six holds, each an object with `status` `PASS` or `FAIL` plus a nonempty string-array `evidence`. `retained` is valid only when all six statuses are `PASS`; otherwise disposition is `deleted` or `not-proposed`. `--validate-before-cleanup` validates provenance/case/lock records, cross-checks every `run_id`, case/process reference, pinned head, expected output path, and lock target, and fails closed on unknown keys; it does not require or write `cleanup.json`. `--finalize-cleanup` performs the authenticated final deletion, writes `cleanup.json` to the durable output only after observing the final state, validates that record in memory against the inline contract, and exits nonzero on any false flag.

#### FT-01 case A — replacement before submit

1. Start the installed `bb` with provider-free loopback config inside the fresh root; create one Session and complete one synthetic turn.
2. Prove quiescence, record the engine PID/start token/listener/authority, kill exactly that owned idle process, and require old-PID dead + listener closed + active authority absent.
3. Begin the fixed recovery submission through the installed TUI while those three conditions still hold. The installed client is the replacement-launch actor; do not wait for a replacement-ready signal before dispatch. If listener or authority reappears before dispatch begins, set `cell_valid=false`, classify `EVIDENCE_RED`, and do not count the attempt.
4. After dispatch begins, identify replacement readiness only by listener reopened + a new authenticated PID/start token + new engine-instance/boot/launch identity. Record the exact structured submission/client message ID, response class, and authority timeline.
5. An ambiguous first attempt means exactly: TUI `isAmbiguousSubmitFailure` accepts either `CanonicalE4ClientError` or `LifecycleE4ClientError` in its timeout or HTTP-status-0/transport class, no typed pre-admission rejection exists, and no durable accepted disposition for the exact client message ID is visible when the 45-second first-attempt deadline ends. Only then use the existing unchanged-manual-retry path with byte-identical structured input and client message ID; never auto-retry or change text. A still-pending call at the deadline is `UNKNOWN`, not ambiguous; any other first result is terminal for this cell.
6. Extract independently: logical Session events through the public reader; durable owned submissions/turns directly from the persistence owner; client receipts/errors; transcript; process/authority timeline. The public event reader must not enumerate durable owned state.

The replacement comparator sorts both sources by stable turn/input identity, checks set/cardinality equality, then ordered event sequence for each durable owned turn. It reports separately: journal completeness; accepted ordinary-turn count for the unchanged identity (0 or 1 only); duplicate/conflict; retry identity equality; client receipt class. `COHERENT` requires every durable owned turn in logical replay, no extra replay turn, at most one accepted recovered turn, a terminal receipt classification, and no unexplained terminal mismatch. Generic HTTP text is observation only.

#### FT-01 case B — permission after dispatch/before receipt

1. Start one provider-free installed Session whose deterministic next action requests gated `run_shell`; wait for the exact permission prompt.
2. Send Escape once after the request is displayed and before a receipt is shown.
3. Extract public/logical events, durable turn state, transcript/tool results, client receipt/ready state, exit, and process/authority state from owners independent of the UI renderer.
4. `COHERENT` requires exactly one `approval.requested` and one matching `approval.resolved` reject, exactly one terminal cancelled owned turn, `terminal_resolution_committed=true`, zero result for the gated call, quiescent client/process state, and `client_receipt_class` equal to either typed reject/cancel or `absent-public-receipt` with ready/quiescent state. Success, pending, a second error, or absence without quiescence is product red. Pre-permission allowed results do not count as gated execution.

The FT-01 executor owns only the fresh root and processes it starts. It may terminate only a PID whose PID and start token match the recorded owned process. After case A, graceful exit/TERM then authenticated hard kill has a 10-second deadline; prove every case-A PID dead, listener closed, active authority absent, and remove only `{fresh-root}/replacement-before-submit/`, preserving the harness. Run case B only after that proof. Apply the same process proof to case B and remove `{fresh-root}/permission-after-dispatch/`. Then invoke the harness once as `--validate-before-cleanup {output}` and once as `--finalize-cleanup {output}`. The latter process deletes its loaded harness file and now-empty packet root, observes both paths absent, writes the single immutable `cleanup.json` outside the root, validates it in memory, and exits. A PID/token mismatch forbids kill; any false cleanup flag or nonzero finalizer exit is infrastructure red and prevents success.

L1/L2 may be retained only when their own record marks all six fields `PASS` with evidence: `observable_contract`, `plausible_bug`, `independent_oracle`, `stable_interface_seam`, `deterministic_and_isolated`, `not_covered_by_existing_test`. Any failed field means no lock and no substitute test. One lock per learned behavior; global cap remains three including L3.


Risk high; wall budget ≤6 minutes total for the initial two cases. Maximum three attempts **per typed defect in one cell**, never three routine runs. Retry requires changed bytes, oracle, environment, or a written mechanism diagnosis naming the transition. No network/provider/secrets. Blast radius is the fresh root, raw/report paths, and conditionally the two named test files.

Evaluate the table top-to-bottom and record exactly one primary disposition per case. A prerequisite/red row outranks a behavior row; product mismatch outranks journal divergence; `UNVERIFIED` outranks `COHERENT`. The lock audit is secondary and never changes the primary disposition.

| Terminal observation | Disposition / write | Next state |
| --- | --- | --- |
| Head/artifact/owner/contract differs | `STALE_RED` | PROC-STALE; invalidate only citing case/review. |
| Process identity mismatch or cleanup not authenticated/complete | `INFRASTRUCTURE_RED`; do not kill unowned PID; record cleanup state | Stop packet; no success claim or installed-slot release until safe state is proven. |
| Harness/oracle cannot distinguish sources or cell synchronization is wrong | `EVIDENCE_RED`; repair only harness/oracle within transition budget | FT-01 remains open; unchanged rerun prohibited. |
| Contract intent is missing/contradictory | `CONTRACT_RED` against D/E; no test | Block affected branch; PROC-AMEND/owner decision. |
| A public field, production instrumentation, new seam, extra matrix cell, provider/network, or secret is required | Set `primary_classification=KILLED`; write the attempted requirement and observed blocker to the case record/report; write no product bytes | Preserve bounded evidence; public need→PROC-AMEND, product need→PROC-DEFECT; independent packets may proceed only if their prerequisites are unaffected. |
| Timeout after bounded configured attempt | `UNKNOWN`, never infrastructure/pass | Diagnose once; after third typed attempt retain strongest claim and close/block exact branch. |
| Duplicate acceptance, conflicting durable identity, wrong retry reuse, accepted/success/other terminal behavior with no authenticated authority, or unexplained receipt/terminal mismatch | Set `primary_classification=PRODUCT_RED`; write minimal raw reproduction; PROC-DEFECT; no repair here | FT-01 report may close with bounded defect; RT branch closed; independent tranche packets may proceed. |
| Permission count/identity/terminal/gated-result/receipt invariant differs | `PRODUCT_RED`; minimal raw reproduction; PROC-DEFECT; no decoder/permission patch | FT-01 report may close with bounded defect; independent packets may proceed. |
| Durable owned turn is absent/extra in logical replay, with no higher-precedence mismatch | `KNOWN_DIVERGENCE`; exact diff; open existing-owner `RT-REPAIR` under guarded policy | FT-01 complete after report; `RT-REPLAY` stays closed until repair and FT-04. |
| Typed pre-admission rejection, zero durable append, and no replacement authority | `COHERENT` explicit rejection for case A; no retry | Record case A and proceed to case B; no receipt/public-surface inference. |
| Retry identity remains hidden but admission is single and all other facts are coherent | Mark retry receipt `UNVERIFIED`; no retry-identity/public claim | FT-01 complete; later public receipt needs named consumer and PROC-AMEND. |
| Both cases meet their complete `COHERENT` rules | Write report/raw | FT-01 complete; FT-04 and isolated FT-02/03/05 may proceed; FT-06 remains closed until all five reports. |

After the primary disposition, audit L1/L2 independently. Any failed six-holds field deletes that candidate lock and retains only the characterization; it does not alter the primary result or spend the lock budget elsewhere automatically.

Review binds the report plus five raw artifacts. Reviewer checks oracle independence, identity/cardinality comparator, exact two-cell scope, authenticated cleanup, six-holds records, and no generic-HTTP contract. Rollback deletes temporary harness and any failing candidate lock. Maximum claim is the exact classification for 2/16 cells plus only qualifying L1/L2.

Invalidation inputs: either pinned head or clean state; installed product/distribution/backend/TUI build identity or exact head equality; Python; F/E/C artifact identity; cited law; owner symbol/path; public reader/persistence extraction method; fixture/harness bytes or argv; process/listener authority method; comparator; expected invariant; output schema/path; packet budget/retry rule; freeze/amendment; or reviewer contract.

### FT-02 — four-case durable reconfiguration

- **Cases:** invalid edit during in-flight turn; reconfigure mid-turn; restart durability; provider swap mid-Session.
- **Owners:** `SessionService.durable_reconfigure`; `SessionRunner.transition_product_session`; `Session.reconfigure`; `EffectiveHarnessLock`.
- **Writes/proof:** one report/raw table with a terminal reject/rollback/durable-apply classification per case and exact old/new Lock-compatible identity, reason, effective sequence. Temporary fixtures deleted; no retained test.
- **Budget/stop/branch:** medium; ≤4 minutes, three attempts/case. Stop on public fact or implementation instrumentation. Existing-owner gap→GEN-INTERNAL; coordination need→DIT-GENERATION; reload remains rejected.
- **Complete contract:** `05_FIRST_TRANCHE_PACKET_SET.md#Packet FT-02 — generation and durable-reconfiguration characterization`; ENGINE head from section 1; output `evidence/first_tranche/FT-02_RECONFIGURATION.md` plus `raw/FT-02/`; zero permanent tests; no module/public/freeze/generated/compatibility change; temporary fixtures deleted; one exact-artifact review; rollback is deletion of temporary/report additions; maximum claim is current behavior for 4/4 cases and which later generation branch is evidence-admitted.

### FT-03 — exact provider-request reconstruction

- **Fixture:** one secret-free request with ordered prompt sections, tools, history, defaulted field, and adapter override. A fake adapter writes held-out bytes only after dispatch.
- **Allowed inputs:** logical Session events, immutable artifacts, exact Lock, prompt resources/compiler defaults, adapter config, and non-request exchange correlation/route/generation metadata.
- **Forbidden:** retained `ProviderRequest`, `ProviderExchangeV2.request`, capture bytes/digest, expected provenance.
- **Writes/proof:** one report, separate capture/reconstruction raw files, and L3 only if direct canonical-byte equality and all six holds pass.
- **Budget/stop/branch:** medium; ≤2 minutes; three attempts. Kill on secret, nondeterminism, forbidden input, or public-field need. Exact→RQ-PASS; first internal missing fact→RQ-INTERNAL; ablation remains a later packet.
- **Complete contract:** `05_FIRST_TRANCHE_PACKET_SET.md#Packet FT-03 — exact provider-request reconstruction`; ENGINE head from section 1; output `evidence/first_tranche/FT-03_REQUEST_RECONSTRUCTION.md` plus separate `raw/FT-03/capture` and `raw/FT-03/reconstruction`; no module/public/freeze/generated/compatibility change; temporary fixture deleted; L3 only after six holds; one exact-artifact review; maximum claim is one exact reconstruction or the first irreducible missing fact.

### FT-04 — unknown-required-event client parity

- **Consumers:** Python SDK validator, public TS SDK validator, canonical TS runtime decoder, separate TUI engine port.
- **Proof:** one semantically identical required event mapped to each existing wire shape; 4/4 typed stops, zero silent accepts. No retained test.
- **Budget/stop/branch:** low; ≤2 minutes; three evidence attempts. Any silent accept→PROC-DEFECT; 4/4→compatibility clear for required-event evolution. No schema, registry, fallback, or decoder patch.
- **Complete contract:** `05_FIRST_TRANCHE_PACKET_SET.md#Packet FT-04 — unknown-required-event cross-client check`; ENGINE/TUI heads from section 1; output `evidence/first_tranche/FT-04_UNKNOWN_REQUIRED_EVENT.md` plus four mapped `raw/FT-04/` outcomes; zero retained tests; no compatibility/public/freeze/generated change; temporary adapters deleted; one exact-artifact review; maximum claim is 4/4 typed stops or exact divergent consumers, not identical wire formats.

### FT-05 — existing-definition expressiveness

- **Cases:** independently mutate the approved base Definition for Code Mode, immutable annotation sidecar, and durable child lineage.
- **Owners:** `compile_harness_definition`; harness model/validation; v1 and compatible-v2 schemas.
- **Proof:** 3/3 terminal EXPRESSIBLE/PARTIAL/INEXPRESSIBLE verdicts; accepted authored facts, materialized Lock facts, first missing fact. Compile acceptance alone never proves runtime semantics.
- **Budget/stop/branch:** low; ≤2 minutes; three attempts. Any need for schema/runtime change stops. Results gate annotation, child, and research nodes; no retained test.
- **Complete contract:** `05_FIRST_TRANCHE_PACKET_SET.md#Packet FT-05 — harness-definition expressiveness probe`; ENGINE head from section 1; output `evidence/first_tranche/FT-05_EXPRESSIVENESS.md` plus four input/output identities under `raw/FT-05/`; zero retained tests; no schema/runtime/public/freeze/generated change; temporary fixtures deleted; one exact-artifact review; maximum claims are only the three exact compile/Lock verdicts.

### FT-06 — exact interface inventory report

- **Scope:** owners, schemas, event kinds, projections, SDK exports, TUI consumers, compatibility surfaces, deletion conditions, exact `durable_reconfigure` paths, named `.omp` consumer surfaces.
- **Method/proof:** authoritative registries plus exact declaration/token enumeration, methods and denominators, blind spots, one sampled recall check, and explained delta against B.
- **Writes:** one Markdown report and raw command output; extraction code deleted. Never a registry, ledger, scoreboard, or CI gate.
- **Budget/stop/branch:** medium; ≤3 minutes; one regeneration plus at most one stale rerun. Kill on second authority or new forms. Exact inventory sizes all downstream packets.
- **Complete contract:** `05_FIRST_TRANCHE_PACKET_SET.md#Packet FT-06 — generated interface inventory report`; ENGINE/TUI heads from section 1; output `evidence/first_tranche/FT-06_INTERFACE_INVENTORY.md` plus cited `raw/FT-06/` counts; no registry/ledger/scoreboard/generated/public/freeze change; extraction code deleted; one exact-artifact review; maximum claim is the pinned interface inventory and blind spots, never behavior.

## 11. Promoted DAG and conditional work

The machine prototype in reviewed G evidence has 50 acyclic nodes, six first-tranche packets, all 623 rows routed, phases 0–12, and zero unconditional new seams.

First-tranche completion reaches one evidence gate. Only observed predicates open later families:

- `EVIDENCE-GATE` waits for FT-06, and FT-06 waits for FT-01 + FT-02 + FT-03 + FT-04 + FT-05.
- `KNOWN_DIVERGENCE` on the FT-01 journal branch opens `RT-REPAIR`; a coherent journal result records that repair as evidence-backed `NOT-TAKEN` and may join FT-04 directly at `RT-REPLAY`. `PRODUCT_RED` or any other terminal defect outcome stops at PROC-DEFECT: it opens neither `RT-REPAIR` nor `RT-REPLAY`. A completed repair plus FT-04, or a coherent result plus FT-04, is required for `RT-REPLAY`.
- `RT-REPLAY + FT-06 → PROJ-CHAR → DIT-PROJECTION`; only approved DIT may open `PROJ-IMPLEMENT`.
- FT-03 exact branch (`RQ-PASS`) or repaired branch (`RQ-INTERNAL → RQ-RECONSTRUCTED`) may feed the `ANY` join `RQ-ABLATION`.
- `FT-02 + FT-06 → DIT-GENERATION`. After FT-02 evidence, `GEN-INTERNAL` must terminate as a current-owner repair or evidence-backed `NOT-TAKEN`. DIT-GENERATION must compare three designs when activation is needed or terminate `NOT-TAKEN`; it always drives `GEN-ACTIVATION` to approved implementation or evidence-backed `NOT-TAKEN`.
- `RT-REPLAY → CP-CHAR`; `RT-REPLAY + FT-06 → EW-CHAR`; `RT-REPLAY + FT-05 → ANN-CHAR` and `CH-CHAR`.
- Characterization precedes its DIT and internal pilot. `CH-PILOT → WF-CHAR → DIT-WORKFLOW → WF-IMPLEMENT`. Distributed-teams research additionally requires both `CH-PILOT` and `WF-CHAR`.
- Any public feature follows `PUBLIC-AMEND → CONTRACT-SCHEMA → GENERATE-BINDINGS → SDK-PY/SDK-TS → TUI-SKELETON/TUI-BB → INSTALLED-COMPOSITION → E4-CONFORMANCE`.
- `RQ-ABLATION + PROJ-IMPLEMENT + EW-PILOT + E4-CONFORMANCE → SUBSTRATE-COMMAND`; the seven-piece audit additionally requires `ANN-INTERNAL + WF-IMPLEMENT + CP-IMPLEMENT` and terminal `GEN-INTERNAL` and `GEN-ACTIVATION` outcomes.

Every DIT gate requires a common caller, at least two real adapters/foundations, deletion payoff, three interface designs, and Kyle's seam approval. Current alternatives favor feature-local/existing-owner composition; standalone services are last, not default.

### Explicit fog

Execution worlds beyond characterization, generic Capability Runtime, universal Operation, projection registry, annotation owner, durable children, workflows, compaction, advanced clients, Code Mode, dynamic packages, and distributed teams remain unpromoted. Each has a named characterization/metric and kill threshold in G. Negative evidence closes the branch; it never creates a product fallback.

## 12. Cross-repository packet boundaries

| Boundary | Authority | Reviewable consumer |
| --- | --- | --- |
| Kernel/runtime | Session admission, logical events, restore, Lock | focused runtime/replay characterization |
| Provider adapters | provider request/exchange/invoker and capture seam | exact fake-adapter reconstruction |
| Public contract/generator | contract/schema then generated bindings | Python and TS SDKs |
| SDKs | typed public operation/event behavior | `tui_skeleton`, separate TUI, installed client |
| TUI clients | projections and interaction only | installed-product scenario; never authority |
| Installed product | exact composed identities and observed behavior | packet report/raw result |
| E4 | internal conformance over product facts | invariant assessment only; never product truth |

Packets are split whenever more than one product decision is required. Generated order, compatibility windows, freeze amendments, stale invalidation, reviews, and rollback are separate nodes.

## 13. Operating procedures

### Start or resume

1. Read goal prompt, plan sections 0–7 and 18, doctrine, charter, freeze, and active workstream.
2. Verify ENGINE/TUI heads and clean state; run plan integrity.
3. Resume the sole in-progress child or claim earliest ready; one child/session.
4. Open one provenance block and refresh control panel/tripwire/charter.

### Execute and classify

1. Run the packet's narrow untouched-base discriminator.
2. Record raw bytes and one terminal classification per predeclared case.
3. Classify every red before action; retry only on a real transition.
4. Remove temporary harness bytes after report capture.
5. Obtain exact-artifact review required by the packet; fix or route findings.

### Close

1. Link each checklist line to an artifact heading.
2. Put digest and pinned heads only in the resolution log/review verdict.
3. Run plan integrity.
4. Append Beads notes, close child, and update map decision.
5. Advance only the dependency graph's next admitted node.

### Stale, defect, amendment, and opportunity

- **PROC-STALE:** identify exact changed input; invalidate only citing nodes/reviews; rerun smallest affected inventory/scenario/review; record repin/hold.
- **PROC-DEFECT:** minimal reproduction/provenance; Kyle chooses outside fix, hold, or accepted divergence; campaign never patches.
- **PROC-AMEND:** record evidence; add one sharp child if needed; transfer points without lowering bar; rerun integrity.
- **PROC-PARK:** add evidence-backed opportunity to the ledger; classify and route; no production code.

## 14. Risks and stop conditions

| Risk | Control | Stop |
| --- | --- | --- |
| Stale owner/head | pinned baselines, symbol-verified evidence, PROC-STALE | never drift silently |
| Shallow DSH-shaped abstraction | module test, common caller, adapters, deletion payoff, DIT | any failed answer leaves hypothesis |
| Evidence machinery displaces outcome | four-artifact cap, one instrument/question, no blind rerun | cut provenance/assurance before decision evidence |
| Taxonomy consumes campaign | compact per-row route, packet only for admitted behavior | no packet from status alone |
| Projection/E4/client becomes truth | sole logical-event authority law | reject competing authority |
| Lock weakened by runtime mutation | exact generation pin and quiescent adoption | no unlogged/surface-equivalent drift |
| Public surface accretes | freeze chain and Kyle gate | no schema/API/SDK/lane/ledger addition outside amendment |
| Data/authority leak in evidence | secret-free fixture, fake provider, isolated roots, authenticated cleanup | kill on secret, ambient authority, or unauthenticated cleanup |
| False completion | dry read, independent review, section-19 audit | score alone never completes campaign |

## 15. Traceability index

This is an index, not a second ledger.

| Denominator / decision | Authority and route |
| --- | --- |
| 615 donor items | `00_PROVENANCE_AND_REUSE.md` + `DONOR_ITEMS.yaml` → 615 ledger rows in `01_CURRENT_STATE_DONOR_LEDGER.md` → one route each in `raw/bb-inj5.7/row-routing.json` |
| 8 seeds S1–S8 | plan section 2.6 → ledger seed table → S1/S2/S4/S7/S8 first-tranche routes; S3/S5/S6 deferred |
| 50 closure groups | `DSH_MINING_PLANNER_CONVO.md` Part X 50-group closure table → `01_CURRENT_STATE_DONOR_LEDGER.md#Coverage` group denominator → exact per-row phase/workstream records in `raw/bb-inj5.7/row-routing.json` |
| 623 total rows | B disposition denominator → G decision counts: 177 satisfied, 4 superseded, 10 rejected, 29 research-only, 398 deferred, 5 promoted |
| 54 laws | E verdict table and owner choices → packet acceptance laws and conditional gates here |
| 7 nouns + 4 protocols | E primitive audit → no new module; DIT gates for conditional seams |
| 13 DSH phases | G phase table → WS-00 through WS-12; every phase has a terminal current decision |
| 6 packets | F exact contracts → G order/branches → section 10 runnable handoff |
| 9 tickets | A source; B ledger; C faults; D evidence; E laws; F packets; G DAG; H prototype/review; I versioned spec/audit |
| Public consumers | contract/schema → generator → Python/TS SDKs → two TUIs → installed product → E4 consumer |

Reviewed decision artifacts:

| Ticket | Decision artifact | Gate result |
| --- | --- | --- |
| A `bb-inj5.1` | `00_PROVENANCE_AND_REUSE.md` | G-A PASS |
| B `bb-inj5.2` | `01_CURRENT_STATE_DONOR_LEDGER.md` | G-B PASS |
| C `bb-inj5.3` | `02_FAULT_BOUNDARY_EVIDENCE.md` | G-C PASS |
| D `bb-inj5.4` | `03_EVIDENCE_AND_APPROVAL_CONTRACT.md` | G-D PASS |
| E `bb-inj5.5` | `04_NORMATIVE_SEMANTIC_LAWS.md` | G-D/E PASS |
| F `bb-inj5.6` | `05_FIRST_TRANCHE_PACKET_SET.md` | G-F PASS |
| G `bb-inj5.7` | `06_PROMOTED_WORKSTREAM_DAG.md` | G-G PASS; guarded conditional |
| H `bb-inj5.8` | this prototype + review | pending G-H |
| I `bb-inj5.9` | committed specification + completion audit | pending G-I/G-Score |

## 16. Completion and implementation handoff

Campaign completion requires all nine tickets closed in dependency order; plan integrity 1000/1000; every blocking gate green on final bytes; 615/615 donors, 8/8 seeds, 50/50 groups routed; both fault observations classified; all laws/primitives dispositioned; every promoted new seam has DIT; first tranche freeze-clean/no-new-seam with locks ≤ learned behaviors; target substrate mapped to packets/fog; separately reviewable engine/SDK/TUI/installed/E4 boundaries; Kyle-approved prototype; committed spec/audit with freeze checker green; and no production implementation, migration, push, PR, merge, release, or publication.

### First ready implementation packet

**FT-01 is first after H and I close, the spec/audit commit exists, and G-I/G-Score pass.** Before those gates, FT-01 is named but not authorized to run. After them, it needs no additional product-design approval. Record fresh ENGINE/TUI/installed identities, clean state, interpreter, fixture/harness identity, commands, and extraction method. Run only the two declared provider-free cells under the section-10 runbook. Write only the five raw records, FT-01 report, disposable harness, and at most L1/L2 after six-holds PASS. Stop on product instrumentation, public fact, new seam, unauthenticated cleanup, or extra matrix scope. Route journal divergence to a later existing-owner repair packet; route any product mismatch through PROC-DEFECT; continue remaining evidence packets independently. FT-06 stays closed until FT-01 through FT-05 reports exist.

Approval state: H owner approval and I completion gates authorize the evidence packet; no separate FT-01 product-design approval is required afterward. Invalidation: ENGINE/TUI/installed identity, governing law, packet contract, fixture, oracle/comparator, event/admission owner, process-authority method, output schema/path, retry/cleanup rule, freeze/amendment, or public contract changes.

## Review, owner decision, and closure

- Deterministic validation: PASS; seven reviewed source artifacts, 623/623 unique routes, 13 phases, six packets, nine tickets, honest `NOT RUNNABLE` substrate.
- Fresh-context dry read: initial 0/6/1/0; one corrected renewal ended PASS with P0/P1/P2/P3 = 0/0/0/0 and no unresolved policy questions. It named FT-01 inputs, writes, proof, deadlines, stops, branches, next states, and invalidation from this document alone.
- Independent raw-first review: initial CHANGES_REQUESTED at 0/2/4/0; one corrected renewal ended APPROVED with P0/P1/P2/P3 = 0/0/0/0. Gate G-H PASS. Review: `07_CAMPAIGN_SPEC_PROTOTYPE_REVIEW.md`.
- Kyle selected **Approve for versioning**. The answer was auto-selected after timeout and counts as Kyle's answer. Raw: `raw/bb-inj5.8/owner-decision.txt`.

H1–H6 are satisfied. This approval authorizes I to create the documentation-only spec worktree, copy reviewed evidence, audit, freeze-check, and commit locally. It does not authorize production implementation, public/schema/API change, compatibility deletion, push, PR, merge, release, or publication.
