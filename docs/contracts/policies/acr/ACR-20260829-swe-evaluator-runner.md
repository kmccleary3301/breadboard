# ACR-20260829-swe-evaluator-runner

- `acr_id`: `ACR-20260829-swe-evaluator-runner`
- `title`: Pinned official SWE-bench evaluation runner
- `author`: Codex (RL integration closure)
- `date`: 2026-08-29
- `status`: implemented

## 1) Problem Statement

The E4 RL harness needs a production path from a completed headless workspace patch to an official SWE-bench score. The evaluator must not accept an unbound task, mutable evaluator code, a stale report, an oversized patch or subprocess output, or a receipt that omits cleanup authority.

## 2) Scope and Surfaces

- Canonical `breadboard.rl.harness.swe_bench_runner` request and receipt contracts.
- Exact controller, target, dataset row, image, base commit, patch, evaluator, report, reward, and cleanup binding.
- Official SWE-bench evaluator invocation pinned to release `5.0.1` commit `87ab1f6ced28f75ba73ca899dc759b019310944a`.
- One new root-owned `0700` evaluator working directory per run and exact report-path validation.
- Bounded evaluator process groups, report files, patch input, and cleanup inventory.
- Danger-zone: yes.
- Outside scope: networked dependency resolution during a scored run, multi-row aggregation, and promotion of Docker readiness without the separate live canary.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Official-evaluator specifics remain behind the evaluator protocol and immutable evaluator identity.
- Generalization impact: positive; the controller-facing runner admits another evaluator only when its executable and implementation identities are explicit.
- Coupling risk: medium because task, patch, evaluator report, reward, and cleanup must describe one run.

## 4) Change Classification

- Classification: `additive`.
- Compat reviewed: non-breaking.
- Invalid or incomplete evidence fails closed; no compatibility fallback or inferred score is introduced.

## 5) Evidence and Validation Plan

- Reject task, prompt, target, image, base, patch, evaluator, report, reward, and cleanup identity mismatches.
- Reject symbolic-link, non-regular, oversized, mutable, or non-canonical patch input.
- Hash the complete locked evaluator package tree, exact official commit, self-contained Python runtime, virtual-environment launcher and configuration, and Docker client; require root-owned authority paths with no group/world writes or executable bytecode caches and remeasure immediately before evaluation.
- Reset and clean the sealed Git repository to the measured pinned base before any model action, then require an empty status and bind that state into sandbox measurement.
- Transform only the verified private dataset copy from the pinned mutable image tag to the pinned platform digest, then bind the transformed artifact and live Docker image observation in the receipt.
- Admit evaluation only inside a root-owned private work directory; run every evaluator helper through the measured venv launcher and require its prefix and imported `swebench` package to remain inside the measured root.
- Use a minimal subprocess environment, exact run-scoped container cleanup, process-group termination, and exact return-code/report checks.
- Bind canonical prediction, evaluator input, image observation, report, reward, and cleanup digests in the final receipt.
- Run the focused SWE runner, installed headless, V2 service, and verifier-snapshot suite.
- Require exact-head correctness and security review, the danger-zone ACR guard, and full CI before merge.

## 6) Rollout Plan

1. Merge the runner while Docker remains experimental.
2. Execute the one-row official SymPy canary through the installed wheel and public headless surface.
3. Publish the evaluator receipt only when the official report, patch, reward, and cleanup bindings agree.
4. Promote Docker readiness only in the later capability-matrix change backed by that live receipt.

No release, signing, notarization, direct `main` push, mutable image selection, or implicit dependency installation is authorized by this record.

## 7) Rollback Plan

- Revert this runner without changing the already-merged headless or sandbox contracts.
- Reject and archive receipts whose evaluator identity references the reverted implementation.
- Keep Docker experimental and the SWE qualification result false until an exact replacement is reviewed and proven.

## 8) Approvals

- SWE runner correctness reviewer: required on the exact PR head.
- SWE runner security reviewer: required on the exact PR head.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge by itself.
