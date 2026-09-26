# ACR-20260926-sandbox-nested-seed-baseline

- `acr_id`: `ACR-20260926-sandbox-nested-seed-baseline`
- `title`: Sealed verifier patches work for nested seeds and emit text hunks
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-26
- `status`: implemented

## 1) Problem Statement

Pi live qualification on DO-2 at 43e05168 (job 1428, row `django__django-12276`, 6173-file workspace seed) exposed two defects in the verifier-seal patch path of `breadboard/rl/harness/sandbox.py`.

1. **Nested seeds fail at seal.**
   - `_workspace_seed_baseline_digest` computes each seed directory's depth with `PurePosixPath(relative_parent)`, but `sandbox.py` imports only `Path`.
   - Every seed with a subdirectory raised `NameError` at seal. The episode then ended `status=failed`, with `run_failure = runtime/"name 'PurePosixPath' is not defined"`, `response=null` and no patch.
   - The defect dates from e5db01e9. Every replay seed was a single root file, so this branch never ran.
2. **Every patch is binary.**
   - `_sealed_repository_diff` writes the trusted attributes line `* -text -filter -diff -working-tree-encoding -eol`. The `-diff` attribute marks every path as binary, so a one-line source edit became `GIT binary patch` (DO-2 diagnostic job 1429).
   - The SWE consumer, rl-tasks 87d89c2a `tasks/swe_openhands/provider/runtime.py` `_verify`, returns `patch_successfully_applied=false` for any patch that contains `GIT binary patch`. Every BreadBoard patch was therefore ungradable.
   - The defect dates from c3142e7f, whose ACR asked for a "binary-safe" patch with trusted attributes. It did not ask for every file to be forced to binary.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/sandbox.py`.
  - The import line now reads `from pathlib import Path, PurePosixPath`.
  - The trusted attributes line becomes `* -text -filter !diff -working-tree-encoding -eol`. Its highest-precedence `!diff` overrides any in-tree `diff`/`binary` attribute, so Git's content check decides between text and binary. Diffs still run with `--binary --no-ext-diff --no-textconv`, so real binary files remain exact `GIT binary patch` sections.
  - The patch is a UTF-8 string. If a snapshot's text diff is not valid UTF-8 (for example, a Latin-1 source file), the same snapshot is re-derived under the previous `-diff` line, so every path is binary. That output is the pre-change behavior and remains applicable.
- Contract surfaces touched: none. The patch's authority, snapshot binding and digest binding are unchanged. Only the byte encoding of text-file hunks changes.
- Kernel danger-zone: yes, under `breadboard/**`.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no.
- Profiles: every profile shares the seal path. The change is profile-neutral.
- Replay evidence: no replay kit or fixture pins patch bytes. Every profile's installed replay is re-run at the new head.
- Security:
  - The attributes file remains private and highest-precedence.
  - External diff drivers and textconv remain disabled.
  - The depth, inode and byte limits and the seed identity comparison behave exactly as written. Before this fix they were unreachable for nested seeds.

## 4) Change Classification

- Classification: `internal`.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- `test_seed_baseline_digest_walks_nested_directories_within_depth_limit`:
  - builds a nested seed with `build_workspace_seed_artifact`;
  - asserts that the baseline digest equals the seed identity, and that `max_depth=1` rejects the two-level tree;
  - at base, fails with `NameError`.
- `test_sealed_workspace_seed_diff_emits_text_hunks_that_apply_to_the_seed` uses real Git and an in-tree `* binary` `.gitattributes`:
  - text edits and new files appear as text hunks;
  - a NUL-bearing file stays a `GIT binary patch` section next to a text hunk;
  - a Latin-1 file makes the whole patch binary;
  - every patch reproduces the workspace under `git apply` onto a copy of the seed;
  - at base, fails because the text edit is a `GIT binary patch`.
- Suites with the fix:

  | Suite | Result |
  |---|---|
  | `test_verifier_snapshot.py` | 76 passed |
  | `test_sandbox_runtime.py` | 143 passed |
  | `test_materialization.py` | 110 passed |
  | `test_sandbox_process_integration.py` | 24 passed, 53 sealed-execution skips |
  | `test_headless_runner.py` | 18 passed |
  | `ruff --select F821` on `sandbox.py` | pass |

- DO-2 diagnostic jobs use the w106 Pi SIF with the fixed `sandbox.py` bound over the installed module. A scripted receiver drives the live driver on the django seed through close, and the official verifier grades the prediction.
  - Job 1429 (import fix only): seal succeeds, patch is `GIT binary patch`, and the grade is "not applied".
  - Job 1430 (both fixes): the terminal succeeded, the envelope was released and native processes were all dead. The patch is a text hunk (sha256 474bec7d…). The official verifier applied it and marked it unresolved, because the edit is a comment only.
- Required after merge:
  - every profile's installed replay at the new head;
  - the Pi live qualification rerun.

## 6) Rollout Plan

- Rollout phases: branch `e4/sandbox-seed-depth-20260926`.
- Flags/toggles: none.
- Blast radius constraints: the verifier-seal patch derivation only.
- Monitoring hooks: the tests above, the installed replays and live qualification.

## 7) Rollback Plan

- Trigger conditions: a consumer that requires binary encoding for text files.
- Exact rollback commands: `git revert <commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun `tests/rl/harness/test_verifier_snapshot.py` and `tests/rl/harness/test_sandbox_process_integration.py`.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
