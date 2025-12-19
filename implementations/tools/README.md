# Tool Definitions (YAML)

This directory holds individual YAML tool definitions under `defs/`. Each file defines a single tool with:

- id, name, description, type_id
- parameters (name, type, required, default, description)
- manipulations (capabilities: e.g., shell.exec, file.read, file.write, diff.apply)
- syntax_formats_supported, preferred_formats
- execution (blocking, max_per_turn)
- provider_routing (native_primary and fallback formats per provider)

Loader: `tool_calling/tool_yaml_loader.py` loads and validates these YAML files into `EnhancedToolDefinition`s and a `manipulations_by_id` map that the executor can use to route logic without switch statements.

Examples:
- `run_shell.yaml`: shell command execution with one-bash-per-turn policy
- `read_file.yaml`: selective file reads
- `list_dir.yaml`: directory listing with optional depth
- `create_file.yaml`: create empty files
- `apply_unified_patch.yaml`: unified-diff patch application
- `apply_search_replace.yaml`: search/replace edits (Aider style)
- `create_file_from_block.yaml`: add-file helper for diff-authoring workflows
- `mark_task_complete.yaml`: finalize session
