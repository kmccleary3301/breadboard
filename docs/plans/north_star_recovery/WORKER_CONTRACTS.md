# North-Star recovery worker contracts

Status: revised outcome-first contracts.

This file owns worker permissions and handoffs. Packet order is in `EXECUTION_PLAN.md`; transitions and gates are in `LOOP_SPEC.yaml`.

## 1. Shared rules

Every worker receives:

- one packet key and one observable outcome;
- current base/head and dependency state;
- allowed files or read-only question;
- explicit non-goals;
- the material risk and smallest checks that expose it;
- applicable human gates;
- an attempt and review budget.

Every worker must:

- distinguish current merged source from historical or blocked work;
- use existing project conventions and test seams;
- report observed behavior, not activity counts;
- keep external facts tied to the provider/model/target/runtime that produced them;
- avoid new campaign schemas, ledgers, hashes, or reports unless the product itself consumes them;
- stop when its packet outcome is proved, blocked, or its budget is exhausted.

Workers never approve their own work, merge, publish claims, spend external resources, or change a gated public/security boundary without the corresponding authority.

## 2. Assignment shape

```text
# Packet
<key>: <title>

# Outcome
<one user-visible or durable behavior>

# Current facts
<base/head, dependencies, relevant observed failure, existing conventions>

# Scope
<allowed files and symbols>

# Non-goals
<explicit exclusions>

# Material risk
<plausible bug this packet must expose>

# Checks
<smallest static/focused/boundary/installed/external observations>

# Budget
<attempts, review rounds, wall clock, external attempts if any>

# Approval
<applicable human gate or none>

# Output
<required role result below>
```

Do not send a worker the entire campaign archive when this packet can be answered by current source, the relevant architecture clause, and one failure/result.

## 3. Roles

| Role | OMP agent | Writes | Use |
| --- | --- | --- | --- |
| Supervisor | main session | campaign coordination only | select packets, enforce gates, integrate evidence |
| Investigator | `scout` | none | answer one unknown before implementation |
| Implementer | most specific writing agent | one isolated packet worktree | reproduce, change, focused verify |
| Reviewer | `reviewer` or specialist | none | independent P0-P2 review where required |
| CI diagnostician | `ci-loop-diagnostician` | none | classify a failed exact run |
| External verifier | specialist or main session | external evidence root only | named provider/target/installed journey |

Use a worker only when it creates real concurrency, specialist judgment, or isolation. The supervisor handles small local work directly.

## 4. Investigator

### Permission

Read-only. It may inspect source, tests, contracts, docs, GitHub, Beads, and stable artifacts and may run deterministic read-only analysis. It does not edit, create campaign records, or run a broad suite.

### Output

```json
{
  "question": "",
  "answer": "",
  "current_facts": [],
  "source_refs": [],
  "uncertainties": [],
  "recommended_next_action": ""
}
```

No generic repository tour. Answer the assigned question and stop.

## 5. Implementer

### Permission

Write access is limited to one isolated worktree and the packet's allowed scope. Commit or push only when the assignment explicitly grants it. Never merge.

### Method

1. Confirm the packet base and reproduce the owning behavior.
2. Read relevant implementations, callers, and tests; use LSP for symbol-aware callsites.
3. Make the smallest complete source change.
4. Migrate affected callers and remove obsolete paths in scope.
5. Run the focused check; add a test only for uncovered observable behavior or a regression.
6. Run a boundary smoke when the change crosses a process, API, client, persistence, or package seam.
7. Return the result; do not generate extra evidence documents.

### Output

```json
{
  "packet": "",
  "base": "",
  "head": "",
  "behavior_before": "",
  "behavior_after": "",
  "changed_files": [],
  "checks": [
    {"command": "", "result": "pass|fail|blocked", "observation": ""}
  ],
  "open_risks": [],
  "decision": "ready|repair|blocked"
}
```

Include environment or external artifact identity only when it affects the claim.

## 6. Reviewer

### Permission

Read-only against the exact base-to-head diff. It may run focused tests and inspect current generated output. It does not redesign unrelated code or require ceremony not named by the packet risk.

### Lenses

Ordinary review:

- architecture and packet conformance;
- user-visible correctness and failure semantics;
- affected callsite and generated-surface coverage;
- whether checks observe the claimed boundary;
- stale compatibility paths or scope creep.

Add the security lens only when the packet changes isolation, secrets, filesystem, network, capabilities, approvals, publication, or a support claim:

- default-deny behavior;
- path and descriptor containment;
- secret redaction and environment inheritance;
- cancellation and orphan cleanup;
- claimability and external-scope accuracy.

### Finding format

Each finding names severity (`P0`-`P3`), exact path/line or behavior, impact, and the minimal correction. P3 is nonblocking. Questions block only when their answer could reveal a P0-P2 defect.

### Output

```json
{
  "base": "",
  "head": "",
  "verdict": "approve|request_changes",
  "findings": [],
  "checks_run": [],
  "unverified_external_facts": [],
  "confidence": 0.0
}
```

One approval at the current head is enough unless the security policy requires the additional security lens. A source change invalidates only review conclusions that touch changed behavior.

## 7. CI diagnostician

Classify one failed exact run as:

- product regression;
- contract/test regression;
- stale or wrong-head evidence;
- flaky infrastructure;
- external environment/provider failure;
- unknown.

Return the failing job/check, narrow reproduction, owning seam, evidence for the class, and one next action. Do not rerun CI until the classification or a changed input provides new information.

## 8. External verifier

### Permission

Read-only against product source. It may use an approved provider, target, installed environment, or external evidence directory within the stated resource envelope. It may not merge, publish a claim, or broaden scope.

### Required preflight

- source/package identity;
- provider/model/endpoint or target/runtime identity;
- credentials present without disclosure;
- bounded spend/resources and cleanup authority;
- fixture and expected claim scope.

### Output

```json
{
  "packet": "",
  "source_or_package": "",
  "external_identity": {},
  "command_or_scenario": "",
  "result": "pass|fail|blocked",
  "observation": "",
  "stable_raw_artifact_ref": "",
  "claim_supported": "",
  "claim_exclusions": [],
  "cleanup": "complete|not_required|blocked"
}
```

A preflight failure is not an execution attempt. A provider/target failure blocks only the claim that depends on it.

## 9. Supervisor

The supervisor:

- selects the earliest dependency-ready outcome packet;
- keeps at most two disjoint writers and four workers active;
- owns interpretation, tradeoffs, and human-gate questions;
- routes only current actionable P0-P2 findings back to an implementer;
- integrates packets and runs the installed/broad boundary checks at stable heads;
- keeps assurance work separate from outcome blockers;
- refreshes `STATE.json` after packet closure, approved merge, or campaign handoff;
- records external blockers without repairing unrelated product code;
- stops attempt/review/CI loops at the configured budget;
- prepares one merge/release recommendation.

### Escalation

```json
{
  "packet": "",
  "blocking_reason": "",
  "what_was_tried": [],
  "observed_evidence": [],
  "recommended_option": "",
  "alternatives": [],
  "decision_required": ""
}
```

Ask one decision that tools and current authorities cannot answer.

## 10. State and handoff

`STATE.json` is a generated control-panel view. It reports packet state, current source identity, active claims, external blockers, and authority conflicts. It does not contain a source-digest registry, event proof chain, review cardinality proof, or readiness score.

The final handoff contains:

- exact integrated source/package identity;
- what the user can now do;
- changed product surfaces;
- focused, boundary, installed, external, and broad checks actually run;
- review verdicts required by changed risk;
- active exact-scope claims and exclusions;
- compatibility removal status;
- external limitations and nonblocking assurance follow-ups;
- recommendation: merge, do not merge, or blocked;
- one human action, if required.
