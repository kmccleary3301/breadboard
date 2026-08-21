# Author an agent

An agent config is a YAML file loaded by `agentic_coder_prototype.compilation.v2_loader.load_agent_config`. The loader now builds the same effective-config graph used by kernel config records, then returns a dict-compatible projection for existing runtime code.

Use this guide when adding a new agent config or converting an older one to the v2 surface.

## File shape

A v2 config needs these top-level fields:

```yaml
version: 2
workspace:
  root: .
providers:
  default_model: openai:gpt-5.1-codex-mini
  models: []
modes:
  default:
    provider: openai
    model: gpt-5.1-codex-mini
loop:
  sequence: [default]
```

Optional sections such as `tools`, `concurrency`, `completion`, `long_running`, `features`, `provider_tools`, `permissions`, and `guardrails` are schema-checked before the runtime sees the config. Validation errors include a JSON pointer, for example `/providers/models/0/adapter`, so a config author can fix the exact field.

## Layers and precedence

Configs compose through `extends`. A child can extend one file or a list of files:

```yaml
extends:
  - ./base.yaml
  - ./tools.yaml
version: 2
workspace:
  root: /work/project
providers:
  default_model: openai:gpt-5.1-codex-mini
modes:
  default:
    provider: openai
    model: gpt-5.1-codex-mini
loop:
  sequence: [default]
```

The loader builds one graph layer for each file in the extends chain. Later layers win with the same deep-merge rule the legacy loader used: nested mappings merge, while lists and scalars replace the previous value. Request metadata and request overrides can be represented as higher-precedence layers by callers that compile an effective graph directly.

## Provenance

`load_agent_config_view(path)` returns a read-only `ConfigView`. It behaves like a mapping for normal reads and exposes provenance for a dotted path:

```python
from agentic_coder_prototype.compilation.v2_loader import load_agent_config_view

view = load_agent_config_view("agent_configs/my_agent.yaml")
assert view["workspace"]["root"]
print(view.provenance("providers.default_model"))
print(view.graph["record_hash"])
```

Use `ConfigView` in new code when you need to explain where a value came from. Existing runtime code can keep using `load_agent_config(path)`, which returns the dict-compatible projection.

## Secrets

Put secret references in values that identify an environment variable instead of writing secret bytes into YAML:

```yaml
provider_tools:
  openai:
    api_key:
      value: null
      env_name: OPENAI_API_KEY
      env_satisfied: false
```

The effective graph treats secret-like paths and `env_name` leaves as host-only. The model-visible record receives a `secret://env/OPENAI_API_KEY` reference rather than the secret value.

## Authority flag

`BREADBOARD_CONFIG_AUTHORITY` controls which projection `load_agent_config` returns:

- `config`: return the legacy normalized dict.
- `parity`: build both projections, log any divergence, return the legacy dict.
- `effective`: build both projections, log any divergence, return the effective projection.

Production defaults to `config`. CI, or a local shell with `BREADBOARD_CONFIG_EFFECTIVE_DEFAULT=1`, defaults to `effective` unless `BREADBOARD_CONFIG_AUTHORITY` is set explicitly. Divergence records are written to `artifacts/config_parity/<config>.json`; set `BREADBOARD_CONFIG_DIVERGENCE_LOG=/path/to/log.jsonl` to collect JSON lines in one file.

Recommended authoring loop:

```bash
BREADBOARD_CONFIG_AUTHORITY=parity python -m pytest tests/compilation/test_config_view_and_tool_registry.py
BREADBOARD_CONFIG_AUTHORITY=effective python -m pytest tests/compilation/test_config_view_and_tool_registry.py
```

## Minimal agent example

```yaml
version: 2
workspace:
  root: .
providers:
  default_model: openai:gpt-5.1-codex-mini
  models:
    - id: openai:gpt-5.1-codex-mini
      provider: openai
      model: gpt-5.1-codex-mini
      adapter: responses
modes:
  default:
    provider: openai
    model: gpt-5.1-codex-mini
loop:
  sequence: [default]
tools:
  defs_dir: implementations/tools/defs
  registry:
    paths:
      - implementations/tools/defs
    include: [read_file, list_dir, run_shell, apply_unified_patch, mark_task_complete]
concurrency:
  nonblocking_tools: [read_file, list_dir]
completion:
  build_guard:
    enabled: true
```

This config uses the shared tool registry. Aliases such as `read`, `list`, `bash`, `shell_command`, `patch`, `write`, `todowrite`, and `todoread` come from the YAML tool definitions, not from local per-consumer maps.

## Harness-clone agent example

A harness-clone config should keep target-specific behavior in data: model routing, tool aliases, guardrails, and provider syntax. The example below sketches a Claude Code-style surface without adding Python dispatch code.

```yaml
version: 2
workspace:
  root: .
providers:
  default_model: anthropic:claude-haiku-4-5-20251001
  models:
    - id: anthropic:claude-haiku-4-5-20251001
      provider: anthropic
      model: claude-haiku-4-5-20251001
      adapter: messages
modes:
  default:
    provider: anthropic
    model: claude-haiku-4-5-20251001
loop:
  sequence: [default]
tools:
  defs_dir: implementations/tools/defs
  aliases:
    Bash: run_shell
    Read: read_file
    Write: create_file_from_block
    TodoWrite: TodoWrite
  registry:
    paths:
      - implementations/tools/defs
    include:
      - run_shell
      - read_file
      - list_dir
      - apply_unified_patch
      - create_file_from_block
      - TodoWrite
provider_tools:
  anthropic:
    prompt_cache:
      cache_control:
        type: ephemeral
    stream: true
guardrails:
  workspace_context:
    enabled: true
    parameters:
      require_exploration_before_edit: true
      require_read_before_edit: true
      require_progress_before_todo_updates: true
  todo_rate_limit:
    enabled: true
    parameters:
      todo_updates_before_progress: 1
```

Keep the clone honest: if a target harness has a new tool name, add an alias or YAML tool definition. If it has a new behavior that cannot be expressed with config, record the gap before adding code.
