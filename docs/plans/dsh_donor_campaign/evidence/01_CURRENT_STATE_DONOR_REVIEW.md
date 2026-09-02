# Current-state donor review — Gate G-B

Review date: 2026-09-02 (raw provenance date). Candidate: 01_CURRENT_STATE_DONOR_LEDGER.md, frozen candidate SHA-256 8131adf57787c54d729eb5e12f96a808aca92d13a421aae7abadcca6de2c0dcc. Pinned ENGINE: b3cacc7356244253305f8a6f84308a993485bfe2 at /Users/kylemccleary/projects/breadboard-dsh-baseline-b3cacc73, clean. Pinned TUI: 73d6e6f55a238fc9ff0486bbcc9ecffe85705715 at /Users/kylemccleary/projects/breadboard-tui-dsh-baseline-73d6e6f5, clean.

## Review method and raw-first reconstruction

I read the ledger charter/control panel, bounded charter, pinned-baseline governance, row contract, and consumer-universe definition before using any decision summary. I then parsed the raw store and inventory exactly, applied corrections in the documented order, and only then read the ledger summaries and embedded rows. No candidate, raw, inventory, plan, or tracker file was edited.

Raw row sources and exact parsed counts:

- raw/bb-inj5.2/rows-15-24.jsonl: 213 rows
- raw/bb-inj5.2/rows-25-38.jsonl: 192 rows
- raw/bb-inj5.2/rows-39-50-and-crosscutting.jsonl: 218 rows
- Corrected in order: rows-39-50-corrections.jsonl (one replace_field), crosscut-partial-corrections.jsonl (142 merge_row records), primitive-verdict-corrections.jsonl (11 merge_row records). Total corrected raw rows: 623 unique IDs; all correction targets existed. DONOR_ITEMS.yaml parsed as exact YAML: 615 items and 615 unique IDs, source revision 141eb6fef83422698aef7a981029e843e8161534, reuse_default idea_only.

The three fenced JSONL blocks in the candidate parse to 623 rows. Corrected-raw versus embedded-ledger comparison is equal for all 623 rows after key-order normalization: 623/623, with no missing, extra, or mismatched IDs.

## Recomputed coverage

ID and anchor checks: 623 candidate rows, 623 unique IDs; 615/615 inventory IDs present; all 615 donor assessment anchors exactly match DONOR_ITEMS.yaml. The eight seeds are outside donor N and are present. Closure coverage is 50/50 inventory closure IDs, with no missing or extra closure row.

Status counts (recomputed):

| Status | Rows |
| --- | ---: |
| SATISFIED | 177 |
| PARTIAL | 343 |
| CONTRADICTED | 2 |
| ABSENT | 58 |
| SUPERSEDED | 4 |
| RESEARCH_ONLY | 29 |
| REJECTED | 10 |
| Total | 623 |

Family counts (inventory families plus seeds): law 43, noun 7, protocol 4, donor 485, schema 26, closure 50, seed 8; total 623. Every row has one allowed status, all rows retain reuse_constraint idea_only, and all required row fields are present. All 177 SATISFIED rows have nonempty owner, contract, and test-evidence fields. There are 704 owner references; every owner path exists under the pinned ENGINE root and every owner revision equals the pinned ENGINE head.

The exact path check does find unresolved test anchors: tests/providers/test_provider_invoker.py does not exist at the pinned ENGINE head. It is referenced by 36 SATISFIED rows (and 12 PARTIAL rows). The pinned tests/providers directory contains provider runtime/adapter tests but no file of that name.

All 58 ABSENT rationales explicitly describe a full-universe search at pinned heads and the absence of an exact owner/contract; this is the required rationale form.

## Sampled status and family checks

The following status samples were checked at the embedded row line and pinned path. Python symbols were resolved by exact AST declaration; registry samples were checked by exact JSON parsing/path; no language server is configured.

| Status | Sample ID | Owner/path result |
| --- | --- | --- |
| SATISFIED | DSH-DONOR-15-006 (candidate line 182) | SessionService and SessionRunner declared in the pinned ENGINE paths; sample evidence paths exist. |
| PARTIAL | DSH-DONOR-15-001 (line 177) | SessionService and SessionRunner declared in the pinned ENGINE paths. |
| CONTRADICTED | DSH-DONOR-25-017 (line 411) | MultiAgentOrchestrator declared in the pinned ENGINE path. |
| ABSENT | DSH-DONOR-15-003 (line 179) | No current owner; rationale records full-universe search. |
| SUPERSEDED | DSH-SCHEMA-49-002 (line 724) | schema_lifecycle.v1 registry path exists and parses at the pinned ENGINE head. |
| RESEARCH_ONLY | DSH-DONOR-26-017 (line 430) | validate_signal_proposal declared in the pinned ENGINE path. |
| REJECTED | DSH-DONOR-21-017 (line 326) | No current owner; governance rationale and evidence are present. |

The seven family samples are DSH-LAW-10-001 (law; SessionState declared), DSH-NOUN-7-001 (noun; ConfigView declared), DSH-PROTOCOL-8_1-001 (protocol; ConfigView declared), DSH-DONOR-15-001 (donor; SessionService/SessionRunner declared), DSH-SCHEMA-49-001 (schema; contract_tiers.v1 registry exists and parses), DSH-CLOSURE-X-001 (closure; no owner by disposition), and S1 (seed; EventLog declared). The corrected closure owner DSH-CLOSURE-X-006 points to the existing sessionsCommand export in tui_skeleton/src/commands/sessions.ts, but its separate test-evidence symbol remains stale; see Finding P2.

## Inventory, tiers, freeze, and recall

The raw current-inventory method states that it counted pinned tracked source/config/schema files and exact exported declarations, used generated manifests where authoritative, searched only the named pinned ENGINE/TUI universes, and used structural declarations plus exact-token references because no language server was available. Deterministic counts have no interval. The recorded surface counts agree with the ledger: 26/26 generated public operations; 135 route decorators across 429 ENGINE Python files; 34 default, 5 legacy, 1 migration, and 24 E4 CLI declarations; events 42/15/2/2; schemas 104 tier entries, 123 kernel files, 27 public files; SDK 24 Python exports, 26 TypeScript methods plus invoke, 99 kernel TypeScript re-exports; TUI SDK consumers 34/364 and 17/1351 with vendored SDK 0.4.0 exposing 25 names; repository support 165 config, 505 scripts, 168 conformance, 1,159 tests; profiles/E4 182/177 YAML, 2 implementation profiles, and 34 E4 lanes (24 YAML, 8 JSON); durable_reconfigure 3 definitions, 5 invocations, 1 guard; two concrete sandbox adapters.

Contract-tier counts recompute to 104/104: config_algebra 15 (8 keep, 6 freeze, 1 supersede), evidence 20 keep, frozen_legacy 24 freeze, host_protocol 34 (32 keep, 1 freeze, 1 supersede), runtime_protocol 11 (10 keep, 1 freeze). The inventory method and known limitations are recorded: dynamic imports/runtime entry-point lookup are not proven by exact-token recall; TUI .omp contents were not enumerated; installed identities were reused from same-head C evidence rather than rerun; route counts do not establish behavior/parity; and per-reference durable_reconfigure paths were not retained. The recall sample is BreadBoardClient at breadboard_sdk/client.py:348, with the generated 26-operation catalog, compatibility subclass, parity tests, and CLI consumers recovered and no additional owner found.

Freeze/progress evidence: check_phase20_freeze.py PASS at baseline 3b8d862f62ee9c2c421fe07758606b6973902c67; BB_RS_PROGRESS reports 885/1000 unconditional and 0/250 conditional, 50 done, 6 todo, 5 gated-N/A, with G-J/G-K/G-L failing. Product identity is 18.0.1, served backend 87616073288ab410a6e9f9bb4e5de4de821715ca, and TUI build 458339c7a76ff5bc045e0cb5cf7f41dfa741c458.

## Substrate, primitive, depth, and duplicate audits

All seven section-0 substrate summaries are present with owner/evidence and missing contract: durable event log/projections PARTIAL (EventLog, SessionState, todo projection; canonical-journal replay and projection registry missing); request reconstruction/provenance PARTIAL (ProviderRequest, ProviderInvoker, SystemPromptCompiler; byte-exact reconstruction and provenance chain missing); immutable generations PARTIAL (compile_config/plugin loader; health/publish/adoption/pinning/drain missing); execution worlds ABSENT (DevSandboxV2 and DockerSandboxV2; no local/container/Ray/Slurm equivalent world interface); annotation sidecars ABSENT (no product owner); durable children/workflows PARTIAL (OpenCodeAgent, MultiAgentOrchestrator, JobManager; provider-neutral durable settlement missing); lossless compaction PARTIAL (SessionState, runtime Session, project_memory_compaction; exact replay after arbitrary compactions missing). No substrate piece is promoted.

All seven nouns and four protocols have explicit corrected verdicts. Definition, Event, Projection, Capability, and Operation are vocabulary-only; Entity and Resource are shallow generalizations rejected as behavior-owning modules. Scope/binding, policy/authority, lease/lifecycle, and message/delivery are vocabulary-only across existing specific authorities. The corrected rows set module_test.proposed false for all eleven and leave promotion to later law/evidence work.

Sixteen module/seam rows have module_test.proposed true and all seven section-6.1 answers in their summaries. Eight have at least two real adapters: DSH-DONOR-21-016, 23-023, 27-012, 31-006, 35-015, 36-005, 38-001, and 38-002. Eight fail the real-adapter threshold and remain hypotheses: DSH-DONOR-25-001, 29-025, 31-008, 36-012, 37-001, 37-003, 38-008, and 38-009. The named-failure adapter entries were not counted as real adapters; no module is promoted.

The five duplicate-authority pairs were compared with section 6: generated Python/TypeScript public bindings are intentional adapters under the operations catalog; public session versus legacy cli_bridge routes retain the public catalog as survivor pending named consumer migration; TUI canonicalClient/controlClient are disjoint lifecycle/control interfaces composed by connectCanonicalBreadboardEnginePort; kernel event v1/v2 are registry-governed compatibility; and product runtime versus E4/audit views remain distinct because E4 is never product truth. No duplicate deletion packet is earned and no authority is silently merged.

## Findings by priority

### P0

None observed.

### P1

1. Repair nonexistent provider-invoker test evidence (candidate lines 204-223 and 250-275). At the pinned ENGINE head, 36 SATISFIED rows cite tests/providers/test_provider_invoker.py even though that exact path is absent; those rows therefore do not have resolvable focused test evidence. This violates the SATISFIED owner/contract/evidence gate and blocks G-B. Replace each stale anchor with an existing pinned test anchor or downgrade the affected rows with an exact rationale.

### P2

1. Correct the stale TUI test-evidence symbol (candidate line 757, DSH-CLOSURE-X-006). The applied correction changed current_owner to sessionsCommand, but test_evidence still names sessions.ts:list_sessions; exact read of the pinned file shows sessionsCommand and no list_sessions. The row's owner correction is therefore not enough to make its recorded test anchor resolvable.

### P3

None observed.

## B1-B8 and Gate verdict

B1 (grounding, identities, environment, freeze), B2 (inventory/method/recall), B3 (sections 15-24 and seven substrates), B4 (sections 25-38), B5 (sections 39-50 and S1-S8), B6 (623 coverage and 50 closure rows), and B7 (module/depth, primitives, adapters, duplicate authority) are confirmed as content and mechanical checks above. B8 independent review is CHANGES_REQUESTED because the unresolved P1 and P2 evidence anchors violate the G-B stop condition.

Verdict: CHANGES_REQUESTED. APPROVED is not permitted: although coverage is 623/623 and corrected raw equality is 623/623, unresolved P1/P2 findings remain.

## Renewal review — Gate G-B

Renewal date: 2026-09-02. The renewed frozen candidate digest is SHA-256 4c8431bac30704c760a01decbe9e8ef5646b80efc15d7e494b53f8e8ec880a4b. The pinned ENGINE and TUI heads are unchanged: b3cacc7356244253305f8a6f84308a993485bfe2 and 73d6e6f55a238fc9ff0486bbcc9ecffe85705715.

The original three raw row slices still parse as 213 + 192 + 218 = 623 unique rows. I reapplied the original correction order, then applied the three append-only renewal files in their documented order: provider-test-anchor-corrections.jsonl (48 replace_list_value records), closure-session-anchor-corrections.jsonl (one record), and closure-session-contract-correction.jsonl (one record). All 204 correction targets and old list values matched. The renewed candidate has 623 rows in three fenced JSONL blocks. Corrected-raw versus embedded-ledger equality is 623/623 with no missing, extra, or mismatched row.

Renewed recomputation remains 623 unique rows, 615/615 donor anchors, 8/8 seeds, and 50/50 closure rows. Statuses are unchanged: SATISFIED 177, PARTIAL 343, CONTRADICTED 2, ABSENT 58, SUPERSEDED 4, RESEARCH_ONLY 29, REJECTED 10. Family totals remain law 43, noun 7, protocol 4, donor 485, schema 26, closure 50, seed 8. The old provider path reference count is zero and the old list_sessions reference count is zero. The corrected DSH-CLOSURE-X-006 owner, contract, and test evidence all now use sessionsCommand at the existing pinned tui_skeleton/src/commands/sessions.ts export.

The renewal exact path check found zero nonexistent owner, contract, or test paths across the candidate. The 704 owner references still all resolve at the pinned head with matching revisions. The previously blocked 36 SATISFIED and 12 PARTIAL provider-test references now resolve to tests/test_provider_invoker.py. The closure-session anchor and contract both resolve to sessionsCommand; the stale session.py/list_sessions contract is gone. Unchanged sampled status and family owners remain symbol/path verified as recorded above, and the renewal-specific closure symbol was structurally read at its pinned export.

### Renewal findings

P0: none.

P1: none. The prior nonexistent provider-test evidence is fixed in all 48 affected rows.

P2: none. The prior stale TUI test and contract anchors are fixed in DSH-CLOSURE-X-006.

P3: none.

### Renewal verdict

B1-B7 remain confirmed. B8/G-B is APPROVED: coverage is 623/623, raw reconstruction and embedded-ledger equality are 623/623, all donor anchors and closure rows are present, and no unresolved P0/P1/P2 finding remains.
