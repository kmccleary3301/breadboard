# ACR-20260923-mini-swe-agent-source-profile

- `acr_id`: `ACR-20260923-mini-swe-agent-source-profile`
- `title`: Replay mini-swe-agent 2.4.6 in BreadBoard, proven against an independent supplier capture
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-23
- `status`: implemented

## 1) Problem Statement

The mini-swe-agent 2.4.6 profile needs BreadBoard to run Mini's source loop through its own admitted runtime. Proof means matching an independent capture of the supplier package on the same scripted endpoint, case for case. BreadBoard-only cases may be judged only on controls the operator records, with the source of each fact stated.

The first installed capture (mci013) failed 55 of 95 assertions. The failures came from BreadBoard runtime defects, not supplier behavior:

- Model responses and provider errors did not go through Mini's locked LiteLLM client.
- A raw-output overrun was reported as a generic tool failure.
- Mini tools ran in the workspace root instead of the repository the sealed diff reads.
- A failed run's typed failure was absent from `result.json`.
- Replayed assistant messages lost `provider_specific_fields`. The Mini consumer id was passed as `agent_config` instead of `context.extra`, so the engine's Mini request branches never ran.

The required outcome is exact trace equality on every supplier-comparable case, plus operator-recorded BreadBoard-only controls. It is not live-model, official-benchmark, or private-training acceptance.

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

The runtime candidate is `56cea6528e148c35ac9e25e128522a7880e73f9a`, tree `6ecb40a3950b03f3ad24254624b14c76e7afdb77`, over protected main `c39054e9afd1457060028148d45e96d0c98db9d6`. Later commits change documentation, tests, lane status, conformance artifacts and one wheel input, `conformance/comparators/mini_swe_agent.py`. Neither the capture operators nor the runtime import that comparator; evidence tooling loads it through the comparator registry at compare time. Every other wheel input is byte-identical to the runtime candidate.

### Installed build and capture

Both runs used IBM `foundation2`, node `cnode-14`, standing Slurm authority, and control root `/shared/bbe4-69b01bc5/`. These are authenticated operator paths, not public downloads.

| Artifact | Retained path | SHA-256 |
| --- | --- | --- |
| Source archive (`git archive` restricted to `_WHEEL_INPUT_PATHS`) | `mcs-56cea652/source.tar` | `6b9d28d1db740eeff1cf5ed168b7f42b0cf829b93dc103bd0d812806e4ef0125` |
| Installed SIF assembly | `mcb-56cea652/assembly.json` | `0b471acf3750c105b9d1078906267b5a02a80807af7ad7e5ad75ee2307925079` |
| Capture spec | `mcs-mci023/spec.json` | `d86bc6d14dd71acc7ae699da36e0c856b32e4893c931a3e709bb54da4a38656f` |
| Capture result | `mcr-mci023/result.json` | `32a32af76dfafa38da90f6bda9331cee819c95e95accaf38a8c8ec0dadce60ad` |
| Evidence archive | `mcc-mci023/mini-capture-evidence.tar.gz` | `63650b3145f2eb575bbb6620da90d556f30ec2ee575c4034a114e2af6d5ee0af` |

Each case runs twice against the same scripted receiver, script SHA-256 `5bd8c00bdcc3d16694c74eb27c20d204ead16b6002a891e284648ca69c2e8325`:

- **Supplier:** the unmodified `mini-swe-agent==2.4.6` package, driven through its native config.
- **BreadBoard:** the installed `python -m breadboard.rl.harness run` inside the assembled SIF.

The operators apply only two declared placeholders, `<TIMESTAMP>` and `<TRACEBACK>`, each in its admitted history `extra` field. The comparator rejects a placeholder outside its field and any other declared rule, including a workspace-root rule; no published trace needs one. The retained operator SHA-256s are:

| Operator | SHA-256 |
| --- | --- |
| `mini_capture_cases.json` | `d49871b4dddd0ca8dd830f106d3ac7bd5d75dde5e2b15430d2f196664e16716b` |
| `mini_capture_supplier.py` | `5b53879050a2904994e535e0bf5514b0407d11b9f74c3de46ef388c91bf0b440` |
| `mini_capture_breadboard.py` | `5328ddce5c3ccb7edfb0e4c5feae6945a773351f0a5a86b26e1968f452f31be5` |
| `mini_capture_probe.py` | `2a01e1200c102d6da952cd763de398454f0db649b56657ebb1dcf8f052c06b26` |
| `ibm_mini_sif_assembly.py` | `7623246137c41d01663b3eab0695c5540c282dbd83778f1a8f77e02eceb039a6` |

### Comparator result

The published packet is under `docs/conformance/e4_target_support/mini_swe_agent_2_4_6_replay/`: 14 supplier traces, 17 BreadBoard traces, both manifests and the run receipt. `run_lane.py --lane mini_swe_agent_2_4_6_replay --stage compare` returns `executed_pass` with 126 passed, 0 failed and 0 errors.

Fourteen cases compare the scenario hash, requests, history, exit, effects and counters by exact JSON equality: grouped batches, format errors, the error-streak reset, the submission guard, the 10,000-character output boundary, the native command timeout, provider 429/5xx exits, the provider timeout, the eighth-call guard, malformed cost and the 1 MiB raw boundary.

Three BreadBoard-only cases are judged by `{path, equals}` expectations. The comparator admits only paths under the operator-built `controls` object, so no expectation can stand in for a supplier-comparable trace field. Controls come from two sources:

- Observed by the operator, independent of BreadBoard: the headless exit code, HTTP attempts counted by the scripted receiver, file effects from applying BreadBoard's sealed patch to a clean checkout, the externally counted raw output bytes, and the cancel trigger.
- Read by the operator from BreadBoard's consumer-visible outputs: `result.json` terminal status, `run_failure`, `primary_failure`, patch availability, cleanup and leak inventory, and the committed ledger's history roles. These show what a consumer of the run sees. They are not independent observations.

The three cases:

- `raw_cap_over_limit`: an externally counted 1,048,577-byte output yields `run_failure {runtime, native_output_limit_exceeded}` and exit code 1. History stops at `system, user, assistant`, the receiver saw one HTTP attempt, there is no patch, cleanup is `released`, and zero resources leaked.
- `shared_control_fault`: the operator observed the tool's effect bytes (SHA-256 `01ca51b1…`) while headless was still running, then sent SIGINT at 15.4 s, inside the tool's 25 s sleep. The run exits 130 with `run_failure {cancellation, process_interrupted}`. No observation is committed and no patch is fabricated; cleanup is released with nothing leaked.
- `workspace_effect_sealed`: a write in Mini's tool cwd appears, with the expected bytes, in the checkout produced by applying the sealed repository diff, and the run succeeds.

### Source gates

In `uv` Python 3.11 with the pinned requirements, the focused suites passed:

- 567 with `litellm==1.101.0`; 565 plus 2 skipped without it.
- The provider suites.

The suites cover the comparator, lane runner, lane definitions, targets, wheel packaging, Mini semantics, policy provider, Mini tools, sandbox runtime, runner conductor, headless and v2 service. `test_mini_wire_messages_keep_source_client_fields` fails on the pre-fix engine and passes after it. The comparator suite's scenario-hash, workspace-rule and trace-rooted-oracle rows fail against the pre-review comparator and pass after it.

### Findings recorded, not changed here

- The sealed diff stages with `add --all --force`, so ignored files that already exist in a task image (for example astropy build products) appear in its patch. This is the existing snapshot-fidelity design, not a Mini divergence. The supplier comparison does not read the patch.
- mci013 through mci022 are diagnostic iterations and are not evidence. mci022 passed 113 assertions, but under a comparator that neither compared scenario hashes nor restricted oracles to controls. The failures drove the fixes above and the case corrections:
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
