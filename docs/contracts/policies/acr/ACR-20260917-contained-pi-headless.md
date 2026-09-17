# ACR-20260917-contained-pi-headless

- `acr_id`: `ACR-20260917-contained-pi-headless`
- `title`: Bind pinned Pi tools to contained installed headless execution
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-17
- `status`: implemented

## 1) Problem Statement

The installed headless producer could not execute the accepted Pi tool implementation inside the supplied Apptainer image. Its composition lacked the measured native binding, and the existing process backend needed explicit outer-containment admission. Installed execution also exposed defects in executable discovery, mode preservation, Linux sealing constants, nullable tool budgets, stdin handling, and final JSON publication.

The required outcome is a contained producer that invokes the pinned tools, preserves real workspace effects, publishes the native result and patch, and reports cancellation and cleanup truthfully. It is not official benchmark or private training acceptance.

## 2) Scope and Surfaces

- Composition and headless admission in `breadboard.rl.harness`.
- Native Pi dispatch through `pi_tools.mjs`, measured executable bindings and the existing Conductor tool port.
- Materialization, process execution, Docker snapshot acquisition and verifier snapshot handoff.
- Nullable compiled per-turn tool limits and recursive thawing at JSON publication.
- Contract surfaces: composition configuration, native tool bindings, process results, artifact modes and cleanup evidence.
- Kernel danger-zone change? yes
- Outside scope: private wrappers, trainers, reward logic and task providers; provider request-policy changes; new E4 profiles; a complete generic SIF qualification; public package release.

## 3) Coupling and Generalization Impact

No core-to-private-extension dependency is introduced. The native bridge resolves the pinned public Pi implementation through admitted materialized paths. It does not contain a supplier agent loop or obtain additional tool authority.

Outer Apptainer admission is explicit. It does not turn an arbitrary host process into a hardened sandbox, replace measurements with a caller flag, or relax the unchanged default headless rejection of uncontained trusted-process execution.

Mode preservation and stdin collection repair shared process behavior. Native response publication converts immutable runtime containers to JSON-compatible values without changing their nested content. Coupling risk is medium because admission, executable identity, snapshots and cleanup cross several public modules.

The accepted pi@0.57.1 and oh-my-pi@16.2.13 target assets remain unchanged. Runtime source and artifact identities change. No new parity or endpoint capability is inferred.

## 4) Change Classification

- Classification: `additive`

The change also corrects shared lifecycle behavior. Existing target identities and canonical episode envelopes are retained. The new containment selection and measured native bindings are explicit composition inputs. No compatibility alias or private fallback is added.

## 5) Evidence and Validation Plan

The source candidate is `c239f65adb6cc8b6fb9afe607c98682e228da450`, tree `e994fcc1ca2d8bfe118bf00be5a5ae7e26839ccf`. Independent authority and execution reviews accepted that exact candidate. The installed producer completed eight controlled turns using all seven accepted tools, produced a real patch, and passed controlled SIGINT exit-130 and authenticated external cleanup observations.

### Source gate references

The following jobs succeeded at documentation successor `39094215d4f96f29cc2276b573327d71d76a11a0`. That successor changed no runtime source relative to c239f65a.

| Evidence | Exact job |
| --- | --- |
| Product contract and behavior checks | [Product spine checks](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115790076) |
| Compilation contracts | [Product compilation](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115790143) |
| Kernel/extension boundaries | [Boundary guard](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115789992) |
| CT scenarios and matrix status | [Conformance matrix sync guard](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115789927) |
| Conformance regression gate | [Conformance gate (Ubuntu)](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115789962) |
| Replay determinism | [Replay determinism guard](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115789938) |
| Evidence bundle validation | [Evidence bundle contract guard](https://github.com/kmccleary3301/breadboard/actions/runs/35194924479/job/105115789894) |

The CT job uses the tracked clean-checkout scenario selection in `.github/workflows/ci.yml`; it does not establish workspace-dependent C4 campaign evidence. The replay job validates its seeded fixture, not a new live-provider replay. These references do not waive fresh required checks for subsequent ACR edits.

### Installed producer evidence

Retained custody is on `ZYPHRA_DO_AMD_1`; these are authenticated operator paths, not public downloads. No credential contents are included here.

| Artifact | Retained path | SHA-256 |
| --- | --- | --- |
| Launch bundle; 87 verified members | `/shared/bbctl1-6fad46cda941/legacy-pi/handoff-pi-0.57.1-c239f65a/pi-0.57.1-sif-launch-bundle.tar.gz` | `5d28f8459030a0066725941df6930257444c7c299dc213754c6afbc549a8c67f` |
| Bundle member manifest | `/shared/bbctl1-6fad46cda941/legacy-pi/handoff-pi-0.57.1-c239f65a/manifest.json` | `d39b6b06a1d4021ac2d8c9a6cb6254ee4581da477fc3cf73550793013ac679a5` |
| Execution receipt | `/shared/bbctl1-6fad46cda941/legacy-pi/episode-probe-85miwf73/receipt.json` | `7e9cccdccc610efeb677efe86156a18f97502748aec9aec62af9bc080be18a75` |
| Real patch application | `/shared/bbctl1-6fad46cda941/legacy-pi/episode-probe-85miwf73/work/patch-application-proof.json` | `9776943497fc8129ca3bf295bf5bf0932b54aa145f9cc455df0a2556637fa66d` |
| Cancellation receipt | `/shared/bbctl1-6fad46cda941/legacy-pi/cancel-probe-_8yorzxg/receipt.json` | `e17657a90560cfbbc4412d2b92829e21c057d6458c3303cb6babe646f77c8187` |
| Post-cancellation ownership observation | `/shared/bbctl1-6fad46cda941/legacy-pi/cancel-probe-_8yorzxg/work/cancellation-after.json` | `b2f056a0721943554cf0b08904b0362067c6828dc26359c32445074c3c58ef88` |

This is producer-scoped evidence. A scripted endpoint does not establish live Qwen, official grading or private policy acceptance. Installed proof remains tied to c239f65a; documentation successors do not claim newly built runtime artifacts. The initial danger-zone rejection and both documentation review rejections remain in the campaign record.

## 6) Rollout Plan

Merge PR128 only after exact-head independent acceptance, required checks and resolution of public review threads. Preserve its ten-commit producer lineage. The provider-enabler work is a separate child packet and does not inherit lifecycle, native-response or official acceptance from this producer.

Final merged installed artifacts and generic SIF worker qualification remain separate campaign gates. Do not publish a package or extend supported-platform claims from this source merge.

## 7) Rollback Plan

### Owner, stable state, and triggers

The campaign integration owner, Main, owns rollback execution and incident recording, accountable to Kyle McCleary. The previous protected source baseline is PR127 commit `b8fbff06b4e6a6990be7b5fdef9f225aaaa8ea24`, tree `4f8fb0d1b373d784cca8ae77b77493d2bf81ac26`.

Stop admitting the affected producer if executable identity, containment, snapshot fidelity, publication, cleanup, contract validation, replay determinism, or kernel boundaries regress. Quarantine the exact source/image/package identities in the campaign record. No producer process or service is active at this source-promotion checkpoint. If a later episode is active, use its admitted ownership-bound cancellation procedure; unresolved ownership or cleanup failure blocks replacement admission.

### Rehearsed restoration

The [isolated-index rehearsal receipt](evidence/ACR-20260917-contained-pi-headless.rollback-rehearsal.json), SHA-256 `41a0351bb0bbfd8e16affdc697e3f2271bc6a129f0dab4d03c95cc5eb8d01855`, records a successful reverse application of the full b8fbff06-to-39094215 change. `git write-tree` returned the exact stable tree above. Only a separate `GIT_INDEX_FILE` was changed; the worktree, branch, running services, and retained evidence were untouched.

That rehearsal proves source-tree restoration, not a production rollback or a rebuilt runtime. It covers the original branch delta; an actual revert must preserve unrelated later changes.

### Conditional source rollback commands

After an actual trigger, create a normal revert PR from current protected main. PR128 must have been merged with a merge commit, preserving its producer lineage:

```sh
git fetch origin main
merge_commit="$(gh pr view 128 --repo kmccleary3301/breadboard --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$merge_commit" && test "$merge_commit" != null
git switch -c rollback/pr128 origin/main
git revert -m 1 --no-edit "$merge_commit"
git push -u origin rollback/pr128
rollback_pr="$(gh pr create --repo kmccleary3301/breadboard --base main --head rollback/pr128 --title 'Revert PR128 contained Pi producer' --body 'Rollback PR128 after a recorded producer regression. Preserve the incident, affected artifact identities, and exact-head review evidence.')"
gh pr checks "$rollback_pr" --repo kmccleary3301/breadboard --required --watch
```

Stop on any command failure or conflict; do not reset protected main or discard unrelated edits. Obtain independent exact-head review and resolve public threads before the normal protected merge. There is no admin bypass:

```sh
gh pr merge "$rollback_pr" --repo kmccleary3301/breadboard --merge --match-head-commit "$(git rev-parse HEAD)"
```

### Post-rollback checks and state disposition

Run these checks on the revert candidate, in its supported test environment; preserve their outputs and the required CI results:

```sh
python -m pytest -q tests/rl/harness/test_headless_runner.py tests/rl/harness/test_materialization.py tests/rl/harness/test_runner_conductor.py tests/rl/harness/test_sandbox_process_integration.py tests/rl/harness/test_sandbox_runtime.py tests/rl/harness/test_verifier_snapshot.py
python scripts/validate_kernel_ext_boundaries.py --json
python scripts/run_conformance_matrix.py --schema-dir docs/conformance/schemas --fixtures-dir tests/fixtures/conformance_v1/fixtures
python scripts/check_replay_determinism_gate.py --glob 'tests/fixtures/conformance_v1/fixtures/valid/bb.replay_determinism_report.v1.valid.json'
python -m pytest -q tests/test_evidence_bundle_v1.py
```

Required CI must also pass its tracked CT selection and conformance regression checks. Keep the affected installed producer disabled: reverting source does not replace an image already in custody. Build and admit any replacement under a new exact identity, with its own applicable installed proof. The previous baseline is not claimed to support this newly added native Pi launch.

There is no database or schema migration to reverse. Preserve workspaces, manifests, results, patches, signing context and resource inventories. Never fall back to uncontained host execution, mutable native binaries, or fabricated cleanup success. Rollback does not authorize deletion of campaign or recipient custody.

### Completed PR-local rollback checklist

- [x] Stable source and tree identified; rollback owner and trigger conditions recorded.
- [x] Exact source-restoration commands rehearsed in an isolated index; receipt linked above.
- [x] Scope fixed to this producer change; unrelated changes and retained state must survive.
- [x] Success criteria recorded: source guards pass, affected launches remain disabled, and any replacement passes separately admitted installed proof.
- [x] Abort criteria recorded: failed guards, replay/boundary regressions, unresolved ownership, or cleanup failures prevent promotion.
- [x] Execution disposition recorded: no production rollback was triggered or executed; post-rollback checks above are conditional, not claimed results.
- [x] Incident disposition recorded: on a trigger, Main records the incident, exact affected identities, failed evidence, root-cause owner and due date, and updates this ACR before a separately reviewed forward fix. No rollback incident exists at this checkpoint.

## 8) Approvals

Kyle's accepted execution handoff authorizes public implementation, PRs and protected-gated merges. It does not grant official verifier, private policy or recipient acceptance.

`PiNativeAuthorityReview2` and `PiNativeExecutionReview2` accepted source c239f65a. Acceptance of this documentation successor remains pending. Final decision is withheld until its exact-head review and required CI pass.
