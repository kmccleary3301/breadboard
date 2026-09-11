# Agent config authoring

Use `breadboard harness create` for a canonical Definition v2 starting point. The older `agent_configs/templates/minimal_harness.v2.yaml` remains a compact compatibility example for data-only local runs.

## Layer model

A v2 config has three layers.

1. **Runtime surface**: fields read by loaders, providers, prompt compilation, tool registry, loop execution, or completion logic. These fields change behavior.
2. **Prompt files and tool defs**: files referenced by the config. `explain_agent_config.py` checks that prompt refs and tool names resolve.
3. **E4 lane bindings**: claim/evidence/lane files produced after a config exists. These files make the config auditable; they do not repair a bad config.

Legacy dossier-only fields such as `profile`, `tool_packs`, `tool_bindings`, and `terminal_sessions` stay out of v2 authoring unless an ADR adds a runtime consumer and updates `contracts/kernel/registries/config_surface_fields.v1.json`.

## Minimal template

Template path:

```bash
agent_configs/templates/minimal_harness.v2.yaml
```

The template uses:

- `schema_version: bb.agent_config_surface.v2` and `version: 2` for schema dispatch.
- `workspace.root: .` so local smoke runs resolve paths from the repo.
- one offline `mock` provider model named `reference-mock`.
- one prompt file, `agent_configs/templates/prompts/minimal_system.md`.
- the default tool definitions at `implementations/tools/defs`.
- one mode, `respond`, wired into a one-step loop.
- completion hints for a short smoke transcript.

Every template line has an inline comment explaining why it exists. Keep those comments when you copy the template; delete them only after the variant has its own evidence packet.

## Start and validate a config

Create a working Definition and its referenced prompt and model-role files:

```bash
breadboard harness create --out docs_tmp/phase_20/scratch/minimal_harness
```

Validate the Definition, then inspect resolved bindings, source provenance, authority requests, and lifecycle state:

```bash
breadboard harness validate docs_tmp/phase_20/scratch/minimal_harness/daily_driver.v1.yaml
breadboard harness explain docs_tmp/phase_20/scratch/minimal_harness/daily_driver.v1.yaml --strict
```

Strict explanation returns a nonzero exit when warnings remain. Fix missing prompt files, unknown tools, and schema errors before creating an E4 lane.

## Run a reference session

Run the config locally with the offline mock provider:

```bash
breadboard harness run agent_configs/templates/minimal_harness.v2.yaml --local --task "List files"
```

The command starts the CLI bridge in-process, creates a session through the Python SDK, posts the task, reads the session records, and consumes the replayed event stream. It prints the session ID and record count before exiting.

## Package, publish, and adopt executable work

An executable module directory contains `module.json` plus every declared source, import, schema, and dependency file. Package it without importing or executing its code:

`entrypoint` is `<captured-source-path>:<callable-symbol>`, not a Python module
name. Exactly one `import_members` record must bind that same captured path.
The import record repeats the source member's exact digest and size:

```json
{
  "entrypoint": "module.py:module",
  "source_members": [
    {
      "path": "module.py",
      "sha256": "sha256:<64 lowercase hexadecimal characters>",
      "size_bytes": 1234
    }
  ],
  "import_members": [
    {
      "module": "authored_module",
      "path": "module.py",
      "sha256": "sha256:<same digest>",
      "size_bytes": 1234
    }
  ]
}
```

Import module names must be unique and cannot bind Python standard-library modules or the `breadboard`, `breadboard_engine`, and `breadboard_sdk` namespaces. Every import path must belong to `source_members`. The worker refuses already-loaded declared imports instead of reusing host code.

### Author entrypoint protocol

The packaged worker process already speaks `bb.worker.v2`. Do not write a frame
loop or a `module(request)` handler. The symbol named by `entrypoint` must be a
`breadboard.modules.PolicyModule` object or a zero-argument factory returning
one. It implements these eight methods:

- `bind_dependencies(bindings)`
- `decode_input(envelope)` and `decode_output(envelope)`
- `decode_checkpoint(envelope)` and `encode_checkpoint(state, **owner_fields)`
- `encode_output(value)`
- `assess_checkpoint(context)`
- `open_instance(...)`

`open_instance` returns a `PolicyInstance` with `step(value)` and
`checkpoint(request)`. `step` returns `ContinueResult`, `OutputResult`, or
`FailureResult`; output and checkpoint payloads use `OutputEnvelope` and
`CheckpointEnvelope`. `InputEnvelope.body` and `OutputEnvelope.body` are already
bytes inside the author API. Base64 appears only in external JSON
`ModuleInput`/Session records.

`bind_dependencies` receives `DependencyBindings`; resolve a declared dependency
with `bindings.dependency(name, contract_id)`. `open_instance` receives the
instance identity and the scoped child, context, provider, and tool facades.
Retain those handles and issue effects from `step`, not while binding or opening
the instance. Use `DependencyAccess.exchange`, `ChildWorkAccess.start` /
`next_output` / `join`, `TurnContextAccess.snapshot` / `propose`, and
`ProviderAccess.start` / `next_event` only when the manifest and Session grant
the corresponding authority.

The installed author contract is inspectable without Engine source:

```bash
python -m pydoc breadboard.modules.author
python -m pydoc breadboard.modules.provider
```

For stateful replacement, `PolicyInstance.checkpoint` returns a
`CheckpointCapture`; `encode_checkpoint` attaches the supplied owner fields and
declared input sequence. The replacement's `assess_checkpoint` returns
`CheckpointCompatibility`, and `decode_checkpoint` restores the admitted state.

`module.json` is a strict public contract. Use `worker_protocol: bb.worker.v2`.
`requested_authority` and `resource_budget` have exact fields; the packager
rejects missing, extra, or runtime-inoperable values:

```json
{
  "worker_protocol": "bb.worker.v2",
  "requested_authority": {
    "project": null,
    "network": null,
    "child": null,
    "provider_ids": [],
    "tool_ids": [],
    "credential_disclosures": []
  },
  "resource_budget": {
    "max_children": 0,
    "max_message_bytes": 262144,
    "max_checkpoint_bytes": 1048576,
    "deadline_ms": 10000
  }
}
```

Set `child` to `{"allowed_module_ids": ["logical.child"], "max_depth": 1}`
only when the package declares and starts that child. Project operations are
`read` or `write`; network operations are `connect` or `resolve`. A manifest
requests authority but never grants it. Session admission separately intersects
the request with operator policy.

Project roots intersect by resolved containment, retaining the narrower path. Network destinations likewise retain the narrower exact host or wildcard suffix, rather than requiring identical strings.

`max_message_bytes` cannot exceed 262144 and `max_checkpoint_bytes` cannot
exceed 1048576. `max_children` is a non-negative integer; `deadline_ms` is a
positive integer. These are the executable worker-profile fields. CPU, memory,
process, and scratch limits belong to the selected execution world and are not
valid `resource_budget` keys.

Use `execution_tier: enforced_isolated` with `runtime.kind: oci` for the
installed local authoring journey. The image reference must be an immutable OCI
digest for the declared platform. The host overrides the OCI image entrypoint
with the complete declared command; for `bb.worker.v2`, set
`runtime.entrypoint` to exactly
`["python3", "-I", "-m", "breadboard.modules.worker", "--stdio"]`.
`trusted_native` is an operator-controlled tier: a Session refuses it with
`native_approval_required` unless the deployment supplies native-execution
approval; `harness run --local` does not grant that approval.

```bash
breadboard harness package ./ranker --out ./dist/ranker.bbmodule.zip
breadboard harness package ./revisiting-policy --out ./dist/revisiting-policy.bbmodule.zip
```

Keep `--out` outside the module source directory. Packaging rejects outputs
inside that directory, including symlink aliases, before capturing or publishing
artifacts.

Reference those immutable artifacts from one Definition v2, bind named dependencies and child edges there, then validate, explain, and lock the complete composition:

```bash
breadboard harness validate ./composition.yaml
breadboard harness explain ./composition.yaml
breadboard harness lock ./composition.yaml
```

Explicit Lock execution uses captured bytes, even if the original Definition has changed or been removed. Run a Lock path directly, or select it with `--lock`; running a mutable Definition without `--lock` still refuses drift. Each `harness run` invocation creates a new admission request. Target execution atomically pins the current published revision:

```bash
breadboard harness publish main --lock ./composition.lock.json --expected-revision 0 --request-id publish-main-1
breadboard harness run --server http://127.0.0.1:8000 --target main --module-input ./input.json
```

With `"final": false` in the module input, `harness run` returns the admitted Session ID without waiting for a terminal event. Use a persistent `--server` for later inputs; `--local` refuses non-final input because its server stops when the command exits.

A successful `"final": true` input completes the Session even when `step`
returns `ContinueResult` without emitting output.

Existing work stays on its admitted generation. To replace it, checkpoint at a quiescent frontier and ask the Session owner to adopt an exact compatible Lock:

```bash
breadboard session --server http://127.0.0.1:8000 checkpoint SESSION_ID --reason "ranker replacement" --request-id checkpoint-1
breadboard session --server http://127.0.0.1:8000 adopt SESSION_ID --checkpoint CHECKPOINT_ID --lock ./replacement.lock.json --request-id adopt-1
```

Use `breadboard session get/events/artifacts` and `breadboard harness get/explain` for generation, publication, checkpoint, pending-effect, and retirement/cleanup projections. Refusals use `bb.problem.v1`; follow `failed_stage`, `error_code`, safe record references, and `next_actions` rather than private service routes.

## Author and capture an E4 lane

Create a lane-manifest skeleton:

```bash
breadboard lane init --out docs_tmp/phase_20/scratch/minimal_harness_lane
breadboard lane validate docs_tmp/phase_20/scratch/minimal_harness_lane/lane.manifest.yaml
```

Fill in the target, capture inputs, assertions, claim scope, and source-freeze reference before compiling a lock. The migrated P6.6 pilot shows the complete manifest-to-capture flow:

```bash
breadboard lane validate config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml
breadboard lane lock config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml --check
breadboard lane capture config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml --out docs_tmp/phase_20/scratch/p6_6_capture
```

Keep capture output under `docs_tmp/`. Promotion into `docs/conformance/` still requires accepted artifacts and fresh hashes.

## Maintainer script entrypoints

`breadboard` is the installed authoring front door. These scripts remain available for maintenance and debugging:

- `breadboard/product/harness/config_explanation.py` backs `breadboard harness explain`.
- `scripts/authoring/validate_lane.py` backs `breadboard lane validate`.
- `scripts/e4_parity/compile_lane_lock.py` backs `breadboard lane lock`.
- `scripts/e4_parity/run_lane.py` backs `breadboard lane capture`.
- `scripts/e4_parity/scaffold_e4_target_lane.py` creates the older full lane-def scaffold.
- `scripts/authoring/render_field_table.py` regenerates the field registry table below.

| Field | Class | Runtime consumer | Public dossiers using it |
|---|---|---|---:|
| `completion` | operational | breadboard_engine.agent_llm_openai | 4 |
| `concurrency` | operational | breadboard_engine.conductor.components | 2 |
| `enhanced_tools` | operational | breadboard_engine.conductor.components | 2 |
| `features` | operational | breadboard_engine.compilation.v2_loader | 4 |
| `guardrails` | operational | breadboard_engine.conductor.plan_bootstrapper | 2 |
| `long_running` | operational | breadboard_engine.compilation.v2_loader | 2 |
| `loop` | operational | breadboard_engine.agent_llm_openai | 4 |
| `modes` | operational | breadboard_engine.agent_llm_openai | 4 |
| `multi_agent` | operational | breadboard_engine.agent_llm_openai | 2 |
| `permissions` | operational | breadboard_engine.conductor.bootstrap | 2 |
| `prompts` | operational | breadboard_engine.compilation.system_prompt_compiler | 4 |
| `provider_tools` | operational | breadboard_engine.agent_llm_openai | 4 |
| `providers` | operational | breadboard_engine.agent | 4 |
| `replay` | operational | breadboard_engine.conductor.bootstrap | 3 |
| `schema_version` | operational | breadboard_engine.compilation.v2_loader | 0 |
| `tools` | operational | breadboard_engine.compilation.tool_registry | 4 |
| `turn_strategy` | operational | breadboard_engine.conductor.turn_runtime | 1 |
| `version` | operational | breadboard_engine.compilation.v2_loader | 4 |
| `workspace` | operational | breadboard_engine.agent | 4 |
| `profile` | dossier_only | - | 4 |
| `terminal_sessions` | dossier_only | - | 1 |
| `tool_bindings` | dossier_only | - | 1 |
| `tool_packs` | dossier_only | - | 1 |
