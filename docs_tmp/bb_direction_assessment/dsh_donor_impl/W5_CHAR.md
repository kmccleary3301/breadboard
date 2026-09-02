# W5.1 DSH pre-run execution-world contract

Status: pre-run contract **ACKNOWLEDGED by Kyle on 2026-09-02 through the plan-authorized ask-tool auto-selection**. No local, container, Ray, or Slurm world had been run when the mask was registered.

This note registers the comparison before any world is started. It does not infer a mask from output. The four worlds are local process, Docker container, Ray actor, and Slurm job.

## Mask

The exact mask is the same in all four worlds and applies only to the serialized product Session event forms named below.

| Serialized event form | JSON pointer | Reason it is non-semantic |
| --- | --- | --- |
| `bb.session_event.v1` (`KernelEvent`) | `/occurred_at` | The host clock records when the append happened. Session order is already carried by the contiguous `sequence`; this wall-clock value does not alter the transition or read model. |
| `bb.public_session_event.v1` | `/timestamp` | The public projection copies `KernelEvent.occurred_at`. It is the same host-clock observation at the API boundary, not a second semantic value. |

No other field is masked. The mask is field-level and exact. It is not a wildcard, regex, path-prefix substitution, sorting rule, tolerance, or allowance for a missing event. If an event differs at an undeclared pointer, the result is **product red**, even when the difference looks platform-related. A future comparator for a different record family, such as the runner lifecycle ledger, has no mask under this registration until it is separately approved.

The following fields must never be masked: `schema_version`; `session_id`; `sequence` and public `seq`; `kind`; public `event_id`; every payload key and value; and every `SessionView` field (`status`, `effective_lock_hash`, `task_hash`, `event_count`, `pending_approval`, and `terminal_outcome`). This includes task, lock, content, attachment, tool, approval, error, terminal, and artifact digests. The fixed session identifier makes `session_id` and the derived public `event_id` comparable rather than volatile.

Runtime identifiers are also not a masking license. Actor handles, process IDs, process-group IDs, container IDs, workspace paths, Slurm job IDs, and node names belong in external evidence and remain fully recorded there. They must not be copied into a Session payload to make them disappear from comparison.

## Common fixture and launch paths

Every world receives the same secret-free Definition, Lock, task, and session identifier.

- Definition source: `agent_configs/templates/minimal_harness.v2.yaml`.
- Definition prompt input: `agent_configs/templates/prompts/minimal_system.md`.
- Provider input: the template's `mock/reference` model and `mock_chat` adapter. No provider credential, secret file, or provider network is admitted.
- Lock: generate `minimal_harness.v2.lock.json` once from those exact Definition and prompt bytes with the installed `bbh harness lock` path, then distribute that one locked artifact read-only to every world. The run record must include its lock hash and reject any Definition/Lock drift before launch.
- Fixed task: `inspect the workspace and print verification command passed`.
- Fixed product session identifier: `w5-dsh-session`.

The installed product orchestration owner is `scripts/breadboard_cli.py` through `breadboard/product/cli/harness.py`. Its local product path is `bbh harness run <Definition> --local --task "inspect the workspace and print verification command passed"`; its remote product path is the same command with `--server <URL>`. The run owner must supply the same locked Definition and task in both paths, then collect the durable event log through `breadboard/product/runtime/session_store.py` and the public event projection in `breadboard/product/operations/session.py:public_session_event`.

The sandbox launch paths under that orchestration are explicit:

- Local process: `breadboard/sandbox.py:DevSandboxV2`, created through `new_dev_sandbox_v2(..., driver="process")`.
- Container: `breadboard/sandbox_docker.py:DockerSandboxV2`, reached through `breadboard/sandbox_driver.py:create_sandbox(..., driver="docker")`. The Docker launch must retain `network="none"`, the pinned image, `/workspace` binding, and the same logical fixture workspace.
- Ray: the `@ray.remote` actor entrypoints in `DevSandboxV2` and `DockerSandboxV2`, routed by `breadboard/sandbox_driver.py:create_sandbox`; installed conductor setup is `breadboard_engine/conductor/bootstrap.py:setup_sandbox`.
- Slurm: the owned job evidence path is `breadboard/rl/phase3/scheduler.py:SlurmRunSpec` and `submit_slurm_run`, with the phase-5 target job surface at `scripts/rl_phase5/run_f3_target_episode.py`. The Slurm wrapper must launch the same installed product and locked fixture, not a source-tree substitute.

The four world labels are comparison labels only. They do not authorize changing the product's selected driver, image, network policy, task, fixture, or event contract.

## Ordered event-diff algorithm

The later run owner must execute this algorithm independently for each world against the same declared fixture:

1. Persist the raw product Session events as ordered JSONL and retain the raw bytes. Parse without sorting and validate each row as `bb.session_event.v1`.
2. Build the public projection from each validated row with `public_session_event`. Do not compare a hand-edited or reconstructed transcript.
3. Require one stream per world. Require the first event to be `session.started`, one session identifier throughout, and contiguous `sequence` values starting at `1`.
4. Compare streams by ordinal position. At each position compare schema version, session identity, sequence/`seq`, kind, public event identity, and payload recursively with exact key sets and exact values. Apply only the two registered timestamp pointers above.
5. Require equal event counts. Reject insertion, deletion, reordering, duplicate sequence values, unknown kinds, invalid transitions, or terminal events followed by more events. Rebuild `SessionView` from each stream and compare every read-model field exactly.
6. Report each mismatch with world, ordinal, JSON pointer, reference value, and candidate value. A mismatch outside the mask is product red. A masked timestamp is still retained in raw evidence and checked for parseability, monotonicity where the source contract requires it, and association with the event at that sequence.

There is no first-difference cutoff, best-effort continuation, "infra-only" downgrade, or observed-output-derived expansion of this mask. External liveness can explain why a stream is absent, but it cannot turn a present stream mismatch into a pass.

## External liveness observations and evidence owners

Liveness is observed separately from Session event equality. The observer records exact values, timestamps, commands, exit statuses, and evidence hashes; none are silently masked.

| World | Required observations | Evidence owner |
| --- | --- | --- |
| Local process | actor/process start and exit, process-group absence after close, workspace root identity, command result, and durable event-log path | `DevSandboxV2.run_shell`, `breadboard/product/runtime/session_store.py`, and the installed CLI owner above |
| Docker container | Docker executable identity, immutable image reference, `/workspace` bind, `--network none`, container start/exit, no leftover container, command result, and event-log publication | `DockerSandboxV2._docker_prefix`/`run_shell` plus the installed CLI and session-store owners |
| Ray actor | actor creation success, `ray.get` completion for each operation, actor/node/resource identity, actor termination, and workspace cleanup | `sandbox_driver.py:create_sandbox` and `conductor/bootstrap.py:setup_sandbox` |
| Slurm job | exact `sbatch --parsable` receipt, `SLURM_JOB_ID`, allocated node, fresh `squeue` active observation, fresh `sacct` terminal observation, raw stdout/stderr, exit code, and cleanup | `breadboard/rl/phase3/scheduler.py` and `scripts/rl_phase5/run_f3_target_episode.py` |

The later run evidence owner is `docs_tmp/bb_direction_assessment/dsh_donor_impl/W5_EVIDENCE/<world>/`. Each world must publish its raw event JSONL, public projection, ordered diff report, fixture/Lock hashes, and liveness records there. No run result is recorded in this pre-run note.

## Acceptance gate

Kyle acknowledged exactly the two timestamp pointers in `#Mask` on 2026-09-02. W5.1 execution is admitted. This acknowledgement does not waive product-red treatment for any other event or external-evidence difference; any later mask addition requires a new Kyle sign-off recorded here before another run.
