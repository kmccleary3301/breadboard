# Fault-boundary evidence

Ticket: `bb-inj5.3`
Status: complete
Session: 1

## Control panel

- North star: classify the two installed-product fault observations tightly enough to constrain the first implementation tranche.
- Current claim: both current installed behaviors are classified; permission cancellation is coherent, while replacement reproduces the generic HTTP failure but not an admission conflict or duplicate durable admission.
- Latest product signal: the replacement probe returned `pass`; the permission probe produced one `approval.requested`/`approval.resolved(reject)` pair and one terminal cancelled turn.
- Current red class: contract gap. The installed retry identity is not projected at the public client seam, and the canonical session-event journal did not contain the recovered turn represented in retained state.
- Next cheapest discriminator: begin `bb-inj5.1`, pinning the donor corpus and reuse constraints.
- Active external blocker: none.
- Stop or escalation: stop each scenario at a stable classification or after three mechanism-informed attempts; do not patch product code.

DECISION THIS STAGE CAN MOVE: whether permission cancellation and replacement-time submission become first-tranche behavior locks, contract decisions, or accepted current behavior.
EVIDENCE THAT WOULD MOVE IT: one installed-artifact scenario per observation, with independent event/process/client oracles at the public seam.
EVIDENCE PRODUCED SO FAR: baseline worktrees are clean; installed distribution commits are ancestors of both pinned main heads; the existing journey source explicitly expects the first post-replacement request to fail and retries it.
PROVENANCE-ONLY ARTIFACTS PRODUCED SO FAR: one session provenance block in `raw/bb-inj5.3/session-1-provenance.txt`.

## Gate card: permission cancellation

- Claim: cancelling one real installed BreadBoard permission prompt produces one deny decision, one turn cancellation, no gated tool execution, and a quiescent client state.
- Risk: the client rejects the permission but leaves the stream or turn live, producing a second error, hidden tool execution, or owned work after cancellation.
- Seam: real `bb` PTY permission UI → installed engine event journal and durable turn state.
- Oracle: UI prompt/cancellation text; canonical engine events and retained turn outcome; absence of the gated tool's durable effect; OS liveness only for cleanup.
- Cost: one focused installed probe, ≤120 seconds; at most three mechanism-informed attempts.
- Retry: only after a different prompt choice, timing point, or diagnosed harness defect.
- Stop: the event, durable-state, effect, and cleanup observations agree; otherwise classify `UNKNOWN` at budget.
- Fallback: source-level TUI permission tests are assurance only and cannot prove installed composition.
- Nonclaim: this does not prove every permission policy or tool category.

## Gate card: engine replacement submission

- Claim: after killing the authenticated idle engine, the first submitted input has one unambiguous durable disposition: accepted exactly once, explicitly rejected without durable append, or returned with a stable retry identity.
- Risk: the client reports an HTTP failure after the replacement accepted or partly appended the request, so manual retry may duplicate or conflict.
- Seam: real `bb` PTY → managed engine replacement → durable binding, event journal, retained turn state, and OS process identity.
- Oracle: old PID death outside BreadBoard; changed authority identity; UI response; owned-submission identities; durable turn/event count before and after explicit retry.
- Cost: one existing installed journey, ≤6 minutes; at most three mechanism-informed attempts.
- Retry: only on new artifact bytes, changed timing, new oracle, or a diagnosed harness defect.
- Stop: one deterministic disposition and its retry consequence are observed; otherwise `UNKNOWN` at budget.
- Fallback: historical capture is provenance and scenario-design input only.
- Nonclaim: this does not prove replacement during active provider execution or tool execution.

## Evidence

### Artifact identity

`OBSERVED` in `raw/bb-inj5.3/session-1-provenance.txt` and the replacement summary:

- ENGINE `b3cacc7356244253305f8a6f84308a993485bfe2`, clean; TUI `73d6e6f55a238fc9ff0486bbcc9ecffe85705715`, clean.
- Installed product `18.0.1`, distribution `sha256:1b3336192a2442953d996ab446fcd9ff2200fe7db0ac15ef09333a3023793f5b`, Darwin arm64.
- Installed engine executable `sha256:53fa710803e862b2c51eefaa71bf23d29a9765de20414f5525271587b38ac9a9`; runtime bundle `sha256:c245c42183962bdf5f68f7d8ef822cad5ef2604805ff4e8de370a454f9131ad2`.
- Served backend commit `87616073288ab410a6e9f9bb4e5de4de821715ca` and TUI build commit `458339c7a76ff5bc045e0cb5cf7f41dfa741c458` are ancestors of the pinned heads.
- Both probes ended with the known engine PID dead, listener closed, and active authority absent. The replacement journey additionally reports every known managed PID dead and both extraction roots removed.

### Permission cancellation sequence

Command: `/opt/homebrew/bin/python3.11 /tmp/bb-dsh-permission-probe.py`. One run, 13.17 seconds. Raw: `raw/bb-inj5.3/permission-probe.json`.

1. `OBSERVED`: the installed PTY displayed `BreadBoard permission request · run_shell · Shell · python3 bubble_sort.py`; Escape selected the UI's cancel path.
2. `OBSERVED`: journal sequence 8 was `approval.requested` for `permission_1`; sequence 9 was `approval.resolved` with `decision: reject` for the same request.
3. `OBSERVED`: the single owned turn had `cancellation_requested: true`, reason `user_requested`, state and terminal outcome `cancelled`, and `terminal_resolution_committed: true`.
4. `OBSERVED`: the transcript contained successful results for `todo.write_board` and `write`, but no result for the gated `run_shell`; therefore the denied operation did not report execution. The already-allowed write created `bubble_sort.py`, so file existence is not used as the shell oracle.
5. `OBSERVED`: `/exit` returned 0; engine PID dead, listener closed, authority absent. No remaining active owned work was observed; the only owned submission was terminal.
6. `INFERRED`: stream cancellation completed rather than hanging because durable terminal resolution appeared and the client accepted `/exit`. The probe did not expose a separate public stream-state receipt.

Verdict: the exploratory rejection is reproduced, but the feared half-cancel is falsified in this cell. Current behavior is coherent and already source-locked at `E4AgentStreamBridge`; the installed seam lacks a focused composition characterization.

### Engine replacement sequence

Command: current `installed-product-journey.py` against the installed distribution, one run, 53.51 seconds. Raw: `raw/bb-inj5.3/replacement-probe/`.

Ranked hypotheses before source inspection:

1. replacement changes the authority before the first request reaches admission; the client sees a transport-class failure and unchanged retry is admitted once;
2. the first request is admitted but its response is lost; manual retry risks duplication;
3. the recovered service rejects a conflicting admission identity.

Observed sequence:

1. The harness completed one synthetic turn, authenticated engine PID 80208 and its start token, killed that exact idle process, and observed its death outside BreadBoard.
2. It submitted the recovery prompt 0.205 seconds later. A replacement at PID 80499 appeared with different process token, engine instance, boot, and launch identities on the same endpoint.
3. The client rendered `HTTP request failed (0)`. No `Session admission conflict` appeared anywhere in the raw store.
4. The harness explicitly retried the unchanged prompt 8.194 seconds after the first submission. The turn completed.
5. Final durable state contained exactly two terminal turns and two owned submissions total: the pre-crash synthetic turn and one recovered turn. The failed first attempt did not create a third durable turn or owned submission. The final prompt transcript contains three user texts because it preserves both client attempts.
6. `WORKER CLAIM`, source-verified at the pinned TUI head: `E4AgentStreamBridge` treats `LifecycleE4ClientError` timeout-class failures as ambiguous, retains the exact structured submission, does not auto-retry, and reuses the same object and `clientMessageId` on an unchanged manual retry (`e4-agent-stream.test.ts`, “reuses the exact structured submission…”). The installed UI and binding do not project that pending retry identity, so exact installed reuse remains externally `UNVERIFIED`.
7. `OBSERVED`: retained state and binding include the recovered turn, but the selected session's canonical `session_events.jsonl` stops at the first pre-crash turn (10 events, one `input.accepted`). The recovered turn is absent from that journal. This is a current-state gap against the campaign's proposed “event log as sole truth” law, not proof that the current product contract is violated.

Verdict: generic post-replacement HTTP failure reproduced; admission conflict and duplicate durable admission falsified in this cell. Hypothesis 1 best fits all installed observations. Exact retry identity is source-locked but not independently observable from the installed public seam.

## Classification and first-tranche consequence

| Observation | Classification | Narrowest lock | First-tranche implication |
| --- | --- | --- | --- |
| Permission UI cancellation | `OBSERVED`, current behavior coherent; no defect in this cell | installed `bb` permission prompt → paired decision → terminal cancelled turn → no gated result | one composition characterization only if F finds no equivalent installed lock; do not add permission abstractions |
| Replacement first submission | `OBSERVED` generic transport failure; `UNVERIFIED` installed retry identity; no conflict/duplicate in this cell | installed submit across authority-generation change: same public request must yield a typed pre-admission rejection or a receipt proving stable retry identity; one accepted turn maximum | characterize before change; map public retry receipt, recovered journal continuity, and unchanged-input retry to one packet or fog |
| Recovered event journal | `KNOWN DIVERGENCE` from proposed north-star law; current contract status unresolved | replay selected session journal and compare to durable owned turns after replacement | candidate first-tranche differential characterization; no schema or API change |

No production patch is authorized. The replacement evidence is a contract gap for workstream E/F unless independent review proves an existing current contract already requires the missing projection, in which case PROC-DEFECT applies.

## S2 bounded fault matrix

The full seed matrix is defined for F3. Cell notation: `C` characterized here, `L` existing source lock only, `P` planned only.

| Fault \\ timing | before submit | after client dispatch / before receipt | after admission / before terminal | after terminal |
| --- | --- | --- | --- | --- |
| kill process | `C`: authenticated idle engine killed, then submit | `P`: kill during submit | `P`: kill active turn | `P`: kill after terminal before projection commit |
| abort client stream | `L`: abort before submit | `L`: ambiguous submit recovery tests | `L`: cancel active turn tests | `P`: abort after terminal before commit |
| reject permission | `P`: policy pre-deny | `C`: Escape at `run_shell` permission wait | `P`: reject after partial tool batch | `P`: late/duplicate rejection |
| replace authority | `C`: replacement observed before retry readiness | `P`: identity change during dispatch | `P`: replacement with active admitted turn | `P`: replacement after terminal before client commit |

Only two cells ran. They separated current product behavior from stale artifact (installed identities pinned), environment/provider variance (provider-free loopback scenario), client misuse (the existing recovery path requires explicit unchanged retry), and the historical admission-conflict claim. Race behavior outside those cells remains `UNVERIFIED`; no soak or expanded matrix is justified in C.

## Checklist evidence

- C1: `#Gate card: permission cancellation`, `#Gate card: engine replacement submission`
- C2: `#Artifact identity`
- C3: `#Permission cancellation sequence`
- C4: `#Engine replacement sequence`
- C5: `#S2 bounded fault matrix`
- C6: `#Classification and first-tranche consequence`
- C7: `02_FAULT_BOUNDARY_REVIEW.md#Coverage verdict`
- C8: `#Closure`

## Independent review

`02_FAULT_BOUNDARY_REVIEW.md` records `APPROVED`: C1–C6 supported, Gate G-C passed, no P0/P1/P2 finding.

## Closure

- Score earned: 110/110 after evidence links and resolution-log entry.
- Map decision: permission cancellation is coherent current behavior; replacement reproduces a generic transport failure but not admission conflict or duplicate durable admission; installed retry identity stays `UNVERIFIED`; recovered event-journal continuity is a `KNOWN DIVERGENCE` from the proposed north-star law.
- Downstream: workstream E decides the law; F may select one installed composition characterization and one differential recovered-journal characterization. No production fix, public surface, or new seam was authorized.
- Ticket: `bb-inj5.3` closed after independent approval.
