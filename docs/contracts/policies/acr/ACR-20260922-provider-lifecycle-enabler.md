# ACR-20260922-provider-lifecycle-enabler

- `acr_id`: `ACR-20260922-provider-lifecycle-enabler`
- `title`: Bind installed headless provider lifecycle to closed request policy, lossless native responses and owned Docker execution
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-22
- `status`: implemented
- `pull_request`: 129

## 1) Problem Statement

The contained Pi producer from PR128 always streamed provider requests and relied on uncontained runtime assumptions around workspaces and Docker execution. New E4 profiles need a public, capability-bound way to request non-streaming or streaming Chat Completions and to preserve the native response losslessly. Their episodes also need owned Docker execution, bounded project-quota workspaces, and truthful cancellation and cleanup.

Two defects surfaced during review. The streamed-delta decoder had dropped its assistant-role, refusal, `function_call` and `audio` rejection. The non-streaming native path accepted explicit tool-call indices that reordered calls. Both would let malformed provider output appear valid.

The required outcome is a public provider-lifecycle enabler. This PR is not a new E4 profile, not supplier parity, and not official verifier or private training acceptance.

## 2) Scope and Surfaces

- Provider request policy, native response admission and OpenAI Chat streaming/non-streaming runtimes in `breadboard_engine.provider`.
- Server compilation of provider-response contracts in `breadboard_engine.compilation`.
- Headless request v3 (`bb.rl.headless-run-request.v3`) with an explicit closed `provider.request_policy`, and v3 target-input encoding in `breadboard.product.harness.targets`.
- Composition, materialization, project-quota workspaces, the mount-namespace broker, the private Docker daemon, Docker sandbox execution and the Conductor runner in `breadboard.rl.harness`.
- Contract surfaces: provider request policy, native response bindings, headless request versions, workspace quota and cleanup evidence.
- Kernel danger-zone change? yes
- Outside scope: private wrappers, trainers, reward logic and task providers; new or modified E4 target packages; supplier captures; live endpoint qualification; public package release.

## 3) Coupling and Generalization Impact

No core-to-private-extension dependency is introduced. No added import in `breadboard_engine` references the `breadboard` product package; added imports are limited to `breadboard`, `breadboard_engine`, tests and the standard library. The CI kernel-extension boundary guard does not verify this: it skips on this branch because `scripts/validate_kernel_ext_boundaries.py` is absent.

Request policy is a closed, versioned input. Headless requests v1 and v2 omit `request_policy` and keep the historical streaming behavior. Only v3 requests can select non-streaming. Nothing infers a capability from a model name or from a caller flag.

Native responses are validated before argument normalization. Unknown delta semantics, invalid roles, refusal/`function_call`/`audio` fields and reordered tool indices fail closed with typed errors. Accepted tool-call argument text and native fragments are carried unmodified into the projected native response. Raw HTTP response bytes are not retained.

Docker execution, project-quota workspaces and the mount-namespace broker require an owned, measured runtime. They do not turn an arbitrary host into a hardened sandbox or relax the default headless rejection of uncontained execution.

Coupling risk is medium-high. Provider decoding, compilation, headless admission, workspace ownership and cleanup cross several public modules. The accepted `pi@0.57.1` and `oh-my-pi@16.2.13` target assets are unchanged.

## 4) Change Classification

- Classification: `additive`

The request v3 schema, closed request policy and Docker/quota execution paths are new explicit inputs. Existing target identities, headless v1/v2 behavior and canonical episode envelopes are retained. The decoder repairs restore rejection that the unrepaired predecessor `8dee9e5b` had lost. No compatibility alias or private fallback is added.

## 5) Evidence and Validation Plan

The source candidate is `2e032d5e8fd893da1513060c4bf04b0e795c5aac`, tree `cb8a879487e53963baba5397d4262db6ae6226bb`. It is 22 commits over protected main `fda293a38afed8508140d932a6cb71b74411da3d`.

### Source gate references

These jobs executed and passed at `2e032d5e`:

| Evidence | Exact job | Executed work |
| --- | --- | --- |
| Product contract and behavior checks | [Product spine checks](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943749163) | spine and clean-consumer suites |
| Compilation contracts | [Product compilation](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943749139) | 856 passed, 10 skipped |
| CT scenarios and matrix status | [Conformance matrix sync guard](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943749261) | 108 tracked-selection scenarios, status pass |

The CT job uses the tracked clean-checkout scenario selection; it does not establish workspace-dependent campaign evidence.

The same run's [Danger-zone ACR guard](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943749076) failed because no ACR was changed, so run 35786278187 as a whole failed. This ACR is the correction. The ACR guard passes on the fresh checks for this commit.

These jobs reported success at `2e032d5e`, but each skipped its work because tracked prerequisites are absent on this branch. They are not evidence for this change:

| Job | Missing prerequisite |
| --- | --- |
| [Kernel-extension boundary guard](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943748876) | `scripts/validate_kernel_ext_boundaries.py` |
| [Replay determinism guard](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943748947) | `tests/test_check_replay_determinism_gate.py`; the seeded `bb.replay_determinism_report.v1` fixture |
| [Evidence bundle contract guard](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943749492) | `tests/test_evidence_bundle_v1.py` |
| [Conformance gate (ubuntu)](https://github.com/kmccleary3301/breadboard/actions/runs/35786278187/job/106943749397) | `scripts/provider_conformance_report.py`; conformance baselines |

Replay determinism and the kernel-extension boundary are therefore not CI-verified for this PR. `ACR-20260917-contained-pi-headless` names two of the same absent commands (`scripts/validate_kernel_ext_boundaries.py`, `tests/test_evidence_bundle_v1.py`). That is a pre-existing gap and is not repaired here. These references do not waive fresh required checks for the ACR commit.

### Installed evidence at the reviewed head

Retained custody is on the IBM Slurm cluster `foundation2`, node `cnode-14`. These are authenticated operator paths, not public downloads. No credential contents are included.

| Evidence | IBM job | Retained record | SHA-256 |
| --- | --- | --- | --- |
| Exact-source stage of `2e032d5e` | 87471 | `/shared/bbe4-69b01bc5/provider-source-2e032d5e-02/source-receipt.json` | `648ae157428780137f897bc17bbfd83782e57e2d941ae647eed1005b1e71ccb4` |
| Installed wheel/CLI build | 87659 | `/shared/bbe4-69b01bc5/pvr22/controller-result.json` | `28326a4a46fab7c2b4886d12ad2a421e7747fe09648e1cd5c6b5c8a3a28a3b01` |
| Installed streaming success lifecycle | 87665 | `/shared/bbe4-69b01bc5/pvl14/controller-result.json` | `a72b6511a399ac18255ba64a0443d64932194c75ef76ee3dbc260b3054602aa2` |

The lifecycle smoke ran the installed CLI against a scripted loopback endpoint. It completed in 40.6 seconds with exit 0 and two valid provider requests. It produced one workspace effect, verifier score 1 and a published result, events and patch. Cleanup errors were empty, owned PIDs, mounts and loop devices were absent, and protected inputs were unchanged.

### Protocol repair discriminator

The two decoder repairs have focused regressions in `tests/providers/test_native_response.py`. Real HTTP runs on IBM show the tests discriminate:

- Job 86725 on the repaired source: 5 passed.
- Job 86728 on the unrepaired predecessor `8dee9e5b`: 3 failed (invalid role and reordered calls accepted; refusal rejected late with the wrong code).

Job 86728 is a pre-repair discriminator, not exact-head proof.

### Not established by this evidence

Earlier installed cancellation, late-publication, wrong-artifact and HTTP-failure evidence was produced at predecessor sources. It is retained in the campaign record but not claimed for `2e032d5e`. A scripted endpoint does not establish live-provider behavior, official grading, supplier parity or private policy acceptance. The retained semantic evidence comparator is non-blocking assurance and is not a gate for this PR.

## 6) Rollout Plan

Merge PR129 only after exact-head independent acceptance, required checks and resolution of public review threads. Use a merge commit to preserve its 22-commit lineage.

New E4 profiles, supplier captures and live canaries are separate campaign packets. They do not inherit acceptance from this enabler. Do not publish a package or extend supported-platform claims from this source merge.

## 7) Rollback Plan

### Owner, stable state, and triggers

The campaign integration owner, Main, owns rollback execution and incident recording, accountable to Kyle McCleary. The previous protected source baseline is `fda293a38afed8508140d932a6cb71b74411da3d`, tree `b09c64f9cf28f21372e27246579bc8a361435983`.

Stop admitting v3 provider requests and Docker-backed episodes if any of these regress: provider response validation, native response fidelity, request policy enforcement, workspace ownership, quota enforcement, cleanup, replay determinism or kernel boundaries. Quarantine the exact source, wheel and image identities in the campaign record. No provider episode or service is active at this checkpoint. If a later episode is active, use its admitted ownership-bound cancellation procedure; unresolved ownership or cleanup failure blocks replacement admission.

### Rehearsed restoration

The [isolated-index rehearsal receipt](evidence/ACR-20260922-provider-lifecycle-enabler.rollback-rehearsal.json), SHA-256 `7f186896f906636e712a943347f8eca2c51f45f5d80bcc0af673144618af2eda`, records a successful reverse application of the full `fda293a3`-to-`2e032d5e` change. `git write-tree` returned the exact stable tree above. Only a separate `GIT_INDEX_FILE` changed; the worktree, branch, services and retained evidence were untouched.

That rehearsal proves source-tree restoration, not a production rollback or a rebuilt runtime. An actual revert must preserve unrelated later changes.

### Conditional source rollback commands

After an actual trigger, create a normal revert PR from current protected main:

```sh
git fetch origin main
merge_commit="$(gh pr view 129 --repo kmccleary3301/breadboard --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$merge_commit" && test "$merge_commit" != null
git switch -c rollback/pr129 origin/main
git revert -m 1 --no-edit "$merge_commit"
git push -u origin rollback/pr129
rollback_pr="$(gh pr create --repo kmccleary3301/breadboard --base main --head rollback/pr129 --title 'Revert PR129 provider lifecycle enabler' --body 'Rollback PR129 after a recorded provider-lifecycle regression. Preserve the incident, affected artifact identities, and exact-head review evidence.')"
gh pr checks "$rollback_pr" --repo kmccleary3301/breadboard --required --watch
```

Stop on any command failure or conflict; do not reset protected main or discard unrelated edits. Obtain independent exact-head review and resolve public threads before the normal protected merge. There is no admin bypass:

```sh
gh pr merge "$rollback_pr" --repo kmccleary3301/breadboard --merge --match-head-commit "$(git rev-parse HEAD)"
```

### Post-rollback checks and state disposition

Run these checks on the revert candidate in its supported test environment, and preserve their outputs with the required CI results:

```sh
python -m pytest -q tests/providers/test_native_response.py tests/providers/test_openai_profile.py tests/compilation/test_server_compiler.py
python -m pytest -q tests/rl/harness/test_headless_runner.py tests/rl/harness/test_policy_provider.py tests/rl/harness/test_runner_conductor.py tests/rl/harness/test_sandbox_docker.py tests/rl/harness/test_project_quota.py tests/rl/harness/test_private_docker_daemon.py tests/rl/harness/test_mount_namespace_broker.py
```

The required Product spine checks, Product compilation and Conformance matrix sync guard jobs must also execute and pass on the revert PR. The CT selection is computed by the workflow; do not substitute a hand-built scenario list. Where the kernel-extension boundary, replay determinism, evidence bundle or conformance gate jobs still skip for missing prerequisites, record them as not verified rather than passed.

Keep affected installed runtimes disabled; reverting source does not replace a wheel or image already in custody. Build and admit any replacement under a new exact identity with its own installed proof. The previous baseline does not support headless request v3 or non-streaming requests. Callers depending on them must stop rather than fall back.

There is no database or schema migration to reverse. Preserve workspaces, manifests, results, patches, quotas and cleanup inventories. Never fall back to uncontained host execution, unvalidated provider responses or fabricated cleanup success. Rollback does not authorize deletion of campaign or recipient custody.

### Completed PR-local rollback checklist

- [x] Stable source and tree identified; rollback owner and trigger conditions recorded.
- [x] Exact source-restoration commands rehearsed in an isolated index; receipt linked above.
- [x] Scope fixed to this enabler; unrelated changes and retained state must survive.
- [x] Success criteria recorded: source guards pass, affected launches remain disabled, and any replacement passes separately admitted installed proof.
- [x] Abort criteria recorded: failed guards, replay/boundary regressions, unresolved ownership, or cleanup failures prevent promotion.
- [x] Execution disposition recorded: no production rollback was triggered or executed; post-rollback checks above are conditional, not claimed results.
- [x] Incident disposition recorded: on a trigger, Main records the incident, exact affected identities, failed evidence, root-cause owner and due date, and updates this ACR before a separately reviewed forward fix. No rollback incident exists at this checkpoint.

## 8) Approvals

Kyle's accepted execution handoff authorizes public implementation, PRs and protected-gated merges. It does not grant official verifier, private policy or recipient acceptance.

`ProviderStandards8b` and `ProviderSpec8b` reviewed the full packet at `8b1e517a` and the exact repair delta to `2e032d5e`. Both found no new blocking source defect. `ProviderStandards8b` recorded full lifecycle, cancellation and late-publication qualification as unverified and separate. `ProviderSpec8b` recorded campaign promotion as separate and incomplete. Acceptance of this ACR commit remains pending. Final decision is withheld until its exact-head review and required CI pass.
