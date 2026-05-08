# BreadBoard Generalized Agentic Search Execution Playbook

Date: 2026-05-08

Source request: `docs_tmp/platform/BREADBOARD_MAINTAINER_REQUEST_GPURL_AGENTIC_SEARCH.md`

Status: planning locked, implementation not yet started

Target score: `0 / 100`

## Purpose

This document locks the execution strategy for turning the maintainer request into a low-blast-radius BreadBoard engineering program.

The target capability is a generalized, artifact-gated, externally evaluated agentic search substrate. GPURL is the motivating pressure test, but the implementation must remain domain-neutral. BreadBoard should own the orchestration and evidence boundary; external projects should own domain-specific validators, hidden tests, benchmark scoring, and acceptance policy.

The highest-value first outcome is not a full campaign search engine. The highest-value first outcome is a bulletproof single-candidate artifact task path:

- take task input
- obtain or accept a model response
- materialize a required artifact
- enforce artifact completion semantics
- run optional external evaluators
- emit complete evidence
- fail loudly when the artifact contract is not satisfied

Everything else in this playbook branches from that surface.

## Operating Thesis

BreadBoard already has the major strategic bones:

- disposable workspace discipline
- CLI/provider bridge machinery
- DAG/search runtime abstractions
- optimization/evidence/promotion concepts
- DARWIN-style campaign governance concepts
- RL/RLM export-facing abstractions
- C-Trees for tree-shaped control programs
- conformance, policy, ledger, and artifact patterns

The current gap is mostly productization and operator semantics:

- completion is too text/provider-response oriented for external campaign work
- required artifacts are not yet first-class completion gates
- response materialization is ad hoc rather than reusable
- external evaluators are not a narrow, standardized hook
- evidence bundle export is not a single canonical product
- operator commands and config explanation are less discoverable than the substrate deserves

The plan below treats those as additive platform layers. It does not require rewriting DAG, RL, C-Trees, provider routing, or the main agent loop.

## Non-Negotiable Design Constraints

1. Additive first: new modules, tests, scripts, and docs should be preferred over invasive modifications.
2. Artifact success must not be inferred from text success.
3. Every externally evaluated candidate must have an auditable evidence bundle.
4. GPURL-specific logic must stay outside BreadBoard.
5. External evaluator hooks must capture outputs, errors, exit status, timeout status, and timing.
6. Live workspace mutation and artifact export must remain clearly separated.
7. No DAG, RL, optimize, or C-Trees architecture change is allowed until the single-artifact path is proven.
8. Campaign parallelism is downstream of single-task correctness.
9. Promotion and quarantine hooks are downstream of evaluator-result normalization.
10. Any operator-facing command must support JSON output for agent consumption.
11. Expensive or live-provider work is optional and explicitly gated.
12. Every tranche must end with either a validation note, a closeout note, or a rollback/abandonment note.

## Scope Boundaries

### In Scope

- artifact completion contracts
- response-to-file materialization
- required artifact validation
- artifact hashing and manifests
- external evaluator shell/argv hooks
- evidence bundle export
- disposable workspace import/export bridge
- one or more agent-readable operator entrypoints
- `doctor` and `config explain` style discovery surfaces
- bounded parallel candidate campaigns
- campaign resume ledgers
- generic promotion/quarantine policy hooks
- later adapters into DAG, optimize, RL, and DARWIN-style governance

### Out Of Scope For The First 40 Points

- GPURL correctness logic inside BreadBoard
- hidden benchmark execution inside BreadBoard
- model-provider redesign
- broad `main.py` CLI rewrite
- live-provider campaign runs
- tree/DAG branching over real model calls
- RL training changes
- C-Trees control-program redesign
- major config schema migration
- making this the only way to run BreadBoard

### Permanently Out Of Scope

- BreadBoard becoming a GPU benchmark harness
- BreadBoard owning domain acceptance thresholds
- silent workspace mutation outside declared output directories
- accepting provider "done" as success when required artifacts are absent
- promotion of unevaluated artifacts

## Completion Weighting

Total weighted score: `100`

Current score: `0 / 100`

The score measures useful implementation completion, not volume of code.

| Workstream | Weight | Dependency | Target Outcome |
| --- | ---: | --- | --- |
| A. Governance And Blast-Radius Lock | 8 | none | Scope, invariants, and success/failure gates are explicit. |
| B. Artifact Contract Core | 12 | A | Pure dataclasses and validators define artifact success. |
| C. Response Materialization | 12 | B | Fenced/block response text can produce required files safely. |
| D. External Evaluator Hooks | 10 | B | External commands run with structured result capture. |
| E. Evidence Bundle Export | 12 | B, C, D | Runs emit canonical auditable bundle artifacts. |
| F. Single Artifact Task Runner | 12 | B, C, D, E | One offline/fake candidate task runs end-to-end. |
| G. Operator Surfaces And Discoverability | 10 | F | Doctor/config explain/JSON command surfaces become usable. |
| H. Workspace Bridge | 8 | F | External repo import/export is deterministic and safe. |
| I. Campaign Runner And Resume | 8 | F, H | Multiple candidates run with ledger/resume discipline. |
| J. DAG/Optimize/RL Adapters | 6 | I | Outputs can be consumed by existing downstream systems. |
| K. Release Gate, Docs, And Handoff | 2 | all | Closeout, examples, and next-sprint boundaries are written. |

## EV Ordering

Highest expected value:

- artifact-gated completion
- response materialization
- external evaluator hooks
- evidence bundle export
- one offline/fake end-to-end test
- JSON diagnostics for agent/operator use

Medium expected value:

- workspace import/export bridge
- campaign ledger
- resume semantics
- small lane preset examples
- config explanation

Lower immediate value, higher later leverage:

- parallel candidate orchestration
- DAG branching integration
- optimize/RL adapters
- promotion/quarantine policy hooks
- DARWIN campaign packaging
- live/provider matrix runs

## Recommended First Implementation Slice

Implement only the following before widening:

- new additive artifact-task module
- pure artifact requirement dataclasses
- required file existence, min-size, and hash checks
- response materializer for fenced code blocks
- evaluator hook runner
- evidence bundle writer
- offline test proving text-only completion fails
- offline test proving fenced block materializes and passes
- offline test proving evaluator failure is captured

Do not modify the main DAG runtime, optimize ranking logic, RL export code, provider routing, or C-Trees runtime in the first slice.

## Candidate File Layout

Preferred additive implementation layout:

- `agentic_coder_prototype/artifact_tasks/__init__.py`
- `agentic_coder_prototype/artifact_tasks/contracts.py`
- `agentic_coder_prototype/artifact_tasks/materialize.py`
- `agentic_coder_prototype/artifact_tasks/evaluators.py`
- `agentic_coder_prototype/artifact_tasks/evidence.py`
- `agentic_coder_prototype/artifact_tasks/runner.py`
- `tests/test_artifact_tasks.py`

Possible later operator entrypoints:

- `scripts/dev/artifact_task_smoke.py`
- `scripts/dev/breadboard_doctor.py`
- `scripts/dev/config_explain.py`
- `agentic_coder_prototype/api/cli_bridge/artifact_tasks.py`

Higher-blast-radius files to avoid until later:

- `main.py`
- `agentic_coder_prototype/agent.py`
- provider runtime internals
- DAG runtime internals
- C-Trees runtime internals
- optimize/RL core modules

## Core Data Model Draft

The first code tranche should use narrow, serializable dataclasses. Names may change, but the concepts should not.

### ArtifactRequirement

Fields:

- `path`: required relative path under workspace or artifact output root
- `min_bytes`: optional minimum size
- `max_bytes`: optional maximum size
- `language`: optional declared language
- `required`: boolean, default true
- `sha256`: optional expected hash for exact fixture tests
- `allow_overwrite`: default false
- `description`: optional operator-facing explanation

Validation rules:

- path must be relative
- path must not contain parent traversal
- absolute paths are rejected
- symlinks should be rejected or copied as symlink metadata only
- missing required files fail completion
- present but undersized files fail completion
- hash mismatch fails completion

### ArtifactContract

Fields:

- `requirements`: list of `ArtifactRequirement`
- `mode`: `response_materialize`, `file_edit`, or later `external_import`
- `completion_policy`: `all_required` for the first tranche
- `workspace_root`: optional root for checking artifacts
- `artifact_root`: optional separate export root

Validation rules:

- empty requirements are invalid for artifact-gated mode
- duplicate output paths are invalid
- completion is false if any required artifact fails
- optional artifacts may be recorded but not required for success

### MaterializationSpec

Fields:

- `strategy`: first value `fenced_block`
- `language`: optional language selector
- `output_path`: relative target path
- `require_single_block`: default true
- `strip_surrounding_whitespace`: default true
- `reject_empty`: default true
- `overwrite`: default false

Validation rules:

- missing fenced block fails materialization
- multiple candidate blocks fail when `require_single_block` is true
- language mismatch fails when language is explicitly required
- target path must pass the same relative-path safety checks as artifacts

### EvaluatorSpec

Fields:

- `name`: stable evaluator id
- `command`: argv list preferred
- `cwd`: optional relative cwd
- `timeout_seconds`: required or defaulted
- `env`: optional explicit environment additions
- `shell`: default false
- `required`: default true

Validation rules:

- missing name fails config validation
- timeout must be positive
- shell commands are allowed only with explicit `shell: true`
- cwd must stay under workspace or evaluation root

### EvaluatorResult

Fields:

- `name`
- `status`: `passed`, `failed`, `timeout`, `error`, `skipped`
- `exit_code`
- `duration_seconds`
- `stdout_path`
- `stderr_path`
- `stdout_tail`
- `stderr_tail`
- `started_at`
- `finished_at`
- `command`
- `cwd`

Completion rules:

- required evaluator nonzero exit fails task
- timeout fails task
- evaluator infrastructure error fails task
- skipped optional evaluator does not fail task

### EvidenceBundleManifest

Fields:

- `schema_version`
- `task_id`
- `candidate_id`
- `created_at`
- `status`
- `inputs`
- `artifacts`
- `materialization`
- `evaluators`
- `hashes`
- `workspace`
- `route`
- `notes`

Required files:

- `manifest.json`
- `inputs/task.md` or normalized task input copy
- `responses/raw_response.md`
- `artifacts/<required-artifact>`
- `artifacts/artifact_manifest.json`
- `evaluators/<name>/stdout.txt`
- `evaluators/<name>/stderr.txt`
- `evaluators/<name>/result.json`
- `hashes/sha256_manifest.json`

## Workstream A: Governance And Blast-Radius Lock

Weight: `8`

Goal:

Keep the program narrow, additive, and measurable before writing production code.

Checklist:

- [ ] Create this playbook and treat it as the authoritative phase-12 scope.
- [ ] Record the source maintainer request path.
- [ ] Record that GPURL is a reference workload, not embedded domain logic.
- [ ] Define the highest-EV first slice as single artifact task execution.
- [ ] Mark DAG, optimize, RL, and C-Trees runtime changes as gated.
- [ ] Define initial file layout for additive modules.
- [ ] Define the minimum offline test suite.
- [ ] Define the first closeout criteria.
- [ ] Add a progress tracker when implementation starts.
- [ ] Refuse broad campaign or live-provider work until Workstreams B-F pass.

Scoring:

- `0 / 8`: scope is not written
- `4 / 8`: scope is written but not tied to files/tests
- `8 / 8`: scope, files, tests, gates, and non-goals are explicit

Exit criteria:

- this document exists
- first implementation slice is unambiguous
- no production behavior has been changed yet

## Workstream B: Artifact Contract Core

Weight: `12`

Goal:

Make artifact success a pure, provider-independent contract.

Implementation checklist:

- [ ] Add an additive artifact-task package.
- [ ] Implement safe relative path normalization.
- [ ] Reject absolute paths.
- [ ] Reject parent traversal.
- [ ] Reject empty required artifact lists.
- [ ] Reject duplicate artifact paths.
- [ ] Implement `ArtifactRequirement`.
- [ ] Implement `ArtifactContract`.
- [ ] Implement artifact check result records.
- [ ] Implement existence checks.
- [ ] Implement `min_bytes` checks.
- [ ] Implement optional `max_bytes` checks.
- [ ] Implement sha256 calculation.
- [ ] Implement optional exact hash checks.
- [ ] Implement aggregate completion result.
- [ ] Serialize results to dictionaries without custom JSON encoders where possible.
- [ ] Add tests for missing artifact failure.
- [ ] Add tests for undersized artifact failure.
- [ ] Add tests for hash mismatch failure.
- [ ] Add tests for valid artifact pass.
- [ ] Add tests for path traversal rejection.
- [ ] Add tests for absolute path rejection.

Design constraints:

- No agent invocation.
- No provider dependency.
- No shell command execution.
- No workspace mutation except reading declared artifacts.
- No direct dependency on GPURL.

Scoring:

- `0 / 12`: no artifact contract implementation
- `4 / 12`: dataclasses exist but validation is partial
- `8 / 12`: validation and tests exist for common cases
- `12 / 12`: path safety, hash checks, aggregate status, and serialization are tested

Exit criteria:

- `pytest tests/test_artifact_tasks.py` passes for contract tests
- artifact status is independent of agent/provider status

## Workstream C: Response Materialization

Weight: `12`

Goal:

Promote response-to-file materialization from ad hoc helper to first-class reusable primitive.

Implementation checklist:

- [ ] Implement `MaterializationSpec`.
- [ ] Implement fenced block extraction.
- [ ] Support language-tagged fences.
- [ ] Support untagged fences when language is optional.
- [ ] Reject missing fences.
- [ ] Reject empty extracted content.
- [ ] Reject multiple candidate fences when `require_single_block` is true.
- [ ] Allow deterministic selection policy only if explicitly configured.
- [ ] Reuse safe relative path validation.
- [ ] Write materialized output under declared root.
- [ ] Respect `overwrite: false`.
- [ ] Record materialized byte count.
- [ ] Record materialized sha256.
- [ ] Record extraction metadata.
- [ ] Preserve raw response separately from extracted artifact.
- [ ] Add tests for missing fence failure.
- [ ] Add tests for wrong language failure.
- [ ] Add tests for multiple fence failure.
- [ ] Add tests for successful Python block materialization.
- [ ] Add tests proving text-only "done" does not satisfy artifact completion.

Design constraints:

- No heuristic attempt to repair arbitrary text in the first tranche.
- No silent overwrite.
- No executing materialized code.
- No model-response mutation beyond configured extraction/strip behavior.

Scoring:

- `0 / 12`: no materializer
- `4 / 12`: happy path only
- `8 / 12`: failure modes covered
- `12 / 12`: materializer integrates with artifact contract and evidence metadata

Exit criteria:

- a raw markdown response containing one fenced code block can produce the required artifact
- text-only completion fails even if the response claims success

## Workstream D: External Evaluator Hooks

Weight: `10`

Goal:

Allow BreadBoard to call external validators without owning their domain logic.

Implementation checklist:

- [ ] Implement `EvaluatorSpec`.
- [ ] Implement evaluator command runner.
- [ ] Prefer argv-list commands.
- [ ] Support shell commands only via explicit opt-in.
- [ ] Validate cwd under allowed root.
- [ ] Enforce timeout.
- [ ] Capture stdout.
- [ ] Capture stderr.
- [ ] Capture exit code.
- [ ] Capture duration.
- [ ] Capture timeout status.
- [ ] Capture infrastructure exceptions.
- [ ] Normalize results to `EvaluatorResult`.
- [ ] Write stdout/stderr/result files into evidence bundle staging area.
- [ ] Preserve full output files and short tails in JSON.
- [ ] Add tests for evaluator pass.
- [ ] Add tests for evaluator nonzero failure.
- [ ] Add tests for evaluator timeout.
- [ ] Add tests for invalid cwd rejection.

Design constraints:

- The evaluator interface is generic.
- BreadBoard does not parse GPURL-specific scoreboards in this tranche.
- Required evaluator failure fails the artifact task.
- Optional evaluator failure is recorded but does not necessarily fail the task.

Scoring:

- `0 / 10`: no evaluator interface
- `3 / 10`: command runner exists but lacks structured evidence
- `7 / 10`: pass/fail/timeout captured with tests
- `10 / 10`: evaluator results are integrated into task status and evidence bundle files

Exit criteria:

- external evaluator failures are visible and machine-readable
- evaluator output can be audited after the run

## Workstream E: Evidence Bundle Export

Weight: `12`

Goal:

Make a candidate run auditable without relying on terminal scrollback or implicit local state.

Implementation checklist:

- [ ] Define evidence bundle directory structure.
- [ ] Implement manifest writer.
- [ ] Copy normalized task input into bundle.
- [ ] Copy raw response into bundle.
- [ ] Copy materialized artifacts into bundle.
- [ ] Write artifact manifest.
- [ ] Write evaluator result JSON.
- [ ] Write evaluator stdout/stderr files.
- [ ] Write sha256 manifest.
- [ ] Hash task input.
- [ ] Hash raw response.
- [ ] Hash required artifacts.
- [ ] Hash evaluator outputs.
- [ ] Include task id and candidate id.
- [ ] Include created timestamp.
- [ ] Include status and failure reasons.
- [ ] Include workspace source metadata when available.
- [ ] Include model/route metadata when available.
- [ ] Include schema version.
- [ ] Add tests for expected file presence.
- [ ] Add tests for hash manifest consistency.
- [ ] Add tests for failure bundle emission.

Design constraints:

- Evidence bundle export should happen on failure as well as success.
- Bundle schema should be stable and versioned.
- Bundle should not assume GPURL file names.
- Bundle should not require a live model call.

Scoring:

- `0 / 12`: no bundle writer
- `4 / 12`: manifest only
- `8 / 12`: artifacts and evaluator outputs captured
- `12 / 12`: success/failure bundles are hash-complete and tested

Exit criteria:

- every single-artifact task result points to a bundle manifest
- the bundle can be inspected without re-running the task

## Workstream F: Single Artifact Task Runner

Weight: `12`

Goal:

Compose contracts, materialization, evaluators, and evidence into a single offline end-to-end path.

Implementation checklist:

- [ ] Implement `ArtifactTaskSpec`.
- [ ] Implement `ArtifactTaskResult`.
- [ ] Accept task text or task file path.
- [ ] Accept raw response text for offline tests.
- [ ] Accept required artifact contract.
- [ ] Accept materialization spec.
- [ ] Accept evaluator specs.
- [ ] Accept output bundle directory.
- [ ] Execute materialization before artifact validation in `response_materialize` mode.
- [ ] Validate artifacts after materialization.
- [ ] Run evaluators only when artifact validation permits or when configured to run anyway.
- [ ] Compute final status from artifact and evaluator results.
- [ ] Emit bundle on all terminal outcomes.
- [ ] Return machine-readable result.
- [ ] Add end-to-end success test.
- [ ] Add end-to-end missing artifact failure test.
- [ ] Add end-to-end evaluator failure test.
- [ ] Add end-to-end failure bundle test.

Design constraints:

- The runner should be callable from Python without invoking the main BreadBoard CLI.
- Live provider invocation is not part of the first runner.
- The runner should make no assumptions about external project layout.

Scoring:

- `0 / 12`: no composed runner
- `4 / 12`: happy path runner
- `8 / 12`: failure modes and bundle outputs covered
- `12 / 12`: all first-slice acceptance tests pass through one API

Exit criteria:

- one function can run a fake/offline artifact task end-to-end
- result JSON is suitable for a future CLI wrapper

## Workstream G: Operator Surfaces And Discoverability

Weight: `10`

Goal:

Make the capability discoverable and agent-readable without destabilizing the existing CLI.

Implementation checklist:

- [ ] Decide whether first operator entrypoint is a script or subcommand.
- [ ] Prefer a low-blast-radius script before modifying `main.py`.
- [ ] Add `--json` output.
- [ ] Add `--task-file`.
- [ ] Add `--response-file`.
- [ ] Add `--artifact-path`.
- [ ] Add `--language`.
- [ ] Add `--out-dir`.
- [ ] Add `--evaluator` or simple evaluator spec file support.
- [ ] Add a smoke command that runs with no provider.
- [ ] Extend or wrap existing doctor script if appropriate.
- [ ] Add artifact-task checks to doctor.
- [ ] Add config explanation surface for artifact task presets.
- [ ] Document example invocations.
- [ ] Ensure nonzero exit on required artifact failure.
- [ ] Ensure JSON includes status, failure reasons, and bundle manifest path.

Design constraints:

- Do not break the existing positional `main.py config` CLI path.
- Avoid changing the default user workflow.
- Operator commands should be thin wrappers over the Python API.

Scoring:

- `0 / 10`: no operator surface
- `3 / 10`: manual Python-only use
- `7 / 10`: script or command with JSON and smoke examples
- `10 / 10`: doctor/config explain make the lane discoverable

Exit criteria:

- a maintainer can run one offline artifact-task smoke from the shell
- an agent can parse JSON output without scraping logs

## Workstream H: Workspace Bridge

Weight: `8`

Goal:

Allow external repos to hand BreadBoard a safe disposable workspace and receive artifacts back.

Implementation checklist:

- [ ] Define workspace bridge contract.
- [ ] Support read-only template import by copy.
- [ ] Support explicit disposable work directory.
- [ ] Reject accidental use of protected roots.
- [ ] Record workspace source path.
- [ ] Record workspace copy path.
- [ ] Record exported artifact paths.
- [ ] Support artifact export directory separate from working tree.
- [ ] Preserve evidence bundle path.
- [ ] Add tests for copy import.
- [ ] Add tests for export.
- [ ] Add tests for protected path rejection.

Design constraints:

- Never mutate external project roots by default.
- Default to copy-in/copy-out.
- External repo remains source of domain evaluator and acceptance gates.

Scoring:

- `0 / 8`: no bridge
- `3 / 8`: basic copy helper
- `6 / 8`: import/export plus metadata
- `8 / 8`: path safety and protected-root tests pass

Exit criteria:

- external project can provide a task bundle and workspace template without exposing its root to mutation

## Workstream I: Campaign Runner And Resume

Weight: `8`

Goal:

Scale from one candidate to many candidates without losing determinism or evidence discipline.

Implementation checklist:

- [ ] Define campaign spec.
- [ ] Define candidate id allocation.
- [ ] Define campaign ledger schema.
- [ ] Run candidates sequentially first.
- [ ] Add bounded parallelism only after sequential ledger works.
- [ ] Record per-candidate bundle manifest path.
- [ ] Record status transitions.
- [ ] Record evaluator summaries.
- [ ] Support resume by skipping completed candidate ids.
- [ ] Support retrying failed candidates only with explicit flag.
- [ ] Support campaign-level summary JSON.
- [ ] Add tests for sequential campaign.
- [ ] Add tests for resume skip.
- [ ] Add tests for failed candidate preservation.
- [ ] Add tests for bounded parallel execution if implemented.

Design constraints:

- Campaign runner must consume the single-task runner rather than reimplementing it.
- Parallelism must not share mutable candidate directories.
- Resume must never overwrite completed evidence by default.

Scoring:

- `0 / 8`: no campaign runner
- `3 / 8`: sequential loop only
- `6 / 8`: ledger and resume
- `8 / 8`: bounded parallelism with tests

Exit criteria:

- multiple candidates can be run and audited independently
- interrupted campaigns can resume without corrupting prior evidence

## Workstream J: DAG/Optimize/RL Adapters

Weight: `6`

Goal:

Connect artifact campaign outputs to existing BreadBoard search, optimization, and RL surfaces only after the artifact/evidence layer is stable.

Implementation checklist:

- [ ] Identify existing DAG packet format expected by search consumers.
- [ ] Identify existing optimize evaluation record format.
- [ ] Identify existing RL/RLM trajectory export expectations.
- [ ] Map artifact task result into a candidate packet.
- [ ] Map evaluator result into objective/evaluation metadata.
- [ ] Map evidence bundle manifest path into downstream records.
- [ ] Preserve failure statuses rather than filtering them out silently.
- [ ] Add adapter tests using offline fixture results.
- [ ] Add one optimize consumer smoke test.
- [ ] Add one RL/export smoke test if low-blast-radius.
- [ ] Write clear boundary note if deeper adapters are deferred.

Design constraints:

- No downstream adapter should own artifact materialization.
- No downstream adapter should call external evaluator directly in the first version.
- DAG, optimize, and RL should consume normalized results.

Scoring:

- `0 / 6`: no adapters
- `2 / 6`: mapping notes only
- `4 / 6`: fixture-backed candidate/evaluation packet adapter
- `6 / 6`: optimize/RL smoke consumption is tested

Exit criteria:

- existing downstream systems can consume artifact-task evidence without knowing GPURL

## Workstream K: Release Gate, Docs, And Handoff

Weight: `2`

Goal:

Close the tranche cleanly and define the next decision.

Checklist:

- [ ] Write closeout note.
- [ ] Update progress score.
- [ ] Document commands run.
- [ ] Document tests run.
- [ ] Document deferred work.
- [ ] Document any behavior changes.
- [ ] Document migration impact as none or explicit.
- [ ] Identify next highest-EV tranche.

Scoring:

- `0 / 2`: no closeout
- `1 / 2`: partial closeout
- `2 / 2`: closeout is complete and actionable

Exit criteria:

- maintainer can see exactly what shipped, what did not, and what should happen next

## Acceptance Tests

Minimum first-slice tests:

- [ ] Text-only response that claims success fails because required artifact is missing.
- [ ] Response containing one fenced Python block materializes `candidate.py`.
- [ ] Missing fenced block returns a structured failure.
- [ ] Multiple fenced blocks fail when `require_single_block` is true.
- [ ] Required artifact below `min_bytes` fails.
- [ ] Required artifact with expected sha mismatch fails.
- [ ] Required artifact success records sha256.
- [ ] External evaluator pass is captured.
- [ ] External evaluator nonzero failure is captured and fails task.
- [ ] External evaluator timeout is captured and fails task.
- [ ] Evidence bundle exists for success.
- [ ] Evidence bundle exists for failure.
- [ ] Evidence bundle manifest lists task, response, artifact, evaluator, and hash records.
- [ ] Path traversal artifact paths are rejected.
- [ ] Absolute artifact paths are rejected.

Second-slice tests:

- [ ] Script or command returns JSON for success.
- [ ] Script or command exits nonzero for missing artifact.
- [ ] Doctor includes artifact-task readiness checks.
- [ ] Workspace template import copies into disposable root.
- [ ] Workspace bridge refuses protected root mutation.
- [ ] Campaign runner creates distinct candidate directories.
- [ ] Campaign resume skips completed candidate.
- [ ] Failed candidate evidence is preserved.

Adapter tests:

- [ ] Artifact task result converts to DAG/search candidate packet.
- [ ] Evaluator result converts to optimize evaluation record.
- [ ] Evidence bundle manifest path survives RL/export projection.
- [ ] Failed candidate is represented as failed, not silently dropped.

## Branch Playbook

### Branch 1: Single Artifact Path Passes Quickly

Condition:

- Workstreams B-F pass with focused tests.

Action:

- Add thin operator script with JSON output.
- Add doctor/config-explain coverage.
- Run one fake end-to-end example.
- Stop and close before campaign widening unless the user explicitly asks for the next tranche.

Do not:

- start live model generation
- modify DAG runtime
- add parallel campaigns
- add provider-specific presets

### Branch 2: Materialization Is Ambiguous

Condition:

- Realistic responses include multiple plausible fenced blocks or mixed explanation/code formats.

Action:

- Keep first policy strict.
- Add clear failure diagnostics.
- Add optional explicit block selector only after strict behavior is tested.
- Document prompt guidance for single-block response contracts.

Do not:

- add fuzzy extraction that silently picks a block
- concatenate blocks by default
- strip imports or rewrite code

### Branch 3: Evaluator Interface Becomes Domain-Specific

Condition:

- Implementation starts parsing GPURL scoreboards or embedding benchmark thresholds.

Action:

- Stop and move domain parsing into external project adapter.
- BreadBoard should only record command output, result metadata, and optional generic metrics if supplied as JSON.
- Add an explicit boundary note.

Do not:

- import GPURL modules into BreadBoard
- encode GPU timing gates in BreadBoard
- add GPURL names to generic artifact-task classes

### Branch 4: CLI Integration Threatens Existing Behavior

Condition:

- Adding subcommands to `main.py` risks breaking the current positional config path.

Action:

- Use a separate script or module entrypoint first.
- Keep `main.py` unchanged until API is stable.
- Add a compatibility test before touching `main.py`.

Do not:

- change positional arguments casually
- require new config schema for existing runs
- change default agent execution semantics

### Branch 5: Campaign Needs Appear Before Single-Task Stability

Condition:

- There is pressure to run many candidates before single-task evidence is finished.

Action:

- Reject widening until B-F pass.
- If necessary, run a manual loop around the single-task API and label it experimental.
- Do not add campaign abstractions until bundle and result schema stabilize.

Do not:

- build resume/parallelism on unstable result records
- share one workspace across candidates
- allow candidate evidence overwrite

### Branch 6: Downstream Optimize/RL Needs Earlier Visibility

Condition:

- Optimize/RL consumers need to see artifact-task output before campaign runner is done.

Action:

- Create fixture-only adapter functions.
- Feed static `ArtifactTaskResult` fixtures.
- Do not connect live runner execution into optimize/RL yet.

Do not:

- make optimize/RL call materialization or evaluators directly
- introduce hidden coupling to artifact-task internals

## Loop Discipline

Each implementation loop must follow this order:

1. Pick one workstream and one acceptance slice.
2. Read the target files before editing.
3. Add or update tests first when practical.
4. Implement only the minimum code for the slice.
5. Run the narrow test.
6. Run a broader smoke only if narrow test passes.
7. Update progress tracker.
8. Decide stop, continue, or branch using this playbook.

Loop stop conditions:

- the slice passes and the next slice touches a new subsystem
- a failure suggests architecture drift
- a safety invariant becomes ambiguous
- a test requires live/provider work unexpectedly
- uncommitted unrelated user changes appear in target files

## Progress Tracker Template

Create `docs_tmp/platform/phase_12/BREADBOARD_GENERALIZED_AGENTIC_SEARCH_PROGRESS_TRACKER.md` when implementation begins.

Template:

```markdown
# BreadBoard Generalized Agentic Search Progress Tracker

Current score: `0 / 100`

## Workstream Scores

- `A. Governance And Blast-Radius Lock`: `0 / 8`
- `B. Artifact Contract Core`: `0 / 12`
- `C. Response Materialization`: `0 / 12`
- `D. External Evaluator Hooks`: `0 / 10`
- `E. Evidence Bundle Export`: `0 / 12`
- `F. Single Artifact Task Runner`: `0 / 12`
- `G. Operator Surfaces And Discoverability`: `0 / 10`
- `H. Workspace Bridge`: `0 / 8`
- `I. Campaign Runner And Resume`: `0 / 8`
- `J. DAG/Optimize/RL Adapters`: `0 / 6`
- `K. Release Gate, Docs, And Handoff`: `0 / 2`

## Latest Validation

- pending

## Notes

- pending
```

## Definition Of 20 Percent

`20 / 100` means:

- governance is locked
- artifact contract core is mostly implemented
- path safety is tested
- missing artifact and valid artifact checks work
- no provider, DAG, optimize, or RL code has been touched

Recommended stopping point:

- after Workstream A and most of B pass

## Definition Of 40 Percent

`40 / 100` means:

- artifact contracts pass
- response materialization passes
- text-only completion failure is tested
- basic evaluator hook is started or complete
- evidence schema is drafted in code or tests

Recommended stopping point:

- after B and C pass, with D partly or fully implemented

## Definition Of 60 Percent

`60 / 100` means:

- artifact contracts pass
- response materialization passes
- evaluator hooks pass
- evidence bundle export mostly passes
- offline single-task runner is underway

Recommended stopping point:

- after D and E pass, before CLI work

## Definition Of 80 Percent

`80 / 100` means:

- single artifact task runner passes end-to-end
- evidence bundles are emitted for success and failure
- JSON result shape is stable
- operator script/doctor/config-explain tranche can begin safely

Recommended stopping point:

- after F and part of G pass

## Definition Of 100 Percent

`100 / 100` means:

- single artifact path is implemented and tested
- operator surface is usable
- workspace bridge is safe
- campaign runner has at least sequential ledger/resume semantics or a documented deferral
- downstream adapter fixtures exist or are explicitly deferred with rationale
- closeout note is written

## Initial Implementation Command Plan

Suggested initial validation commands:

```bash
pytest tests/test_artifact_tasks.py
python -m compileall agentic_coder_prototype/artifact_tasks
python scripts/dev/artifact_task_smoke.py --help
```

Suggested broader validation after CLI/operator work:

```bash
pytest tests/test_artifact_tasks.py tests/test_search_runtime.py
python scripts/dev/first_time_doctor.py --profile engine --json
git status --short
```

Do not run live/provider campaign commands unless a later tranche explicitly authorizes that cost and blast radius.

## First Closeout Criteria

The first closeout note should answer:

- What artifact contract semantics now exist?
- What materialization modes now exist?
- What evaluator hook semantics now exist?
- What evidence files are emitted?
- Which acceptance tests pass?
- Which workstreams remain incomplete?
- Did any existing BreadBoard behavior change?
- Is the next highest-EV step operator surfacing, workspace bridge, campaign runner, or downstream adapters?

## Decision Matrix For Next Tranche

If Workstreams B-F pass cleanly:

- next tranche should be operator surfaces and workspace bridge

If evaluator hooks are brittle:

- next tranche should harden evaluator specs and timeout/error semantics

If evidence bundles are noisy or incomplete:

- next tranche should stabilize manifest schema before CLI work

If GPURL immediately needs many candidates:

- next tranche should be sequential campaign runner, not DAG integration

If downstream systems need artifact evidence:

- next tranche should be fixture-only DAG/optimize/RL adapters

If single-task path exposes provider completion bugs:

- next tranche should be provider/agent completion diagnosis, but only after preserving the provider-independent artifact gate

## Final Strategic Read

The maintainer request is highly actionable because it identifies a narrow general abstraction: artifact-gated external-evaluation campaigns. The right implementation path is to make one artifact task boringly reliable before widening. That gives BreadBoard immediate value for GPURL and many other external projects while minimizing risk to existing core behavior.

The key engineering stance is:

- BreadBoard should decide whether orchestration produced required artifacts and evidence.
- External projects should decide whether those artifacts solve their domain problem.
- Downstream DAG, optimize, RL, and DARWIN layers should consume normalized evidence only after the artifact boundary is stable.
