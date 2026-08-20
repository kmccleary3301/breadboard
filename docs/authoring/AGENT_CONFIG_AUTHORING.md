# Agent config authoring

Use `agent_configs/templates/minimal_harness.v2.yaml` when you need a new v2 config that can be explained, smoke-booted, and promoted into an E4 lane. Start there, copy it, then change one field at a time.

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

Create a working copy of the minimal config and its prompt:

```bash
bbh harness init --out docs_tmp/phase_20/scratch/minimal_harness
```

Validate the config, then inspect the resolved fields and references:

```bash
bbh harness validate docs_tmp/phase_20/scratch/minimal_harness/minimal_harness.v2.yaml
bbh harness explain docs_tmp/phase_20/scratch/minimal_harness/minimal_harness.v2.yaml --strict
```

Strict explanation returns a nonzero exit when warnings remain. Fix missing prompt files, unknown tools, and schema errors before creating an E4 lane.

## Run a reference session

Run the config locally with the offline mock provider:

```bash
bbh harness run agent_configs/templates/minimal_harness.v2.yaml --local --task "List files"
```

The command starts the CLI bridge in-process, creates a session through the Python SDK, posts the task, reads the session records, and consumes the replayed event stream. It prints the session ID and record count before exiting.

## Author and capture an E4 lane

Create a lane-manifest skeleton:

```bash
bbh lane init --out docs_tmp/phase_20/scratch/minimal_harness_lane
bbh lane validate docs_tmp/phase_20/scratch/minimal_harness_lane/lane.manifest.yaml
```

Fill in the target, capture inputs, assertions, claim scope, and source-freeze reference before compiling a lock. The migrated P6.6 pilot shows the complete manifest-to-capture flow:

```bash
bbh lane validate config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml
bbh lane lock config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml --check
bbh lane capture config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.manifest.yaml --out docs_tmp/phase_20/scratch/p6_6_capture
```

Keep capture output under `docs_tmp/`. Promotion into `docs/conformance/` still requires accepted artifacts and fresh hashes.

## Maintainer script entrypoints

`bbh` is the authoring front door. These scripts remain available for maintenance and debugging:

- `scripts/authoring/explain_agent_config.py` backs `bbh harness explain`.
- `scripts/authoring/validate_lane.py` backs `bbh lane validate`.
- `scripts/e4_parity/compile_lane_lock.py` backs `bbh lane lock`.
- `scripts/e4_parity/run_lane.py` backs `bbh lane capture`.
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
| `turn_strategy` | operational | breadboard_engine.conductor.execution | 1 |
| `version` | operational | breadboard_engine.compilation.v2_loader | 4 |
| `workspace` | operational | breadboard_engine.agent | 4 |
| `profile` | dossier_only | - | 4 |
| `terminal_sessions` | dossier_only | - | 1 |
| `tool_bindings` | dossier_only | - | 1 |
| `tool_packs` | dossier_only | - | 1 |
