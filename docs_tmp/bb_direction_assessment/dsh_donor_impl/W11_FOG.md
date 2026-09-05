# W11 fog research

## Decision rule

Each route gets one bounded run after its named prerequisites. The run may close or defer a route; it cannot silently create a product seam. Dispositions are `ADOPT-NOW`, `ADOPT-LATER` with a named trigger, or `CLOSE`. Any public-contract work still requires the W9 approval route.

## RESEARCH-CODE-MODE — pre-registration

- **Prerequisite:** W6.1. Its verdict is `EXPRESSIBLE` only as Definition/compiler intent, not as a distinct runtime protocol.
- **Question:** does a separately named Code Mode improve a deterministic coding-tool policy task over the existing standard Operation runtime?
- **Existing arm:** one valid Definition mode named `coding` with `run_shell` enabled and `write`/`apply_patch` disabled.
- **Code arm:** the same Definition, task, registry, and allow/deny policy, with only the mode name changed to `code`.
- **Correctness metric:** four required facts in the compiled authority: selected mode; `run_shell` enabled; `write` and `apply_patch` disabled; both arms use the same registry and authority path. Required score: 4/4 in both arms. Any regression kills adoption.
- **Cost metric:** canonical effective-configuration bytes and median compile latency over 100 warm repetitions. A separately named protocol must reduce bytes or latency by at least 10% without correctness loss. Equal standard-runtime behavior is not a gain.
- **Bound:** one process, 100 warm repetitions per arm, no provider or network access.
- **Kill:** any correctness regression, or no pre-registered cost gain.
- **Result:** both arms scored 4/4. Existing `coding`: 433 canonical bytes, 607,667 ns median. Renamed `code`: 429 bytes, 616,062 ns median. The 0.924% byte reduction missed the 10% threshold and latency regressed 1.382%. The standard Operation runtime already carries the same tool registry and authority facts; a distinct protocol adds no measured value.
- **Disposition:** **CLOSE**. Reopen only for a named consumer with a pre-registered correctness capability that the standard Operation runtime cannot express.

## RESEARCH-DYNAMIC-PACKAGES — pre-registration

- **Prerequisites:** W5 execution worlds and W3 immutable generation route.
- **Question:** can one named research-comparison consumer generate and execute a disposable package reproducibly without ambient host authority?
- **Run:** generate a package containing a fixed pure comparator in two fresh isolated local execution worlds from the same immutable generation input; execute it; compare package-tree, stdout, stderr, and side-effect digests.
- **Correctness metric:** both runs return the expected comparison result and identical four-digest tuples.
- **Authority metric:** the package can access only the declared working tree and cannot observe a host-only sentinel outside it.
- **Bound:** two fresh worlds, no network, one package, one consumer, then authenticated cleanup.
- **Kill:** ambient authority, unequal package/output digests, or absence of the named consumer.
- **Result:** two fresh `alpine:3.20` OCI worlds ran with `--network none`, read-only root filesystems, all capabilities dropped, `no-new-privileges`, isolated named volumes, and no host-sentinel mount. Both exited 0 with `{\"delta\":2,\"winner\":\"candidate\"}`. Both produced package-tree `sha256:064e19436c58c9721ce67061da72bb6be4b204506baaa1dad1dbcf462afbad5d`, stdout `sha256:ea19c96f45bcd53f16048ab99f3cf502c4ca9fb9661991059d48745b2d6bf45a`, empty-stderr `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`, and side-effect `sha256:ae0246d07cb3808cec8dabc4b0c24dc502be5ef5a9c6214ddd42937f9b8b45ca`. Containers and volumes were removed after capture.
- **World deviation:** the pre-registration said local worlds; the measured worlds were OCI containers. The authority result is limited to those container boundaries, not local host isolation. No local-world equivalence is inferred.
- **Evidence limit:** the image was selected by mutable tag, not a retained immutable image identity. The result proves equality of those two contemporaneous runs, not reproducibility from immutable world inputs. The comparator was invoked directly; no installed `bb research compare` consumer participated.
- **Disposition:** **CLOSE**. The pre-registered missing-consumer kill condition fired; the earlier `ADOPT-LATER` disposition was unsupported. Reopen only when a named installed consumer needs a user-supplied comparator that its existing Operation cannot express, with a new pre-registration that pins the OCI image before execution. No package subsystem is authorized by this experiment.

## RESEARCH-ADVANCED-CLIENTS — pre-registration

- **Prerequisites:** W9 `PUBLIC-AMEND` landed **and** the W10.1 `bb research compare` journey built and installed with its approved side-session contract. Public-schema availability alone does not supply an executable consumer.
- **Question:** can one named advanced-client consumer run a bounded side session and return an accepted result without weakening snapshot, merge, rollback, authority, compaction, or compatibility laws?
- **Consumer:** the installed `bb research compare` journey from W10, using one child comparison side session.
- **Correctness metric:** stable run/report identities; child result merged once; rejected/failed child leaves the parent snapshot unchanged; replay reproduces the accepted report.
- **Bound:** one successful and one rejected side-session case, provider-free fixtures.
- **Kill:** no explicit merge and rollback law, authority bypass, replay mismatch, or public work outside the approved amendment.
- **Result:** blocked until both prerequisites are met. The W10.1 installed consumer must be observed before this experiment starts.

## RESEARCH-DISTRIBUTED-TEAMS — pre-registration

- **Prerequisites:** W7 durable children and workflow implementation.
- **Run T2 pre-registration:** repeat the two-running-child / one-settled-before-restart scenario against committed W7 source, retaining the executable fixture and raw phase output. The original experiment remains historical; its rebased source citation is not the landing evidence. Pass requires two distinct coordinator PIDs, two real child processes initially running, one settled and the other running across restart, identical decision and owner events before/after observation, exactly one settlement per child, two parent joins, completed parent, replay equality, and terminal owned process groups. Bound: two child commands, no providers or network, 60 seconds per command with explicit release gates and authenticated cleanup on failure. No new workflow journal or runtime authority; any harness snapshot is an external comparison oracle only.
- **Question:** do durable local children and `WorkItem` coordination preserve event truth through one coordinator crash boundary?
- **Run:** one parent creates two children for distinct ready work items; one child settles, the coordinator restarts from durable state, and the remaining child settles before the parent joins.
- **Correctness metric:** each work item has one claim and one terminal outcome; each child has one terminal settlement; replay yields the same parent/child/work-item state; no coordination state exists only in memory.
- **Bound:** local provider-free run, two children, one simulated restart, no new protocol.
- **Kill:** any local recovery or settlement failure, duplicate claim/settlement, or coordination state outside event truth.
- **Prior evidence limitation:** the earlier `test_workflow_decision_replays_across_controller_restart` run at `ca540daa556ce1ee5225423ba43b473123e7d1f8` started `verify` only after `inspect` settled. It did not exercise two already-running children across restart.
- **Result:** a provider-free experiment at W7 head `7d97e7913f14f7f74d0e304f86067603120d54ea` used two real `ProcessExecutionAdapter` children and two independent coordinator processes. Both children were observed running; `inspect` settled before the first coordinator exited, while `verify` remained running. A fresh registry/repository/factory/controller reproduced the saved decision, complete retained child records, and event counts without writing events. It then accepted `verify`, projected workflow completion, joined both distinct children once, and completed the parent. Each of the three WorkItems had exactly one lease, one attempt, and one completion; each child had exactly one terminal settlement. Session and WorkItem replay equaled the durable read models, and both owned processes were terminal.
- **Experiment corrections:** the first harness mistakenly asked the intentionally sequential workflow controller to launch both children; the final run starts them through the durable child factory and uses the controller as the event projection. A captured output pipe initially delayed coordinator return until the surviving child's bound expired; file-backed output removed that harness wait. The first post-restart audit used `terminal` instead of the Session status `completed`. These were harness failures, not product failures; prior targets were cleaned through their owning adapter and no product code changed for this experiment.
- **Run T2 retained evidence (supersedes the historical run for landing):** [`W11_TEAM_PROBE.py`](W11_TEAM_PROBE.py) is the executable fixture; [`W11_TEAM_OUTPUT.json`](W11_TEAM_OUTPUT.json) retains commands, raw stdout/stderr, complete observed child records, and Session/WorkItem events. Source: `https://github.com/kmccleary3301/breadboard.git` at `e60620a532b76440d384039ccca9366d1d647d97`. Coordinator PIDs `18197` and `18230` were distinct; child PIDs `18207` and `18211` both started running. One child settled before restart and one remained running; restart and read-only observation reproduced the decision and owner events. Final settlements were `[1, 1]`, parent joins `2`, parent completed, replay equal, and both owned process groups terminal. Each WorkItem had one lease, attempt, and completion.
- **T2 harness correction and scope:** the first attempt completed a detached parent Session view rather than persisting through `mutate_session`; the retained parent therefore remained running. The fixture was corrected to use the existing durable owner, with no product change. Its failed output and authenticated cleanup are retained beside the successful rerun. Both temporary roots were removed after output retention. This provider-free fixture uses a synthetic immutable Lock and proves local team ownership/recovery only—not installed comparison behavior, full Definition parity, or detached-process containment.
- **Disposition:** **ADOPT-LATER**. W7 already supplies the proved local durable-team semantics. Trigger: a named workflow requires coordinated placement across more than one W5 execution world and cannot express it as current durable children plus WorkItems. Preserve the same event-truth/replay oracle; do not add a second coordination state owner.

### Replaying T2 from a fresh checkout

The fixture lives in this PR; its engine source is the published [W7 snapshot](https://github.com/kmccleary3301/breadboard/commit/e60620a532b76440d384039ccca9366d1d647d97), not the W11 tree. A shallow W11 checkout must fetch that source. From the repository root:

```sh
fixture="$PWD/docs_tmp/bb_direction_assessment/dsh_donor_impl/W11_TEAM_PROBE.py"
source_root="$(mktemp -d)"
run_root="$(mktemp -d)"
git fetch https://github.com/kmccleary3301/breadboard.git e60620a532b76440d384039ccca9366d1d647d97 &&
git worktree add --detach "$source_root" e60620a532b76440d384039ccca9366d1d647d97 &&
(
  cd "$source_root" || exit 1
  export PYTHONPATH="$source_root"
  status=0
  uv run --no-project --python 3.11 --with pydantic --with pyyaml --with jsonschema python "$fixture" start "$run_root" >"$run_root/start.stdout" 2>"$run_root/start.stderr" || status=$?
  if [ "$status" -eq 0 ]; then
    uv run --no-project --python 3.11 --with pydantic --with pyyaml --with jsonschema python "$fixture" resume "$run_root" >"$run_root/resume.stdout" 2>"$run_root/resume.stderr" || status=$?
  fi
  cleanup_status=0
  uv run --no-project --python 3.11 --with pydantic --with pyyaml --with jsonschema python "$fixture" cleanup "$run_root" >"$run_root/cleanup.stdout" 2>"$run_root/cleanup.stderr" || cleanup_status=$?
  if [ "$status" -eq 0 ]; then
    status="$cleanup_status"
  fi
  exit "$status"
)
```

The subshell preserves the first phase failure while always attempting authenticated cleanup; a cleanup failure also fails an otherwise successful run. Inspect the retained phase stdout/stderr files. Remove the temporary source worktree with `git worktree remove "$source_root"` and the owned `run_root` only after cleanup reports `owned_process_groups_terminal: true`. If cleanup fails, keep both paths for recovery. Python 3.11 is selected by version, not a machine-specific interpreter path. The probe rejects optimized Python before creating run state, so `-O` or `PYTHONOPTIMIZE` cannot silently disable its evidence checks.

Automated runners must capture phase output to regular files rather than pipes: the surviving child inherits output descriptors after the `start` coordinator exits. The fetched-source reproduction passed with file-backed capture; both coordinators were distinct, replay matched, the parent joined twice and completed, and authenticated cleanup confirmed terminal process groups.

Recipe verification: the version-selected Python 3.11 command passed from fetched source with coordinators 62355/62409, settlements `[1,1]`, two parent joins, replay equality, and terminal-group cleanup. A deliberately mismatched observation oracle made `resume` fail and the recipe exit 1 even though cleanup succeeded. Inherited `PYTHONOPTIMIZE=1` exited 1 before creating run state or emitting success output. The first portable invocation exposed an undeclared `jsonschema` import before launch; the recipe now declares that dependency explicitly.
