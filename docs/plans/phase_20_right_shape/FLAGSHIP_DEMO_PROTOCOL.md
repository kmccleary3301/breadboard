# Flagship demo protocol

Status: predeclared for J2. The J2 outsider run may begin only from a commit that contains this file. Changes to the target, profile, budgets, counting rules, or pass criteria after the first timed access make that run ineligible for G-J.

## Target freeze

The target is the restricted gptme profile below.

- Package: `gptme==0.31.0`
- Source tag: [`gptme/gptme@v0.31.0`](https://github.com/gptme/gptme/tree/v0.31.0), commit `4130850053d87756d28cabff5c0ba9b68750f5eb`
- Install artifact: [`gptme-0.31.0-py3-none-any.whl`](https://files.pythonhosted.org/packages/ba/0e/7e65e191e124b44d2949b50c7d29cfbc15d2691a46aa5b228e31cbfaa8c4/gptme-0.31.0-py3-none-any.whl)
- Artifact SHA-256: `f1a83d8ef2a870ee882965d3dfa5b4d35f788196f676a8a4c8eb6b5840a88b49`
- Model selection (AM21, revised before first timed access): route `openrouter/openai/gpt-5.6-luna` via OpenRouter (credential: `OPENROUTER_API_KEY` provided as an environment input; exact provider-returned id recorded at capture, preflighted as `openai/gpt-5.6-luna-20260709`), reasoning effort `low` where the pinned target exposes such control; if gptme 0.31.0 exposes no reasoning-effort control, the provider default applies and the outsider records that fact. The model identifier is captured as an input, not included in the conformance claim. Original selection `openai/gpt-4.1-mini` was replaced solely because its provider credential is unavailable in the execution environment; user-directed substitution, recorded as AM21 in SPEC_AMENDMENTS.md.

Before invoking the target, the outsider must verify the downloaded wheel digest and record `gptme --version`. A version or digest mismatch is a functional failure. Installation time counts against the time budget.

## Bounded behavior profile

The lane ID is `gptme_0_31_0_restricted_read_patch`. It covers one noninteractive edit in an isolated fixture workspace. The fixture starts with a single tracked file named `greeting.txt` whose complete contents are:

```text
status=before
```

The prompt is:

```text
Read greeting.txt. Replace exactly `status=before` with `status=after` using the patch tool. Do not change any other file. Then stop.
```

Invoke gptme noninteractively, with confirmations disabled, streaming disabled, the frozen model selected, and the exact tool allowlist `read,patch`. The outsider must derive the precise command syntax from the pinned target's public CLI documentation or help. No default or additive tool set is permitted.

The capture must enumerate and preserve these behaviors:

1. CLI invocation inputs: target version, model identifier, prompt, workspace, noninteractive settings, and exact tool allowlist.
2. The target's read request and outcome for `greeting.txt`.
3. The target's patch request and outcome for `greeting.txt`.
4. The assistant's terminal response and process exit status.
5. Workspace effects: before and after SHA-256 for `greeting.txt`, the exact patch, and a complete list of created, changed, and deleted paths.

The expected final fixture is exactly `status=after` followed by one newline. No path other than target-owned conversation metadata and `greeting.txt` may change. Target-owned metadata must remain outside the fixture workspace or be declared and excluded from the filesystem-delta assertion.

The claim is limited to this read-then-patch sequence on one UTF-8 text file. It must explicitly exclude shell execution, Python/IPython execution, append/save tools, multi-file edits, interactive conversations, conversation resume or replay, browser and network access, GUI/computer use, MCP, GitHub operations, subagents, tmux, vision, speech, image or video tools, model quality, and behavior under any other gptme version or model.

## Capture route and deliverables

Use a new target-specific adapter. The adapter must run the frozen invocation in the isolated fixture workspace and normalize the five captured behaviors into the published E4 artifact contracts. Reusing an existing BreadBoard adapter or comparator implementation as study material is prohibited. Public entry points may call the new adapter after it has been registered through the documented/scaffolded route.

The outsider must produce:

- one author-owned `bb.e4.lane_manifest.v1` manifest for the lane;
- the generated lock produced through `bbh`, never hand-edited;
- the registered adapter and any new target-specific helper source it transitively imports;
- scratch capture artifacts containing the invocation, normalized records, fixture hashes, exact delta, exit status, and declared exclusions;
- a session-running BreadBoard harness exercised through `bbh`;
- an accepted-scope conformance claim whose scope is exactly the bounded profile and whose exclusions include every item listed above;
- a stage time log and an outsider-materials log.

The outsider must use `bbh --help` and relevant subcommand help to discover the available front door. The required sequence is scaffold, author, validate, lock, scratch capture, session run, claim generation, and claim reverify. Record every stage outcome. If a front-door command is absent or cannot perform a stage, record that stage as failed; do not substitute an internal script or implementation entry point.

## Outsider rule

The list below is exhaustive. A path, URL, command output, chat transcript, or remembered content outside it is unavailable to the outsider even when it would make the task easier.

### Allowed materials

- This `docs/plans/phase_20_right_shape/FLAGSHIP_DEMO_PROTOCOL.md` file.
- `docs/DIRECTION_CHARTER.md`.
- `docs/authoring/AGENT_CONFIG_AUTHORING.md`.
- `docs/conformance/E4_COOKBOOK_V2.md`.
- The installed `bbh` CLI and text emitted by its top-level and subcommand help.
- `scripts/e4_parity/scaffold_e4_target_lane.py`.
- Published schemas below `contracts/kernel/schemas/` and `docs/conformance/schemas/`.
- The pinned gptme wheel, gptme's own public documentation, and public source at the pinned tag. Package dependencies may be installed and executed, but their source and documentation are not study material unless gptme's public documentation directly links to them for the invoked behavior.
- Files, logs, scratch artifacts, and source created by the outsider during this run from allowed material.

The target's upstream public sources include PyPI metadata, `gptme.org` documentation, CLI help emitted by the pinned artifact, and the `gptme/gptme` repository at tag `v0.31.0`. Later branches, issues, discussions, third-party tutorials, search-result summaries, and prior BreadBoard analyses are outside the allowed set.

### Prohibited materials

- BreadBoard internal implementation files, including `run_lane` internals, adapter implementations, comparator implementations, loader/compiler internals, registry-generation internals, and claim-generation internals beyond published entry points.
- Existing lane YAML or JSON definitions, lane manifests, lane locks, target packages, accepted artifacts, support claims, inventories, and adapter or comparator registry contents.
- Prior campaign evidence, probes, scout reports, transcripts, retrospectives, and work products from any earlier attempt at this target.
- BreadBoard tests, fixtures, commit history, pull requests, issues, and source not named in the allowed list.
- Advice or copied content from another agent that consulted prohibited material.

The outsider-materials log must record every file access and external URL with timestamp, disposition (`allowed` or `violation`), and purpose. It must also record every attempted access outside the allowed list as a violation, including denied or unsuccessful attempts, with a justification. A violation remains in the log; deleting it or restarting the clock cannot cure it.

## Predeclared budgets

These ceilings are the charter thesis numbers in `docs/DIRECTION_CHARTER.md`, under **Falsifiable thesis**. They agree with the charter and are fixed for the first J2 run.

- Hand-authored lane manifest: at most 150 canonical lines.
- Adapter: at most 300 lines of code under the transitive target-specific counting rule.
- Time: at most 8 hours from the first timed outsider access through the final claim-reverify attempt. This is one working day of agent-time.

The 150-line ceiling applies to the flagship manifest even though the general validator permits manifests through 300 lines. Generated locks, generated sidecars, capture artifacts, logs, and claims do not count as manifest lines. They do count as deliverables and cannot be moved into the manifest or adapter solely to evade another measurement.

At 8 hours, finish the current atomic write, stop implementation, and attempt the remaining validation and reverify stages with the artifacts as they stand. At 16 hours (2.0 times the time budget), stop the run entirely as required by WS-J failure routing. Line-budget overruns do not authorize deletion of required intent or behavior.

## Measurement rules

All measurements are taken from the J2 head commit used for verification. Record the head SHA, commands, raw outputs, and measured values.

### Manifest lines

Apply the canonical counting rule from master plan §4.1:

- The manifest uses block-style YAML.
- Each physical line is at most 120 characters.
- Count physical lines that are neither blank nor comments in the committed manifest.
- A comment line is a line whose first non-whitespace character is `#`. Inline comments remain part of a counted content line.

Flow-style collections with more than one key and lines longer than 120 characters are functional failures even when the numerical count is at most 150. Report both the canonical count and the maximum physical line length.

### Adapter lines

Count nonblank, non-comment physical lines across the transitive set of new target-specific source files reachable from the registered adapter entry point. Imports of source files that existed before the J2 branch are excluded. Every new helper reachable from the entry point is included, regardless of directory or filename. Importing generated or vendored target-specific code does not exempt it.

A comment line is one whose first non-whitespace token is the language's line-comment marker. Docstrings, multiline string bodies, declarations, decorators, imports, and executable continuations count. Record each counted file, its count, and the total. The total must be at most 300.

### Time

Create the stage time log immediately before the first outsider reads or executes any allowed material. Record UTC start and end timestamps for every stage, to whole-second precision, using one monotonic clock for durations. The run duration is the sum of elapsed stage durations and must also equal the unpaused elapsed interval from the first stage start through the final claim-reverify attempt, within clock-rounding tolerance. Installation, documentation reading, authoring, debugging, capture, waits, retries, validation, and verification all count. Parallel work consumes the same unpaused interval once, not the sum of worker durations.

A credential or service outage does not pause the clock. Record it as a blocker and continue with every stage that can still be attempted. The only time before the clock is target selection and this protocol's J1 commit.

### Functional measurements

Record pass or fail, with an artifact or command reference, for each of these conditions:

1. The installed artifact matches the frozen version and SHA-256.
2. The manifest and adapter satisfy their structural and counting rules.
3. `bbh` validation and lock generation succeed, and the generated lock is unchanged by an immediate repeat.
4. Scratch capture invokes only `read,patch`, exits successfully, produces the exact expected fixture, and changes no undeclared fixture path.
5. Normalized evidence contains each of the five enumerated behaviors and validates against the published schemas.
6. A BreadBoard session using the authored harness starts through `bbh` and reaches an observable response or documented terminal state.
7. The accepted-scope claim names the bounded behaviors and all exclusions, and claim reverify succeeds through `bbh`.
8. Every protocol stage was attempted and both required logs are complete.
9. No outsider-rule violation occurred.

## Decision rules

J2 is complete when the outsider honestly attempts every stage, preserves all measurements and violations, and submits the packet for independent verification. J2 completion does not imply that G-J passed.

G-J passes only when all nine functional conditions pass and all three budgets remain within 1.0 times their ceilings: manifest at most 150 canonical lines, adapter at most 300 counted lines, and time at most 8 hours. Equality passes. Missing evidence, an unattempted stage, a concealed access, or an outsider-rule violation fails G-J.

A target crash, provider failure, missing public instruction, unavailable `bbh` capability, schema failure, non-deterministic lock, incorrect fixture delta, incomplete claim, line overrun, or time overrun is recorded as a failure with the measured result. The outsider must not broaden the profile, consult prohibited material, change the frozen artifact or model, or relax an assertion to convert that failure into a pass.

The independent verifier reruns the available gates from the recorded head SHA, recomputes both line counts, checks the unpaused timing arithmetic, audits the materials log, and reports J2 completion separately from the G-J verdict. WS-J permits at most one rerun after named fixes; the original evidence and this predeclaration remain immutable.