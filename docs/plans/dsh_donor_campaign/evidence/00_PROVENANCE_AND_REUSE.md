# DSH provenance and reuse

Ticket: `bb-inj5.1`
Status: approved
Session: 1

## Control panel

- North star: freeze one reproducible donor denominator and reuse policy before current-state reconciliation.
- Current claim: the assessment's named `0.1.0-rc.8` corpus is the first-party DeepSeek Harness tag `dsh-v0.1.0-rc.8` at commit `141eb6fef83422698aef7a981029e843e8161534`; `DONOR_ITEMS.yaml` enumerates 615 of 615 assessment proposals.
- Latest product signal: the pinned archive reproduces the assessment's four corpus fingerprints exactly: 226 public package contracts, 50 package groups, 112 English documentation pages, and 724 English Agent Notes.
- Current red class: none in A; item-level DSH anchors remain intentionally unresolved.
- Next cheapest discriminator: independent recount and source-policy review under G-A.
- Active external blocker: none.
- Stop or escalation: any recount mismatch changes N once through PROC-AMEND; any license or revision mismatch fails A.

DECISION THIS STAGE CAN MOVE: what exact donor corpus and reuse boundary downstream work may cite.
EVIDENCE THAT WOULD MOVE IT: a first-party source/revision/license, an archived digest, corpus fingerprints, and an independently reproducible extraction of every required assessment family.
EVIDENCE PRODUCED SO FAR: exact first-party tag and commit; archived source snapshot; MIT license text; matching corpus fingerprints; 615-item deterministic inventory.
PROVENANCE-ONLY ARTIFACTS PRODUCED SO FAR: the archived DSH tag under `raw/bb-inj5.1/` and the primary-source research record in that raw store.

## Gate card

- Claim: `DONOR_ITEMS.yaml` contains N of N explicit donor proposals required by A2, each with a stable assessment anchor and a primary anchor or `NO_PRIMARY_ANCHOR`; the reuse policy follows the pinned license evidence.
- Denominator: all explicit proposal units in Part IV laws; seven nouns; four protocols; sections 15–50; section 49 schema recommendations; and each row of the 50-group closure table. Cross-family duplicates remain separate when the assessment presents them as separate obligations; accidental textual duplicates within one source block collapse to one item with all anchors.
- Discriminating power: an independent re-extraction of at least three sections, including one law block, one donor section, and the closure table, must reproduce their counts and stable IDs. Any mismatch changes N once before close.
- Oracle: source text ranges and first-party repository/license files, never the assessment's confidence claims.
- Cost: at most two sessions; one background primary-source research pass; one independent recount review.
- Retry: only a new source candidate, archive, parser rule, or diagnosed extraction mismatch.
- Stop: exact source and license pinned; denominator independently confirmed.
- Fallback: preserve the assessment as a claim index, forbid code/fixture/test/schema reuse for unmapped items, and allow only independently restated ideas.
- Nonclaims: A does not decide whether any donor item fits current BreadBoard or should be implemented.

## Pinned primary source

| Field | Frozen value |
| --- | --- |
| Identity | DeepSeek Harness, first-party repository `https://github.com/deepseek-ai/deepseek-harness` |
| Release tag | `dsh-v0.1.0-rc.8` |
| Commit | `141eb6fef83422698aef7a981029e843e8161534` |
| Commit date | `2026-08-19T15:11:50Z` |
| npm corroboration | `@deepseek-ai/dsh@0.1.0-rc.8`, published `2026-08-19T15:41:29.655Z` |
| First-party archive URL | `https://api.github.com/repos/deepseek-ai/deepseek-harness/tarball/refs/tags/dsh-v0.1.0-rc.8` |
| Preserved archive | `raw/bb-inj5.1/deepseek-harness-dsh-v0.1.0-rc.8.tar.gz` |
| Retrieved | `2026-09-02` |
| Archive SHA-256 | `f232ba127ad9120308436655c7c89ed1c81680c8eda0ff70d22c86c4331dfbdc` |
| Archive size | `14,390,053` bytes |
| License anchor | `deepseek-ai/deepseek-harness@141eb6fef83422698aef7a981029e843e8161534:LICENSE` |

The tag commit message is `Merge pull request #2783 from deepseek-harness/release/dsh-0.1.0-rc.8`, followed by the release body `release: dsh@0.1.0-rc.8`. The root manifest at that commit names version `0.1.0-rc.8`, license `MIT`, package manager `pnpm@11.7.0`, and Node engine `^22.19.0 || >=24.0.0`. The root README calls the release a developer preview with compatibility-breaking changes. The root manifest has no `os` or `cpu` restriction. These are source-corpus assumptions, not claims that BreadBoard should adopt the DSH toolchain or runtime.

The archive contains 9,061 members: 7,799 regular files, 8 links, and 1,254 directories. Mechanical inventory found 224 non-private package manifests below `packages/`, plus public `apps/cli` and `apps/web`, for 226 public package contracts; 50 top-level package groups; 111 English Markdown pages below `docs/` plus root `README.md`, for 112; and 724 English Agent Notes. Those four exact matches identify this tag as the assessment's audited corpus rather than merely a same-version CLI package.

Silent source upgrades are forbidden. A later DSH release is a different donor corpus and requires PROC-AMEND.

## Extraction contract and denominator

1. `assessment_anchor` is `DSH_MINING_PLANNER_CONVO.md:<start>-<end>` and names the containing family.
2. `source_text` is the assessment's exact proposal phrase, whitespace-normalized only.
3. `family` is one of `law`, `noun`, `protocol`, `donor`, `schema`, `closure`.
4. IDs are deterministic: `DSH-{family code}-{source section}-{ordinal}`; ordinals follow source order and never renumber after close.
5. `primary_anchor` is a pinned repository/archive path and revision or `NO_PRIMARY_ANCHOR`.
6. No BreadBoard status, owner, or promotion decision appears in A; B owns those fields.

`DONOR_ITEMS.yaml` freezes N = **615**:

| Family | Count |
| --- | ---: |
| Part IV laws | 43 |
| Seven nouns | 7 |
| Four protocols | 4 |
| Donors from sections 15–48 and 50 | 485 |
| Section 49 schema recommendations | 26 |
| Closure-table rows | 50 |
| **Total** | **615** |

The extraction includes each distinct normative proposal or negative lesson. A parent and its subordinate list form one item; table rows remain separate; rationale, statistics, examples, and deletion-benefit lists are excluded. Cross-family repetition remains separate because the assessment assigns separate obligations.

The pinned archive establishes corpus identity, not item-level provenance for the assessment author's synthesized proposals. No exact DSH path was established for any of the 615 synthesized phrases during A, so every item remains `primary_anchor: NO_PRIMARY_ANCHOR` and `reuse_scope: idea_only`. Downstream work may add an item-level primary anchor only through PROC-AMEND; it may not infer one from topical similarity.

## License and reuse boundary

The repository-owned source and associated documentation at the pinned commit use the MIT License, copyright 2026 DeepSeek. MIT permits use, copy, modification, merge, publication, distribution, sublicensing, and sale. Copies or substantial portions must retain the copyright and permission notice. The software is supplied without warranty. `THIRD_PARTY_NOTICES.md`, vendored trees, generated assets, and dependencies may carry separate terms; this campaign does not treat them as MIT merely because they appear in the repository.

| Reuse kind | Campaign rule |
| --- | --- |
| Idea | Allowed when independently restated and cited to the donor item. This is the only allowed reuse for `NO_PRIMARY_ANCHOR`. |
| Quotation | Requires an exact pinned primary path and revision. Attribute DeepSeek; retain the MIT notice for copied substantial portions. No quotation from an unmapped inventory item. |
| Schema | Copy or modification is allowed only from a repository-owned MIT path with exact provenance and required notice retention. An unmapped schema recommendation is idea-only. |
| Fixture | Same as schema; third-party data and generated assets require their own license review. Unmapped fixtures are prohibited. |
| Test | Same as schema; preserve notice for copied or substantially derived test material. Unmapped tests are prohibited. |
| Implementation | Same as schema; exact source provenance and notice retention are mandatory. No implementation work occurs in this campaign. |

This table is the campaign's conservative evidence policy, not a legal opinion. It is stricter than the license where provenance is missing.

## Evidence anchors and provenance blocks

A primary DSH anchor has the form:

`deepseek-ai/deepseek-harness@141eb6fef83422698aef7a981029e843e8161534:<path>[:<symbol-or-line-range>]`

An assessment anchor has the form:

`docs_tmp/bb_direction_assessment/DSH_MINING_PLANNER_CONVO.md:<line-range>`

An archive-level claim must also name the preserved archive path and its SHA-256 above. `NO_PRIMARY_ANCHOR` is a terminal provenance value, not permission to cite the assessment as primary evidence.

Every repository command cited in a later decision repeats the PROC-GROUND block:

```text
checkout_path: <absolute path>
git_head: <git rev-parse HEAD>
git_status_short: <empty output required>
python: /opt/homebrew/bin/python3.11
breadboard_engine_file: <resolved breadboard_engine.__file__>
```

Every DSH archive extraction cited later repeats:

```text
source_url: https://github.com/deepseek-ai/deepseek-harness
tag: dsh-v0.1.0-rc.8
commit: 141eb6fef83422698aef7a981029e843e8161534
archive_path: docs_tmp/bb_direction_assessment/dsh_donor_campaign/raw/bb-inj5.1/deepseek-harness-dsh-v0.1.0-rc.8.tar.gz
archive_sha256: f232ba127ad9120308436655c7c89ed1c81680c8eda0ff70d22c86c4331dfbdc
retrieved: 2026-09-02
```

## Independent review

The initial independent pass reproduced law block 10 (7 items), donor section 15 (25 items), the entire closure table (50 rows), the 615 structural denominator, and samples from all six families. It rejected four precision defects. The candidate corrected source order for section 48, archive member types, and the full release commit message; local UTC evidence resolved the reviewer's stale-date finding. The single permitted renewal recomputed both hashes and returned **APPROVED** with no remaining P0/P1/P2. Gate G-A and A5 pass. Review: `00_PROVENANCE_AND_REUSE_REVIEW.md`.

## Closure

Decision: pin `deepseek-ai/deepseek-harness` tag `dsh-v0.1.0-rc.8` at commit `141eb6fef83422698aef7a981029e843e8161534`; freeze N = 615; apply the per-kind MIT/third-party rules above; and treat every synthesized inventory item as idea-only until an exact item-level primary anchor is added through PROC-AMEND. Silent DSH upgrades and inferred anchors are forbidden.
