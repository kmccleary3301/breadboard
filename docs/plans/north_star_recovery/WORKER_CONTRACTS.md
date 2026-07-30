# North-Star recovery worker contracts

Status: approved planning direction.

This file owns worker permissions, context packets, output shapes, and handoff rules. Packet scope and budgets come from `EXECUTION_PLAN.md`. State transitions and approvals come from `LOOP_SPEC.yaml`.

## 1. Shared rules

Every worker receives:

- packet key and title;
- exact repository root and isolated worktree when writing;
- exact base, head, and merge-base identities;
- allowed and forbidden paths;
- file, line, attempt, review, and CI budgets;
- dependencies and their verified state;
- architecture sections that constrain the packet;
- acceptance criteria and derived proof tiers;
- current known risks;
- explicit permission boundary.

Workers start blank. The assignment must contain everything required to act. Large evidence travels through repository paths, `local://`, `artifact://`, `agent://`, `issue://`, or `pr://` references.

Universal rules:

1. Read repository instructions plus `ARCHITECTURE_SPEC.md`, `EXECUTION_PLAN.md`, `LOOP_SPEC.yaml`, `WORKER_CONTRACTS.md`, `STATE.json`, `docs/DIRECTION_CHARTER.md`, and `docs/plans/phase_20_right_shape/FREEZE_POLICY.md` before acting.
2. Re-read current source; do not edit from an old plan anchor.
3. Use LSP for definitions, references, renames, implementations, and diagnostics when available.
4. Use structural search for dispatch, import, and generated-callsite coverage.
5. Writing workers use an isolated worktree and touch only allowed paths.
6. No worker may weaken a test, skip a required gate, change a fixture to hide a defect, or call a blocked result complete.
7. No worker may add a shim, alias, deprecated export, fallback, retry, telemetry, or abstraction outside packet scope.
8. No worker may edit accepted historical evidence.
9. No worker may treat a campaign score as product readiness or broad target support.
10. No worker self-approves, merges, closes an issue, expands scope, or publishes a claim.
11. A changed head invalidates the previous review, CI, and exact-head results.
12. Stop immediately at a hard budget or forbidden-path change.
13. Report observed facts, inferences, and unverified claims separately.
14. Return the required structured result even when blocked.
15. Before every spawn, inspect the effective `task.agentModelOverrides` route; `modelRoles` alone is insufficient. Record the resolved agent and model. Never spawn if the resolved model name contains `fable` or the route cannot be verified. Treat task effort as relative to the resolved model.
16. Run the Phase 20 freeze guard at preflight and local gates. Missing guard coverage, missing exception evidence, or failure blocks the packet.
17. Before any external-spend action, validate the approval's exact numeric limit, unit/currency, request cap, token-or-runtime cap, and consumed/remaining balance. Emit the typed consumption receipt from `LOOP_SPEC.yaml#external_spend_limit_contract` after every attempted request or launch, including failures and zero-cost routes.

## 2. Role matrix

| Role | OMP agent | Writes | Primary output | May approve |
| --- | --- | --- | --- | --- |
| Supervisor | `loop-architect` or parent session | tracker/orchestration state only | packet decision and handoff | no self-approval |
| Investigator | `scout` | none | evidence map | no |
| Implementer | `loop-surgeon` | isolated packet scope | patch and acceptance evidence | no |
| Specification reviewer | `loop-reviewer` | none | exact-head verdict | no |
| Security/standards reviewer | `reviewer` | none | risk-lens verdict | no |
| CI diagnostician | `ci-loop-diagnostician` | none | failure classification | no |
| State renderer | `sonic` | `STATE.json` only | materialized state | no |
| Human | owner | approval records | exact decision | yes, within stated scope |

The supervisor may prepare commits, branches, PRs, and review/CI routing under managed PR autonomy. Merge, closure/supersession, public/default changes, security changes, external spend, claim publication, campaign-graph changes, and release require the human gate in `LOOP_SPEC.yaml`.

A human approval is valid only for the actions named in its record. The record includes approval ID, gate ID, packet key, scope version, exact base, exact merge base, exact head and PR when known, protected actions, grantor, grant time, expiry or terminal event, and decision. Approval expires on any bound identity or scope change and never implies merge, closure, publication, spend, or another protected action that is not listed.

## 3. Universal assignment

Use this shape for every subagent task:

```text
# Packet
<key>: <title>

# Goal
<one observable outcome>

# Context
<exact current facts, dependencies, architecture clauses, and evidence refs>

# Target
<allowed files and symbols>

# Forbidden
<forbidden paths, behavior, generated outputs, and external writes>

# Change or investigation
<ordered steps; no hidden design choices>

# Acceptance
<commands, proof tiers, expected behavior, and negative cases>

# Budget
<files, non-generated lines, attempts, review rounds, wall clock if applicable>

# Permissions
<read-only or isolated write; commit/push/PR policy>

# Output
<one of the role contracts below>
```

A worker assignment that says "clean this up," "fix as needed," "run relevant tests," or "follow the plan" is invalid.

## 4. Investigator

### Permission

Read-only. It may inspect source, tests, schemas, docs, tracker/PR objects, immutable evidence, and generated artifacts. It may run deterministic read-only analysis. It does not edit, format, create tracker records, or run a project-wide suite.

### Required method

- start from the exact question;
- distinguish current merged source from blocked branches and historical artifacts;
- trace owners, callers, generated consumers, tests, and side effects;
- name contradictions instead of silently choosing one source;
- recommend the smallest decision supported by evidence;
- state what remains unverified.

### Output

```json
{
  "status": "complete|partial|blocked",
  "question": "string",
  "observed": [
    {
      "claim": "string",
      "evidence": ["repo-relative path, symbol, issue/pr ref, or artifact ref"]
    }
  ],
  "contradictions": [
    {
      "left": "string",
      "right": "string",
      "resolution_needed": "string"
    }
  ],
  "recommendation": {
    "decision": "string",
    "reason": "string",
    "tradeoffs": ["string"]
  },
  "affected_paths": ["string"],
  "behavior_locks": ["string"],
  "risks": ["string"],
  "unverified": ["string"]
}
```

## 5. Implementer

### Permission

Write access is limited to one isolated worktree and the packet's allowed paths. The parent task call authorizes those edits. Commit and push are allowed only when the supervisor assignment says so. Merge is never allowed.

### Required method

1. Verify base, merge-base, branch, and worktree.
2. Run the packet checker before editing. G0 and G2 alone follow their distinct exact bootstrap records in `EXECUTION_PLAN.md`; G0 runs its deterministic JSON/diff postcheck, and G2 must run the newly installed checker on itself before review.

G0's supervisor assignment may authorize one terminal bootstrap commit only after canonical closure validation seals the result in the success ledger. A distinct finalizer owns preflight validation, the dual-ledger anchor, and both genesis rows. Every operational identity is disjoint from both ledgers and every approval, grantor, reviewer, review-run, author, and actor identity. Both ledgers record path, device, inode, digest, sequence, prior-entry digest, and run identity under exclusive locking.

After either closure or terminal start, every external mutation boundary catches `BaseException`. Protected ledger writers complete the whole row or truncate and fsync to the locked pre-append length. Validation and intent rows use deterministic IDs, exact matching-row recovery, and conflict rejection. A writer result is not self-authenticating: the supervisor requires full failure validation after success or bounded publication validation after writer failure.

The v5 push receipt contains no unbound output or timing claims. Fresh push, ordered precheck, reconciliation, terminal validation, and G2 require the same complete canonical origin and receipt predicates. A receipt-bound optional failure is completely validated before success append and revalidated afterward; G2 binds start, main row, and live heads independently.
3. Reproduce the existing behavior or failure on the base when applicable.
4. Use existing patterns and owners. Do not create a second convention.
5. Apply one bounded logical change.
6. Run T0 after meaningful edits and the complete derived local plan before handoff.
7. Run `scripts/check_phase20_freeze.py` at preflight and local gates.
8. Run the packet checker after every commit.
9. Stop at 70% and report trajectory; obtain owner acknowledgement at 85%; stop at 100%.
10. Return exact commands and result refs. A summary without evidence is incomplete.

### Output

```json
{
  "status": "complete|partial|blocked",
  "packet_key": "string",
  "base_sha": "40-hex",
  "head_sha": "40-hex",
  "merge_base_sha": "40-hex",
  "branch": "string",
  "worktree": "string",
  "files_changed": ["repo-relative path"],
  "budget": {
    "files_used": 0,
    "files_max": 0,
    "non_generated_lines_used": 0,
    "non_generated_lines_max": 0,
    "attempt": 1
  },
  "external_spend": {
    "approval_id": "required for N9B or null",
    "identity_mode": "provider|target|participant_only",
    "provider_or_target_identity": "string|null",
    "route": "string|null",
    "model": "string|null",
    "limit_value": 0,
    "unit": "currency|target_seconds|provider_tokens|zero_cost",
    "currency_or_not_applicable": "string",
    "request_cap": 0,
    "token_or_runtime_cap": 0,
    "consumed_value": 0,
    "remaining_value": 0,
    "request_count": 0,
    "token_or_runtime_consumed": 0,
    "attempt_receipts": [{"path": "external evidence path", "sha256": "sha256:<64hex>"}]
  },
  "behavior": {
    "before": "string",
    "after": "string",
    "preserved": ["string"]
  },
  "acceptance_results": [
    {
      "tier": "T0|T1|T2|T3|T4",
      "command": "string",
      "command_sha256": "sha256:<64hex>",
      "environment_sha256": "sha256:<64hex>",
      "head_sha": "40-hex",
      "cache_key": {
        "packet_key": "G0|G1|G2|R0|R1|R2|V1|P0|P1|R3A|R3B|R3C|R4|N9B|N10|S1|A1|C1|O1|O2|F1",
        "scope_version": "string",
        "source_tree_sha": "40-hex",
        "head_sha": "40-hex",
        "base_sha": "40-hex",
        "merge_base_sha": "40-hex",
        "exact_command_sha256": "sha256:<64hex>",
        "environment_fingerprint_sha256": "sha256:<64hex>",
        "test_manifest_sha256": "sha256:<64hex>",
        "dependency_lock_sha256": "sha256:<64hex>",
        "generated_input_sha256s": ["sha256:<64hex>"],
        "target_version": "required for T4 or null",
        "fixture_sha256": "required for T4 or null",
        "provider_route_and_model": "required for T4 or null",
        "comparator_version": "required for T4 or null",
        "runtime_fingerprint": "required for T4 or null"
      },
      "passed": true,
      "duration_seconds": 0.0,
      "artifact_ref": "string",
      "cache_hit": false
    }
  ],
  "generated_outputs": [
    {
      "path": "string",
      "authored_inputs": ["string"],
      "authored_input_sha256s": ["sha256:<64hex>"],
      "generator_command": "string",
      "fixed_point": true
    }
  ],
  "deviations": ["string"],
  "open_risks": ["string"],
  "human_gate": null
}
```
A `cache_hit: true` result is invalid unless every non-T4 cache field is present and non-null. A T4 cache hit additionally requires every T4 field to be present and non-null. The top-level command and environment fields are display copies and must equal their cache-key digests.
For N9B, `external_spend` is required and non-null; its receipt list contains one typed receipt for every attempted provider request or target launch, including failed and zero-cost attempts. For packets without an `external_spend` gate, this field is null and all spend actions are forbidden.

When blocked, `human_gate` contains one exact question, observed evidence, 2-4 options, and a recommendation.

## 6. Specification reviewer

### Permission

Read-only. Review the exact base-to-head diff and named evidence. It may run targeted tests. It does not edit or suggest unrelated cleanup.

### Review lenses

- conformance to `ARCHITECTURE_SPEC.md` and the packet specification;
- observable correctness and failure semantics;
- behavior preservation where required;
- complete migration of callers within scope;
- generated/schema/dispatch consistency;
- test adequacy and false-green risks;
- scope and budget compliance;
- stale evidence or identity mismatch.

### Finding format

Each finding has:

- stable finding ID;
- P0, P1, P2, P3, or Q severity;
- exact path and line or artifact reference;
- violated contract;
- concrete failure mode;
- required correction or evidence;
- whether it blocks promotion.

P0-P2 block. P3 and Q do not block unless they reveal a wider invariant failure.

### Output

```json
{
  "verdict": "approve|request_changes|blocked",
  "packet_key": "string",
  "base_sha": "40-hex",
  "head_sha": "40-hex",
  "merge_base_sha": "40-hex",
  "scope_version": "string",
  "environment_fingerprint_sha256": "sha256:<64hex>",
  "target_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "fixture_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "provider_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "runtime_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "reviewed_artifacts": [
    {
      "path": "repo-relative path",
      "sha256": "sha256:<64hex>"
    }
  ],
  "approval_scope": {
    "allowed_paths": ["repo-relative path"],
    "forbidden_paths": ["repo-relative path or glob"],
    "protected_actions_reviewed": ["string"]
  },
  "review_lens": "specification_correctness",
  "scope_compliant": true,
  "budget_compliant": true,
  "evidence_fresh": true,
  "findings": [
    {
      "id": "F-001",
      "severity": "P0|P1|P2|P3|Q",
      "location": "path:line or artifact ref",
      "contract": "string",
      "failure": "string",
      "required_action": "string",
      "blocking": true
    }
  ],
  "tests_run": [
    {
      "command": "string",
      "passed": true,
      "artifact_ref": "string"
    }
  ],
  "unverified": ["string"],
  "findings_sha256": "sha256:<64hex>"
}
```

A reviewer approval is invalid if the head changes.
A reviewer approval is also invalid unless its environment fingerprint equals the current packet environment and every target, fixture, provider, or runtime identity applicable to the reviewed work is `known`, non-null, and equal to the identity in the current packet record. `evidence_fresh` is derived from those equalities and the reviewed artifact digests; it is not accepted as an unbound assertion.

## 7. Security and standards reviewer

### Permission

Read-only. Required for the dual-lens packets listed in `LOOP_SPEC.yaml`.

### Review lenses

- product/evidence boundary;
- isolation, secret, filesystem, network, capability, and approval boundaries;
- path traversal, symlink containment, inherited environment, file descriptors, and child cleanup;
- public contract and compatibility semantics;
- schema versioning and migration;
- generated-client and OpenAPI drift;
- claim scope and evidence provenance;
- rollback and operational failure.

Use the same output as the specification reviewer with `review_lens: security_standards_generated_drift`.

## 8. CI diagnostician

### Permission

Read-only. It reads the exact CI run, logs, workflow, changed paths, local results, and known baseline. It never edits and never reruns CI itself.

### Classification

Choose exactly one primary class:

- `product_defect`: changed behavior or code is wrong;
- `specification_gap`: packet requirements are contradictory or incomplete;
- `flaky_check`: same code/environment has nondeterministic check outcome with evidence;
- `environmental_hold`: required platform, credential, service, or resource is unavailable;
- `gate_defect`: the check is missing, stale, skipped, or tests the wrong contract.

An absent or skipped required guard is a gate defect, not green CI.

### Output

```json
{
  "status": "classified|blocked",
  "packet_key": "string",
  "head_sha": "40-hex",
  "base_sha": "40-hex",
  "merge_base_sha": "40-hex",
  "scope_version": "string",
  "environment_fingerprint_sha256": "sha256:<64hex>",
  "changed_paths": ["repo-relative path"],
  "changed_paths_sha256": "sha256:<64hex>",
  "target_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "fixture_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "provider_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "runtime_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "run_id": "string",
  "failed_check": "string",
  "classification": "product_defect|specification_gap|flaky_check|environmental_hold|gate_defect",
  "evidence": ["string"],
  "narrow_reproduction": "string",
  "recommended_route": "implementer|planner|retry_once|human_external|gate_owner",
  "safe_to_rerun": false,
  "unverified": ["string"]
}
```

A CI classification cannot drive a state transition unless the run record and diagnostician output agree on packet key, head, base, merge base, scope version, environment fingerprint, canonical changed-path digest, and every applicable target/fixture/provider/runtime identity. Unknown applicable identities block classification use; a stale run remains evidence but cannot be attached to the current packet state.

## 9. Partial external-governance recovery

After any partial G0 mutation, the supervisor emits a fail-closed record with exactly the fields in `LOOP_SPEC.yaml#partial_governance_recovery_record_required_fields`. The record binds the original preflight and pre-attempt tracker snapshot, a fresh live tracker snapshot, completed action receipts, remaining protected actions, the one reconciled packet-key-to-issue-ID map, duplicate-key and unrelated-graph diff reports, current source/environment identities, and a future-UTC-expiring `partial_governance_recovery` approval whose protected action is `resume_G0_after_partial_external_mutation_with_exact_reconciliation`.

Recovery reuses every conforming issue already created for a packet key. It never creates a second issue for that key, silently deletes a partial issue, or changes an unrelated tracker node or edge. Nonconforming partial records remain blocked for an explicit human abandon/supersede decision.

## 10. Evidence-only packet worker

### Permission

Read-only against product source. It may write only to the packet's isolated external evidence root after participant/spend approval. It cannot create a code branch, PR, CI run, merge record, claim, or packet closure.

### Output

```json
{
  "status": "evidence_complete|failed|blocked",
  "packet_key": "O1|O2",
  "scope_version": "string",
  "evidence_attempt": 1,
  "evidence_attempt_max": 1,
  "review_round": 1,
  "review_round_max": 1,
  "approval_ids": ["string"],
  "spend_limit": {
    "identity_mode": "provider|target|participant_only",
    "provider_or_target_identity": "string|null",
    "route": "string|null",
    "model": "string|null",
    "limit_value": 0,
    "unit": "currency|target_seconds|provider_tokens|zero_cost",
    "currency_or_not_applicable": "string",
    "request_cap": 0,
    "token_or_runtime_cap": 0,
    "consumed_before": 0,
    "remaining_before": 0
  },
  "spend_consumed": 0,
  "spend_remaining": 0,
  "spend_request_count": 0,
  "spend_token_or_runtime_consumed": 0,
  "spend_attempt_receipts": [{"path": "external evidence path", "sha256": "sha256:<64hex>"}],
  "participant_or_context_identity_sha256": "sha256:<64hex>",
  "source_tree_sha": "40-hex",
  "head_sha": "40-hex or null when no branch exists",
  "base_sha": "40-hex",
  "merge_base_sha": "40-hex",
  "target_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "fixture_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "environment_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "runtime_identity": {"status": "known|unknown", "value": "sha256:<64hex>|null"},
  "dossier_path": "external evidence path",
  "dossier_sha256": "sha256:<64hex>",
  "raw_inputs_sha256": ["sha256:<64hex>"],
  "raw_outputs_sha256": ["sha256:<64hex>"],
  "commands": ["string"],
  "elapsed_seconds": 0,
  "authored_line_count": 0,
  "failures": ["string"],
  "redaction_report_sha256": "sha256:<64hex>",
  "review_required": true,
  "unverified": ["string"]
}
```

For N9B, O1, and O2, the packet checker dereferences every ordered spend receipt, recomputes its digest, validates the typed identity/timing/counter fields and every balance/cap invariant in `LOOP_SPEC.yaml#external_spend_limit_contract`, and reconciles the output aggregates to those receipts. Failed and zero-cost attempts are included.

A failed, over-budget, contaminated, unredacted, or missing-hash dossier remains negative evidence and cannot emit `evidence_complete`.

O1/O2 nevertheless have existing Beads packet issues. After a successful dossier and independent exact-hash review, the supervisor may close only that issue through the `evidence_closure_approved` transition and only with a current human `packet_closure` approval. No evidence worker creates or closes an issue, branch, PR, CI run, or merge.

## 11. State renderer

### Permission


It may replace `docs/plans/north_star_recovery/STATE.json` only. It reads external authorities and immutable artifacts. It does not infer closure, approval, freshness, or equivalence from names.

### Rules

- keep reviewed head, PR head, merge commit, source authority, and target/runtime identities as typed roles;
- materialize conflicts instead of choosing a convenient value;
- separate packet score from promoted score and product readiness;
- separate target-support claims from non-target accounting;
- include source ref and hash for every derived claim;
- hash and schema-validate every referenced support claim and evidence manifest before counting it;
- record per-claim support-claim digest, evidence-manifest digest, target/fixture/environment/runtime identity, and validation result;
- set claim counts to unknown and blocking when any referenced artifact or current-source validation is missing;
- represent every unavailable target, fixture, environment, or runtime identity as `{"status": "unknown", "value": null}` on that claim; never omit the key or count the claim active;
- require the recovery-claim pointer to bind record, claim, scope, input, environment, execution, comparison, support-claim, and reverification digests before completion;
- after planning review, record the reviewed pre-refresh STATE digest in `planning_review_lineage` and emit a `bb.north_star_recovery.state_handoff.v1` manifest whose refreshed-state digest matches the final file;
- set `do_not_edit` true;
- compute artifact hashes after all other artifacts are stable;
- never use the prior `STATE.json` as an authority.

### Output

The output is the complete valid JSON document. The renderer also returns:

```json
{
  "status": "rendered|blocked",
  "output": "docs/plans/north_star_recovery/STATE.json",
  "generated_at_utc": "RFC3339 UTC",
  "input_refs": ["string"],
  "conflicts": 0,
  "blockers": 0
}
```

## 12. Supervisor

### Permission

The supervisor owns packet selection, preflight, worker dispatch, synthesis, review routing, CI classification, promotion preparation, state refresh, and the final human handoff. It may use managed PR autonomy from `LOOP_SPEC.yaml`.

### Required behavior

- select only a dependency-ready packet;
- refuse overlapping writer scopes;
- give each worker a self-contained assignment;
- verify worker claims against current files and tool results;
- run shared integration gates once after packet-local work;
- preserve a single promotion owner and CI watcher;
- route only actionable review findings;
- stop at hard budgets;
- ask one precise question at a human gate;
- record the exact approval scope and artifact/head identities;
- update external authorities before rendering state;
- never call the program complete from packet scores.

### Escalation payload

```json
{
  "schema_version": "bb.north_star_recovery.escalation.v1",
  "packet_key": "string",
  "state": "string",
  "question": "one precise question",
  "observed": ["string"],
  "what_was_tried": ["string"],
  "options": [
    {
      "label": "string",
      "tradeoff": "string"
    }
  ],
  "recommendation": "string",
  "decision_required_from": "human"
}
```

## 13. Finding remediation

The supervisor sends the implementer only accepted P0-P2 findings from the current head. The implementer returns:

```json
{
  "old_head_sha": "40-hex",
  "new_head_sha": "40-hex",
  "resolutions": [
    {
      "finding_id": "F-001",
      "status": "fixed|not_fixed|disputed",
      "files": ["string"],
      "evidence": ["string"]
    }
  ],
  "new_tests": ["string"],
  "budget_after_fix": {
    "files_used": 0,
    "non_generated_lines_used": 0,
    "review_round": 2
  }
}
```

The independent reviewer then reviews the new head. The previous approval has no standing.

## 14. Final handoff

The supervisor's merge brief contains:

- packet goal and non-goals;
- exact base, head, merge-base, branch, worktree, PR, and CI run;
- files changed and budget use;
- observable behavior before and after;
- test commands, environments, durations, and artifacts;
- both review verdicts and finding resolutions;
- current external blockers;
- compatibility or migration consequences;
- rollback path;
- exact typed human approval records and their protected-action scope;
- exact human attention requested;
- recommendation: merge, do not merge, or blocked.

The brief does not contain a numeric readiness score.
