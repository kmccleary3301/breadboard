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

## Validate a config

Run the explainer in strict mode before attempting an E4 lane:

```bash
.venv/bin/python scripts/authoring/explain_agent_config.py   --config agent_configs/templates/minimal_harness.v2.yaml   --strict   --json docs_tmp/phase_19/scratch/minimal_harness_explanation.json
```

`--strict` returns non-zero for warnings. Fix missing prompt files, unknown tools, and schema errors before moving on.

## Smoke-boot a reference session

Use the CLI bridge with the mock provider and a short task. Keep the transcript or API response in `docs_tmp/phase_19/scratch/` for review.

```bash
BREADBOARD_ENABLE_E4_API=1 BREADBOARD_LEGACY_ROUTES=0 .venv/bin/python - <<'PY'
import asyncio
from agentic_coder_prototype.api.cli_bridge.models import SessionCreateRequest
from agentic_coder_prototype.api.cli_bridge.service import SessionService

async def main():
    service = SessionService()
    response = await service.create_session(SessionCreateRequest(
        config_path="agent_configs/templates/minimal_harness.v2.yaml",
        task="Read README.md if it exists, then report one verification command.",
        stream=False,
    ))
    print(response.model_dump_json())

asyncio.run(main())
PY
```

For B6 fresh-eyes evidence, the operator may use an equivalent API request if it captures the session id, config path, task, final status, and transcript path.

## Capture an E4 lane

After the config explains cleanly and the smoke boot works, scaffold a lane packet:

```bash
.venv/bin/python scripts/e4_parity/scaffold_e4_target_lane.py   --lane-id minimal_harness_probe   --config-id minimal_harness_v2   --target-family agent_config   --target-version v2   --provider-model reference-mock   --sandbox-mode fixture_only   --emit-inventory-row   --emit-lane-def
```

Then validate the generated lane definition:

```bash
.venv/bin/python scripts/authoring/validate_lane.py   --lane docs_tmp/phase_15/lane_scaffolds/minimal_harness_probe/config/e4_lane_def.scaffold.yaml   --json docs_tmp/phase_19/scratch/minimal_harness_lane_validation.json
```

Do not promote scaffold output into `docs/conformance/` until the lane has accepted artifacts and fresh hashes.

## Field registry table

This table is generated from `contracts/kernel/registries/config_surface_fields.v1.json`:

```bash
.venv/bin/python scripts/authoring/render_field_table.py
```

| Field | Class | Runtime consumer | Public dossiers using it |
|---|---|---|---:|
| `completion` | operational | agentic_coder_prototype.agent_llm_openai | 4 |
| `concurrency` | operational | agentic_coder_prototype.conductor.components | 2 |
| `enhanced_tools` | operational | agentic_coder_prototype.conductor.components | 2 |
| `features` | operational | agentic_coder_prototype.compilation.v2_loader | 4 |
| `guardrails` | operational | agentic_coder_prototype.conductor.plan_bootstrapper | 2 |
| `long_running` | operational | agentic_coder_prototype.compilation.v2_loader | 2 |
| `loop` | operational | agentic_coder_prototype.agent_llm_openai | 4 |
| `modes` | operational | agentic_coder_prototype.agent_llm_openai | 4 |
| `multi_agent` | operational | agentic_coder_prototype.agent_llm_openai | 2 |
| `permissions` | operational | agentic_coder_prototype.conductor.bootstrap | 2 |
| `prompts` | operational | agentic_coder_prototype.compilation.system_prompt_compiler | 4 |
| `provider_tools` | operational | agentic_coder_prototype.agent_llm_openai | 4 |
| `providers` | operational | agentic_coder_prototype.agent | 4 |
| `replay` | operational | agentic_coder_prototype.conductor.bootstrap | 3 |
| `schema_version` | operational | agentic_coder_prototype.compilation.v2_loader | 0 |
| `tools` | operational | agentic_coder_prototype.compilation.tool_registry | 4 |
| `turn_strategy` | operational | agentic_coder_prototype.conductor.execution | 1 |
| `version` | operational | agentic_coder_prototype.compilation.v2_loader | 4 |
| `workspace` | operational | agentic_coder_prototype.agent | 4 |
| `profile` | dossier_only | - | 4 |
| `terminal_sessions` | dossier_only | - | 1 |
| `tool_bindings` | dossier_only | - | 1 |
| `tool_packs` | dossier_only | - | 1 |
