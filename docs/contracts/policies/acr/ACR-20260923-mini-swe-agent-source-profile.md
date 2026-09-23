# ACR-20260923-mini-swe-agent-source-profile

- `acr_id`: `ACR-20260923-mini-swe-agent-source-profile`
- `title`: Replay mini-swe-agent 2.4.6 in BreadBoard, proven against an independent supplier capture
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-23
- `status`: implemented

## 1) Problem Statement

The mini-swe-agent 2.4.6 profile needs BreadBoard to run Mini's source loop through its own admitted runtime. Proof means matching an independent capture of the supplier package on the same scripted endpoint, case for case. BreadBoard-only controls must be judged on observed values, not self-report.

The first installed capture (mci013) failed 55 of 95 assertions. The failures came from BreadBoard runtime defects, not supplier behavior:

- Model responses and provider errors did not go through Mini's locked LiteLLM client.
- A raw-output overrun was reported as a generic tool failure.
- Mini tools ran in the workspace root instead of the repository the sealed diff reads.
- A failed run's typed failure was absent from `result.json`.
- Replayed assistant messages lost `provider_specific_fields`. The Mini consumer id was passed as `agent_config` instead of `context.extra`, so the engine's Mini request branches never ran.

The required outcome is exact trace equality on every supplier-comparable case, plus observed BreadBoard-only controls. It is not live-model, official-benchmark, or private-training acceptance.

## 2) Scope and Surfaces

- Mini response, request and provider-failure projection in `breadboard.rl.harness.policy_provider`. The locked `litellm==1.101.0` functions are the ones Mini's `LitellmModel` calls: `validate_and_fix_openai_messages`, `exception_type` and the `ModelResponse` conversion.
- Profile-bound request projection in `breadboard_engine.provider.runtimes.openai.chat`. Mini messages are sent as given and are not rebuilt from BreadBoard's canonical message shape.
- Conductor Mini exit commits (`breadboard.rl.harness.runners.conductor`, `runners/base.py`) and the shared `MiniProviderFailure`.
- Native tool cwd and the `native_output_limit_exceeded` outer error in `breadboard.rl.harness.sandbox`.
- `V2RunResult.primary_failure` and the `terminal.run_failure` projection in `service.py` and `headless.py`.
- The `mini_swe_agent_trace_v1` comparator, schema-checked comparator registry admission, and lane `config/e4_lanes/mini_swe_agent_2_4_6_replay.yaml`.
- The wheel-input fix for the target package in `setup.py`.
- Kernel danger-zone change? yes
- Outside scope:
  - the other five E4 profiles
  - live provider or model quality
  - official SWE-bench grading
  - private wrappers, trainers and reward logic
  - lane acceptance or points
  - changes to the sealed-diff snapshot design (see Section 5, Findings)

## 3) Coupling and Generalization Impact

No core-to-private-extension dependency is introduced. LiteLLM is imported lazily and only on the Mini consumer path. Non-Mini consumers keep BreadBoard's canonical request conversion, which `test_mini_wire_messages_keep_source_client_fields` contrasts directly.

The engine branch is keyed by the existing compiled `MINI_RESPONSE_CONSUMER_ID` carried in `ProviderRuntimeContext.extra`, not by model name or endpoint. No supplier agent loop, bespoke runner or opaque supplier tool is added. BreadBoard's conductor runs Mini's source-derived phases and its pinned tools.

`run_failure` is additive: consumers that read only `terminal.status` (for example `swe_bench_runner`) are unchanged. The accepted `pi@0.57.1` and `oh-my-pi@16.2.13` target assets are untouched. Coupling risk is medium because request projection, exit commits, tool cwd and result publication cross public modules.

## 4) Change Classification

- Classification: `additive`

These are fixes to shared lifecycle behavior. The new projections apply only under the Mini consumer id. The failure fact and the output-limit code add fields without changing existing ones. No compatibility alias or fallback path is added.

## 5) Evidence and Validation Plan

The runtime candidate is `56cea6528e148c35ac9e25e128522a7880e73f9a`, tree `6ecb40a3950b03f3ad24254624b14c76e7afdb77`, over protected main `c39054e9afd1457060028148d45e96d0c98db9d6`. Later commits on this branch change only documentation, lane status and conformance artifacts, not wheel inputs.

### Installed build and capture

Both runs used IBM `foundation2`, node `cnode-14`, standing Slurm authority, and control root `/shared/bbe4-69b01bc5/`. These are authenticated operator paths, not public downloads.

| Artifact | Retained path | SHA-256 |
| --- | --- | --- |
| Source archive (`git archive` restricted to `_WHEEL_INPUT_PATHS`) | `mcs-56cea652/source.tar` | `6b9d28d1db740eeff1cf5ed168b7f42b0cf829b93dc103bd0d812806e4ef0125` |
| Installed SIF assembly | `mcb-56cea652/assembly.json` | `0b471acf3750c105b9d1078906267b5a02a80807af7ad7e5ad75ee2307925079` |
| Capture spec | `mcs-mci022/spec.json` | `bf4249dbaa78f850c560cd3c38ebb596843958125818d608704dd5f5dadfc763` |
| Capture result | `mcr-mci022/result.json` | `868ed1238d5c531aabd7c4b3136b821b3ba756e27e3ef573f454cc2f98408d53` |
| Evidence archive | `mcc-mci022/mini-capture-evidence.tar.gz` | `b4a811e2659694d52075b40f8b2c77c3e19699e22952a40b82f65002f650cbd8` |

Each case runs twice against the same scripted receiver, script SHA-256 `5bd8c00bdcc3d16694c74eb27c20d204ead16b6002a891e284648ca69c2e8325`:

- **Supplier:** the unmodified `mini-swe-agent==2.4.6` package, driven through its native config.
- **BreadBoard:** the installed `python -m breadboard.rl.harness run` inside the assembled SIF.

The operators apply only the declared placeholders: `<WORKSPACE>`, `<TIMESTAMP>` and `<TRACEBACK>`, each in its admitted field. The comparator rejects any placeholder found outside its field. The retained operator SHA-256s are:

| Operator | SHA-256 |
| --- | --- |
| `mini_capture_cases.json` | `249bd2ae74b897c78d7cf76b8e4255e1121d03169b1cc71ff0fa2f06db64916a` |
| `mini_capture_supplier.py` | `5b53879050a2904994e535e0bf5514b0407d11b9f74c3de46ef388c91bf0b440` |
| `mini_capture_breadboard.py` | `52203bc313ceef3c686049e7baeab5e7e432ab7680f65064acb4da5d35d660c3` |
| `mini_capture_probe.py` | `2a01e1200c102d6da952cd763de398454f0db649b56657ebb1dcf8f052c06b26` |
| `ibm_mini_sif_assembly.py` | `7623246137c41d01663b3eab0695c5540c282dbd83778f1a8f77e02eceb039a6` |

### Comparator result

The published packet is under `docs/conformance/e4_target_support/mini_swe_agent_2_4_6_replay/`: 14 supplier traces, 17 BreadBoard traces, both manifests and the run receipt. `run_lane.py --lane mini_swe_agent_2_4_6_replay --stage compare` returns `executed_pass` with 113 passed, 0 failed and 0 errors.

Fourteen cases compare requests, history, exit, effects and counters by exact JSON equality: grouped batches, format errors, the error-streak reset, the submission guard, the 10,000-character output boundary, the native command timeout, provider 429/5xx exits, the provider timeout, the eighth-call guard, malformed cost and the 1 MiB raw boundary.

Three BreadBoard-only controls are judged on observed `{path, equals}` values:

- `raw_cap_over_limit`: an externally counted 1,048,577-byte output yields `run_failure {runtime, native_output_limit_exceeded}` and exit code 1. History stops at `system, user, assistant`, there is one HTTP attempt and no patch, cleanup is `released`, and zero resources leaked.
- `shared_control_fault`: the operator observed the tool's effect bytes (SHA-256 `01ca51b1…`) while headless was still running, then sent SIGINT at 15.1 s, inside the tool's 25 s sleep. The run exits 130 with `run_failure {cancellation, process_interrupted}`. No observation is committed and no patch is fabricated; cleanup is released with nothing leaked.
- `workspace_effect_sealed`: a write in Mini's tool cwd appears in the sealed repository diff and the run submits.

### Source gates

In `uv` Python 3.11 with the pinned requirements, the focused suites passed:

- 623 with `litellm==1.101.0`; 621 plus 2 skipped without it.
- The provider suites.

The suites cover the provider differential, comparator, lane runner, C4 chain, targets, Mini semantics, policy provider, Mini tools, sandbox runtime, runner conductor, headless and v2 service. `test_mini_wire_messages_keep_source_client_fields` fails on the pre-fix engine and passes after it.

### Findings recorded, not changed here

- The sealed diff stages with `add --all --force`, so ignored files that already exist in a task image (for example astropy build products) appear in its patch. This is the existing snapshot-fidelity design, not a Mini divergence. The supplier comparison does not read the patch.
- mci013 through mci021 are diagnostic iterations and are not evidence. Their failures drove the fixes above and the case corrections:
  - quote-free raw-cap commands
  - deterministic timeout effect bytes
  - effect-triggered cancellation instead of an in-sandbox marker
- LiteLLM needs `LITELLM_LOCAL_MODEL_COST_MAP=True` in both roles so cost is computed offline. The launch sets it; it is not inferred from the environment.

## 6) Rollout Plan

Merge the PR only after green required checks on its exact head, independent exact-head review, and resolved review threads. Preserve the branch lineage with a merge commit.

The lane moves from `scaffolded` to `compared`, with 0 points. Claim, acceptance and points are separate external gates. Live-provider qualification and the remaining five profiles are separate packets that do not inherit this evidence.

## 7) Rollback Plan

### Owner, stable state, and triggers

Main, the campaign integration owner, owns rollback execution and incident recording, accountable to Kyle McCleary. The stable source baseline is protected main `c39054e9afd1457060028148d45e96d0c98db9d6`, tree `83415f73d57fb48d90d16962337fdd0053cf206d`.

Stop admitting the Mini profile if any of these regress:

- request or response projection
- provider exit mapping
- tool cwd
- output-limit reporting
- failure publication
- cleanup
- replay determinism
- contract validation
- kernel boundaries

Quarantine the exact source and assembly identities in the campaign record. No Mini producer process is active at this checkpoint.

### Rehearsed restoration

The [isolated-index rehearsal receipt](evidence/ACR-20260923-mini-swe-agent-source-profile.rollback-rehearsal.json) records a reverse application of the full branch delta onto a separate `GIT_INDEX_FILE`. `git write-tree` returned the stable tree above. The worktree, branch and retained evidence were untouched. This proves source-tree restoration only, not a production rollback or a rebuilt runtime.

### Conditional source rollback commands

After an actual trigger, open a normal revert PR from current protected main:

```sh
git fetch origin main
merge_commit="$(gh pr view "$pr" --repo kmccleary3301/breadboard --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$merge_commit" && test "$merge_commit" != null
git switch -c rollback/mini-source-profile origin/main
git revert -m 1 --no-edit "$merge_commit"
git push -u origin rollback/mini-source-profile
```

Stop on any conflict. Never reset protected main or discard unrelated edits. Merge the revert only after independent exact-head review and required checks, with `gh pr merge --merge --match-head-commit`; there is no admin bypass.

### Post-rollback checks and state disposition

Run these on the revert candidate and keep their outputs:

```sh
python -m pytest -q tests/rl/harness/test_policy_provider.py tests/rl/harness/test_mini_semantics.py tests/rl/harness/test_mini_tools.py tests/rl/harness/test_runner_conductor.py tests/rl/harness/test_headless_runner.py tests/rl/harness/test_v2_service.py tests/e4_parity/test_run_lane.py
git diff --name-only origin/main...HEAD > changed.txt && python scripts/check_danger_zone_acr.py --changed-files-file changed.txt
```

The CI kernel-extension boundary job currently skips because `scripts/validate_kernel_ext_boundaries.py` is absent; it is not claimed as a rollback check. There is no schema or data migration to reverse. Keep the retained IBM build and capture custody: rollback does not authorize deleting it. A replacement build is admitted under a new exact identity with its own capture proof.

### Completed PR-local rollback checklist

- [x] Stable source and tree identified; rollback owner and trigger conditions recorded.
- [x] Source restoration rehearsed in an isolated index; receipt linked above.
- [x] Scope fixed to this change; unrelated changes and retained custody must survive.
- [x] Success criteria recorded: source guards pass, the Mini profile stays disabled, and any replacement passes its own capture proof.
- [x] Execution disposition recorded: no production rollback was triggered or executed; post-rollback checks are conditional, not claimed results.

## 8) Approvals

Kyle's execution handoff authorizes public implementation, PRs and protected merges. It does not grant lane acceptance, points, or official or private acceptance.

Decision: withheld until independent exact-head review and required CI pass on the PR head.
