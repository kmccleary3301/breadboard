# W11 research: observation and occupancy

## Coverage (29/29; exact plan IDs listed once)

| Ref | Exact plan ID | Note | Disposition |
|---|---|---|---|
| R01 | `DSH-DONOR-39-003` | observation | CLOSE |
| R02 | `DSH-DONOR-39-006` | observation | CLOSE |
| R03 | `DSH-DONOR-39-007` | observation | CLOSE |
| R04 | `DSH-DONOR-39-013` | observation | CLOSE |
| R05 | `DSH-SCHEMA-49-003` | schema | CLOSE |
| R06 | `DSH-SCHEMA-49-008` | schema | CLOSE |
| R07 | `DSH-SCHEMA-49-013` | schema | CLOSE |
| R08 | `DSH-DONOR-46-008` | maintenance | CLOSE |
| R09 | `DSH-DONOR-46-011` | maintenance | CLOSE |
| R10 | `DSH-DONOR-46-013` | maintenance | CLOSE |
| R11 | `DSH-DONOR-46-019` | maintenance | CLOSE |
| R12 | `DSH-DONOR-46-025` | maintenance | CLOSE |
| R13 | `DSH-DONOR-47-001` | maintenance | CLOSE |
| R14 | `DSH-DONOR-47-008` | maintenance | CLOSE |
| R15 | `DSH-DONOR-47-009` | maintenance | CLOSE |
| R16 | `DSH-DONOR-47-011` | maintenance | CLOSE |
| R17 | `DSH-DONOR-26-017` | surfaces | CLOSE |
| R18 | `DSH-DONOR-29-026` | surfaces | CLOSE |
| R19 | `DSH-LAW-10-006` | surfaces | CLOSE |
| R20 | `DSH-DONOR-42-002` | surfaces | CLOSE |
| R21 | `DSH-DONOR-43-001` | surfaces | CLOSE |
| R22 | `DSH-DONOR-50-001` | surfaces | CLOSE |
| R23 | `DSH-CLOSURE-X-001` | surfaces | CLOSE |
| R24 | `DSH-CLOSURE-X-007` | surfaces | CLOSE |
| R25 | `DSH-CLOSURE-X-012` | surfaces | CLOSE |
| R26 | `DSH-CLOSURE-X-015` | surfaces | CLOSE |
| R27 | `DSH-CLOSURE-X-016` | surfaces | CLOSE |
| R28 | `DSH-CLOSURE-X-032` | surfaces | CLOSE |
| R29 | `DSH-CLOSURE-X-048` | surfaces | CLOSE |

## Bounded disposition

**Pre-registration (before Run O).** For each 39-* row, metric `active_requirement` is 1 only when the pinned ledger JSON names both a current owner and a current consumer; baseline is 0/4. Pass threshold is `active_requirement >= 1` (a named requirement survives for adoption); kill threshold is `active_requirement = 0` (the proposed abstraction is unowned and closes). Cost cap: one ledger parse, <=30 seconds wall time, zero source/test execution. Run O parsed exactly the four JSONL records and counted only `current_owner` and `consumers`.

**Run O result:** 4/4 have no current owner and 4/4 have no current consumer. Result is below the pass threshold and meets the kill threshold; all four rows CLOSE. This is a bounded ownership discriminator, not evidence that an advisory signal is wrong.

- **R01 — Item/question:** approximate context occupancy is explicitly not gating authority; is there a current product requirement to preserve that boundary? **Ledger JSON:** `01_CURRENT_STATE_DONOR_LEDGER.md:L648`, anchor `DSH_MINING_PLANNER_CONVO.md:2353-2353`; current owner/consumers: none (the ledger lists only the charter contract and provider-metrics test evidence). **Metric/baseline:** `active_requirement=0/1`. **Pass/kill:** pass at 1; kill/close at 0. **Cost cap/run:** Run O, as above. **Result:** 0, kill threshold met. **Disposition:** CLOSE. **Owner/trigger:** none (CLOSE).
- **R02 — Item/question:** provider capacity and measured prompt size may be from different moments; must a UI consumer label this approximation now? **Ledger JSON:** `01_CURRENT_STATE_DONOR_LEDGER.md:L651`, anchor `DSH_MINING_PLANNER_CONVO.md:2359-2359`; current owner/consumers: none. **Metric/baseline:** `active_requirement=0/1`. **Pass/kill:** pass at 1; kill/close at 0. **Cost cap/run:** Run O. **Result:** 0, kill threshold met. **Disposition:** CLOSE. **Owner/trigger:** none (CLOSE).
- **R03 — Item/question:** auxiliary model jobs use revision fences; is a current durable consumer forcing this contract? **Ledger JSON:** `01_CURRENT_STATE_DONOR_LEDGER.md:L652`, anchor `DSH_MINING_PLANNER_CONVO.md:2361-2361`; current owner/consumers: none. **Metric/baseline:** `active_requirement=0/1`. **Pass/kill:** pass at 1; kill/close at 0. **Cost cap/run:** Run O. **Result:** 0, kill threshold met. **Disposition:** CLOSE. **Owner/trigger:** none (CLOSE).
- **R04 — Item/question:** one result convention is proposed for titles, summaries, extraction, reruns, search, compaction, and indexing; is there a current consumer needing it? **Ledger JSON:** `01_CURRENT_STATE_DONOR_LEDGER.md:L658`, anchor `DSH_MINING_PLANNER_CONVO.md:2376-2390`; current owner/consumers: none. **Metric/baseline:** `active_requirement=0/1`. **Pass/kill:** pass at 1; kill/close at 0. **Cost cap/run:** Run O. **Result:** 0, kill threshold met. **Disposition:** CLOSE. **Owner/trigger:** none (CLOSE).

**Theme result:** 4 CLOSE, 0 ADOPT-LATER, 0 ADOPT-NOW. No PROC-AMEND recommendation.
