# ACR-20260928-rl-lease-start-seal-and-close

- `acr_id`: `ACR-20260928-rl-lease-start-seal-and-close`
- `title`: Bound RL patch evidence to the lease-start checkout and keep episode failures attributable
- `author`: BreadBoard RL implementation
- `date`: 2026-09-28
- `status`: implemented

## 1) Problem Statement

SMoE BreadBoard Pi job 112340 completed one optimizer step but recorded 80 `breadboard_failed` episodes out of 260. Seventy-one raised `workspace_diff_too_large`; some episode results lost their original failure code when the sealed diff was absent. Job 118836 on the earlier lease-start implementation reduced failures to 16/260, but six retained oversized patches: policy-run package builds created ignored files and the verifier staging command forced them into the patch. The same campaign exposed hangs while closing a provider request blocked in a read, premature process-group cleanup failure, and policy tool errors that ended a turn instead of returning a tool observation.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. This branch changes `breadboard/rl/harness/{sandbox,service,headless,evidence,materialization,policy_provider}.py`, `runners/{base,conductor}.py`, and provider compilation/transport modules under `breadboard_engine/`.
- The manager captures the checkout's lease-start Git tree and stores its objects under a manager-private directory. Sealing diffs that tree against the policy workspace. `_PrivateGit.stage_work_tree` uses `git add -A`, without `--force`; tracked edits and new non-ignored files remain eligible, while newly created ignored build products do not enter the patch. Nested seed and empty-base cases retain their own baseline rules.
- The 32 MiB diff and 64 MiB object limits, binary-safe Git flags, private attributes, verifier join and result manifest identities remain enforced. A missing patch after a failed service run projects the bounded `SafeFailureFactV2` code rather than replacing it with a missing-diff error.
- Compiled `provider_tools.tool_errors_as_observations` is an opt-in boolean, default false. When enabled, unknown tools, malformed arguments and bounded tool timeouts become policy-visible errors and the complete assistant turn precedes its tool observations. The native-stream profiles keep their separate source semantics.
- Profile-client close terminates in-flight HTTP reads by shutting down the sockets opened for that client. Process-group drain waits for the killed members to exit before declaring a survivor. Chat length cutoff remains a truncated turn, not an invented completed answer.

## 3) Coupling and Generalization Impact

- Trainer still owns token IDs, logprobs, masks, advantages and policy versions; no `verl_wrapper` change. BreadBoard owns episode effects, immutable patch evidence, failure projection and provider lifecycle.
- The seal path is shared across profiles. The staging rule changes patch bytes for newly created ignored files only; tracked ignored files still contribute their edits. Retaining the lease-start object directory avoids trusting policy-mutated `.git` metadata during verification.
- The error-observation switch is compiled and opt-in, so existing profiles retain strict errors unless they select it. A cross-profile native-stream replay is required because Conductor also handles Pi, OMP, Mini and other profiles.
- Disabling HTTP keepalive trades reuse for a close that can interrupt a blocked response; the client closes only its own tracked sockets. A future pooling design must restore the same bounded-close guarantee before enabling reuse.

## 4) Change Classification

- Classification: `additive`.
- No schema version bump: the compiled boolean defaults to the previous strict behavior. The patch staging and failure projection correct existing behavior within existing evidence bounds.

## 5) Evidence and Validation Plan

- Linux Slurm job 119329 on SRC `73aee9cb` passed seven targeted suites (24 + 10 + 103 + 108 + 77 + 12 + 1 tests); the process-integration suite retained 16 previously observed fixture failures. Local post-merge tests passed 696 with 24 skipped on the reconciled head; the five native Pi cases that initially lacked the pinned Node package passed after the pinned fixture dependency was installed.
- Installed runtime v8 receipt begins `82dbe2d9`, manifest `64bc6ffd`. The same smoke edits a tracked source and creates a new source file: v8 patch 455 bytes, v7 patch 59,465,427 bytes including ignored build outputs. The guard was not raised for the valid edit.
- Red-before/green-after cases cover ignored product staging, service failure with and without evidence, blocked-read client close and process-group drain. A bounded SMoE v8 train (119946) is in progress; do not infer optimizer success, failure-rate threshold or BMoE readiness from the smoke.
- Required before merge: CI on the current PR head including OMP historical replay and danger-zone guard. The OMP test fixture's static 2026-09-26 runtime date diverged from its real pinned SDK prompt on 2026-09-28; the fixture now declares the current UTC date, preserving the exact request comparison.

## 6) Rollout Plan

- Merge PR #153 into `kmccleary3301/breadboard` after CI. Keep installed v8 immutable as proof for source ancestor `73aee9cb`; build a new sibling runtime if later source changes alter the installed RL path.
- Consume the v8 one-step train episode taxonomy and optimizer metrics before a multi-update/checkpoint/resume train, then attempt the BMoE topology. Never label an optimizer step alone as train readiness.

## 7) Rollback Plan

- If a real consumer requires newly created ignored artifacts in the verifier patch, revert the source change rather than forcing ignored files through the existing evidence guard. A needed ignored source can be explicitly tracked by the task author before the lease starts.
- Roll back the PR commit on protected main through a normal revert PR; keep the v7/v8 receipts and run roots for audit. Verify tracked-edit and untracked-source patch application, bounded failure codes, and provider close before redeploying.

## 8) Approvals

- This artifact records the implementation decision and evidence; it does not assert an independent reviewer, Rob acceptance, E4 final-cell acceptance, or BMoE promotion. PR/CI and recipient gates remain separate.
