# ACR-20260930-trusted-argv-nonlogin

- `acr_id`: `ACR-20260930-trusted-argv-nonlogin`
- `title`: Run trusted process argv and workspace probes without a login shell
- `author`: BreadBoard RL implementation
- `date`: 2026-09-30
- `status`: implemented

## 1) Problem Statement

`TrustedProcessHandle` (`breadboard/rl/harness/sandbox.py`) ran three trusted executions through the pinned shell as a **login** shell (`<pinned bash> -lc …`):
- `run_argv`, which serves setup and verifier argv and `ProcessLease.execute`;
- `measure_repository_base_commit`, which runs `git rev-parse --verify HEAD^{commit}` with a 256-byte output limit;
- `workspace_diff`.

A login shell sources `/etc/profile` and the first of `~/.bash_profile`, `~/.bash_login` and `~/.profile`. Those files are in state the policy can write, so a policy command could get its own code run inside the trusted executions. It could print extra output, change the exit status, or change working state before the requested argv ran.

Measured defect on IBM Slurm, 2026-09-30, runtime v11, SMoE SWE training (job 128376), episode hihbg4el:
- The policy ran `cp /tmp/test_fix.py ~/.bash_profile`.
- The verifier's base-commit probe sourced that file and exceeded its 256-byte output limit.
- The episode failed closed with `output_limit_exceeded`.

The graded patch did not depend on these probes. It comes from the shell-free sealed diff (`sealed_workspace_diff`, which runs pinned git by descriptor with a private `HOME` and `GIT_CONFIG_GLOBAL=/dev/null`). The probes could still misreport or fail.

## 2) Scope and Surfaces

- Kernel danger-zone: yes, under `breadboard/**`. This change touches `breadboard/rl/harness/sandbox.py` only.
- `TrustedProcessHandle.run_argv`, `measure_repository_base_commit` and `workspace_diff` pass `-c` instead of `-lc` to the pinned shell. The argv, positional parameters, environment, timeouts and output limits are unchanged.
- Not changed: policy-facing `run_shell` and `run_native_tool` keep `-lc`, so policy commands still see the image's login environment. The idle process argv is also unchanged.
- This matches the existing backends:
  - The Docker backend's `run_argv` executes argv directly.
  - The native worker launch already uses `-c` ("Not a login shell: the image's /etc/profile would replace the admitted worker PATH").

## 3) Coupling and Generalization Impact

- Harness- and task-neutral. Trusted executions now run exactly the argv the runtime requested, whatever shell startup files the workload has written. No task or image may rely on login-profile side effects inside setup/verifier argv. Those argv already receive their environment from the admitted runtime's fixed environment.

## 4) Change Classification

- Classification: `behavioral-change`. Trusted argv and workspace probes no longer source shell startup files. There is no schema or wire-format change.

## 5) Evidence and Validation Plan

- New real-execution test `tests/rl/harness/test_sandbox_process_integration.py::test_trusted_argv_ignores_policy_written_login_profile`:
  - The policy writes `$HOME/.profile` and `$HOME/.bash_profile` through `run_shell`.
  - A sensitivity check asserts that the policy's own login shell runs the profile.
  - The trusted base commit must then equal `HEAD`, `workspace_diff` must be unchanged, and `execute` must print only the requested argv's output.
- `test_run_argv_executes_requested_command_through_pinned_shell` and `test_workspace_diff_uses_nested_repository_and_types_missing_git` pin `-c`.
- IBM Linux (bash, `/bin/sh` = dash), on the runtime lineage (`e4src/trusted-argv-nonlogin-v11` on runtime-v11 source `ecd17010`, job 129406):
  - Without the change, the three tests fail. The new test fails with `SandboxLaunchError: workspace base commit measurement failed`.
  - With the change, all three pass.
  - `test_verifier_snapshot`, `test_sandbox_runtime`, `test_v2_service`, `test_production_composition_runtime` and `test_headless_runner` have the same results in both trees, as does the rest of `test_sandbox_process_integration`.
- On `main`, the two pinning tests fail before and pass after (job 129373). The sealed test is skipped as `runtime_unsupported` on hosts without namespace/UID mapping.

## 6) Rollout Plan

- RL runtime lineage: `e4src/trusted-argv-nonlogin-v11` builds `bbagent-runtime/v12`.
- Main: this PR.

## 7) Rollback Plan

- Revert the commit. There is no configuration surface.

## 8) Approvals

- This artifact records the implementation decision and its evidence. It does not assert external PR acceptance or BMoE promotion.
