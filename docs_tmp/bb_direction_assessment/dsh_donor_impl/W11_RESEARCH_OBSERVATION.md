# W11 research: observation and occupancy

## Coverage (29/29; exact plan IDs listed once)

| Ref | Exact plan ID | Note | Disposition |
|---|---|---|---|
| R01 | `DSH-DONOR-39-003` | observation | CLOSE |
| R02 | `DSH-DONOR-39-006` | observation | ADOPT-LATER |
| R03 | `DSH-DONOR-39-007` | observation | ADOPT-LATER |
| R04 | `DSH-DONOR-39-013` | observation | CLOSE |
| R05 | `DSH-SCHEMA-49-003` | schema | CLOSE |
| R06 | `DSH-SCHEMA-49-008` | schema | CLOSE |
| R07 | `DSH-SCHEMA-49-013` | schema | CLOSE |
| R08 | `DSH-DONOR-46-008` | maintenance | CLOSE |
| R09 | `DSH-DONOR-46-011` | maintenance | CLOSE |
| R10 | `DSH-DONOR-46-013` | maintenance | ADOPT-LATER |
| R11 | `DSH-DONOR-46-019` | maintenance | ADOPT-LATER |
| R12 | `DSH-DONOR-46-025` | maintenance | CLOSE |
| R13 | `DSH-DONOR-47-001` | maintenance | CLOSE |
| R14 | `DSH-DONOR-47-008` | maintenance | CLOSE |
| R15 | `DSH-DONOR-47-009` | maintenance | ADOPT-LATER |
| R16 | `DSH-DONOR-47-011` | maintenance | CLOSE |
| R17 | `DSH-DONOR-26-017` | surfaces | ADOPT-LATER |
| R18 | `DSH-DONOR-29-026` | surfaces | CLOSE |
| R19 | `DSH-LAW-10-006` | surfaces | ADOPT-LATER |
| R20 | `DSH-DONOR-42-002` | surfaces | CLOSE |
| R21 | `DSH-DONOR-43-001` | surfaces | CLOSE |
| R22 | `DSH-DONOR-50-001` | surfaces | CLOSE |
| R23 | `DSH-CLOSURE-X-001` | surfaces | CLOSE |
| R24 | `DSH-CLOSURE-X-007` | surfaces | CLOSE |
| R25 | `DSH-CLOSURE-X-012` | surfaces | CLOSE |
| R26 | `DSH-CLOSURE-X-015` | surfaces | CLOSE |
| R27 | `DSH-CLOSURE-X-016` | surfaces | CLOSE |
| R28 | `DSH-CLOSURE-X-032` | surfaces | ADOPT-LATER |
| R29 | `DSH-CLOSURE-X-048` | surfaces | CLOSE |

## Bounded disposition

**Pre-registration O2/M2/P2 (before current-source investigation).** The old Run O result below is a superseded historical ledger measurement. For R01-R04, use each ledger row only to nominate candidates, then trace current implementation owners and callers at HEAD `9e221936b251e425887fc1e84baf9b89b5410638`. The bounded candidate source paths are `breadboard_engine/conductor/`, `breadboard_engine/provider/`, `breadboard_engine/agent_llm_openai.py`, `breadboard_engine/orchestration/`, `breadboard_engine/search/`, `breadboard_engine/state/`, `breadboard_engine/turns/`, `breadboard_engine/api/cli_bridge/`, `tui_skeleton/src/`, and only the named ledger test paths as test evidence. The observable owner/consumer discriminator is a current product implementation symbol plus a caller or output surface that reads or produces the row's signal and exposes the proposed contract. Generic validators, test-only fixtures, and historical ledger prose do not count as product consumers. Register each candidate source path and exact line range before tracing. Pass requires a current owner and current product consumer with the observable contract; kill is bounded no-pair evidence. A current owner without the proposed consumer is ADOPT-LATER only with a concrete owner and trigger; ADOPT-NOW requires PROC-AMEND and is not self-authorized. CLOSE never removes or freezes live behavior.
**Superseded historical Run O ledger measurement (not current-source evidence).** At the ledger's older revision `b3cacc...`, Run O parsed the four JSONL rows and recorded `current_owner=[]` and `consumers=[]` for R01 (`01_CURRENT_STATE_DONOR_LEDGER.md:L648`, anchor `DSH_MINING_PLANNER_CONVO.md:2353-2353`), R02 (`L651`, anchor `DSH_MINING_PLANNER_CONVO.md:2359-2359`), R03 (`L652`, anchor `DSH_MINING_PLANNER_CONVO.md:2361-2361`), and R04 (`L658`, anchor `DSH_MINING_PLANNER_CONVO.md:2376-2390`). Those arrays remain historical observations; the old 4/4 CLOSE result is superseded and is not evidence about current consumers.

**O2/M2/P2 current-source trace (all W11 citations below are at HEAD `9e221936b251e425887fc1e84baf9b89b5410638`).** The preregistered discriminator was applied after using each ledger row only to nominate candidates. A pass requires a current owner and current product consumer with an observable contract; a current owner without the proposed consumer is ADOPT-LATER only with a concrete owner and trigger. Generic validators, tests, and ledger prose remain excluded.

- **R01 — Item/question:** approximate context occupancy is explicitly not gating authority; is there a current product requirement to preserve that boundary? **Ledger candidate:** `01_CURRENT_STATE_DONOR_LEDGER.md:L648`, anchor `DSH_MINING_PLANNER_CONVO.md:2353-2353`. **Current owner:** `breadboard_engine/conductor/context_window_guard.py:6-36` estimates by the `len(content)//4` heuristic and emits `{approx_tokens, limit, ratio}`; `breadboard_engine/conductor/modes.py:370-378` invokes it and carries the warning into provider metadata. **Current consumer/contract:** `breadboard_engine/turns/context.py:23-42,53-75` hydrates and snapshots `context_warning`; `breadboard_engine/state/session_state.py:616-651,854-874` stores a host-visible `context_window` transcript item and an audit guardrail event; `breadboard_engine/conductor/loop.py:297-298,348-364,454` carries guardrail events into the run summary. A bounded search for `context_window_warning`, `approx_tokens`, and `maybe_warn` found no path from this advisory payload to a permission or authority decision. **Result:** the current owner and host/audit consumer already preserve the advisory-versus-authority boundary. **Disposition:** CLOSE; this does not delete or freeze the live guard.

- **R02 — Item/question:** provider capacity and measured prompt size may be from different moments; must a UI consumer label this approximation now? **Ledger candidate:** `01_CURRENT_STATE_DONOR_LEDGER.md:L651`, anchor `DSH_MINING_PLANNER_CONVO.md:2359-2359`. **Current owners:** `breadboard_engine/provider/profiles.py:289-350,409-434` owns the route's declared `context_window`, while `breadboard_engine/conductor/context_window_guard.py:6-36` owns the approximate prompt estimate. **Current consumer/contract:** the engine transcript path above retains the warning, while `tui_skeleton/src/commands/repl/controllerUtils.ts:171-190`, `tui_skeleton/src/repl/components/replView/composer/FooterV2.tsx:198-205`, and `tui_skeleton/src/repl/components/replView/overlays/buildModalStack.tsx:887-910` consume provider usage (`promptTokens`, `totalTokens`, cost, latency) but do not consume `approx_tokens` with capacity. The bounded `tui_skeleton/src/` search for `context_window`, `context_window_warning`, `approx_tokens`, and `context warning` found no current UI label for this approximation. **Result:** an owner pair exists, but the requested UI consumer is absent. **Disposition:** ADOPT-LATER. **Concrete owner/trigger:** use the existing warning projection in `tui_skeleton/src/commands/repl/controllerEventMethods.ts:889-898` when a future runtime event carries both the approximate estimate and declared capacity in one payload; then label it explicitly approximate and advisory. No ADOPT-NOW or live behavior change.

- **R03 — Item/question:** auxiliary model jobs use revision fences; is a current durable consumer forcing this contract? **Ledger candidate:** `01_CURRENT_STATE_DONOR_LEDGER.md:L652`, anchor `DSH_MINING_PLANNER_CONVO.md:2361-2361`. **Current owner:** `breadboard_engine/agent_llm_openai.py:1227-1256,1353-1387,1420-1646` creates `AsyncAgentJob`/`BackgroundTaskJob`, spawns work, and emits completion/task events; `breadboard_engine/orchestration/job_manager.py:11-49` and `breadboard_engine/orchestration/orchestrator.py:58-116,132-178` retain job identity and a per-job `seq`, not a parent/session revision. **Current consumer/contract:** `tui_skeleton/src/commands/repl/controllerEventMethods.ts:2105-2134` routes task events to `tui_skeleton/src/commands/repl/controllerStateMethods.ts:1029-1076`, which upserts task/work-graph state. The bounded W11 source search found no `revision`, `generation`, `epoch`, or fence binding in the job/task payloads or event normalizer; the named `tests/test_multi_agent_sync_task_smoke.py:41-103` is test evidence only. **Unlanded route distinction:** W7 snapshot `e60620a532b76440d384039ccca9366d1d647d97` has a concrete durable-child route in `breadboard/product/runtime/children.py:159-168,292-327,1247-1253,1504-1522,1847-1883` (CAS revisions, attempt identity, and late-result rejection); W8 snapshot `2e33aae792f6ab5549bf8a718480ce45ea482528` has unlanded reconciliation callsites in `breadboard_engine/api/cli_bridge/service.py:2287-2290,3135-3153`. Neither route is merged into W11, so it cannot be called current product behavior. **Result:** W11 has a durable task consumer but no current revision-fence contract; the unlanded route supplies the concrete future direction. **Disposition:** ADOPT-LATER. **Concrete owner/trigger:** adopt at the W7/W8 durable-child integration (or the first parent/session supersession or stale completion), with spawn and completion guarded by the parent revision/attempt identity. No ADOPT-NOW or live behavior change.

- **R04 — Item/question:** one result convention is proposed for titles, summaries, extraction, reruns, search, compaction, and indexing; is there a current consumer needing it? **Ledger candidate:** `01_CURRENT_STATE_DONOR_LEDGER.md:L658`, anchor `DSH_MINING_PLANNER_CONVO.md:2376-2390`. **Current owner/consumer:** `breadboard_engine/search/compaction.py:80-143,176-189` owns bounded compaction and explicitly marks the default backend `non_kernel`; `breadboard_engine/search/runtime.py:517-621` consumes it through `BoundedMessagePassingScheduler`; `breadboard_engine/search/study.py:312-355` exposes the resulting `summary_json`, `summary_txt`, lineage, and replay inspection. Session title/summary remain separately owned by `breadboard_engine/api/cli_bridge/service.py:1550-1557` and `breadboard_engine/api/cli_bridge/registry/records.py:261-299`. A bounded search for `SearchCompactionRegistry` callers found only the search package and test evidence, with no current session/TUI consumer requiring a cross-surface convention. **Result:** existing local owners and consumers cover current search compaction/observation; the proposed shared convention has no bounded current consumer. **Disposition:** CLOSE; this keeps every live local behavior and does not delete or freeze a feature.

**Theme result:** 2 CLOSE, 2 ADOPT-LATER, 0 ADOPT-NOW. No PROC-AMEND recommendation.
