# Session Import (IR + Canonical Event Stream)

BreadBoard supports importing external session transcripts (from harnesses or other tools) by converting them into the **canonical CLI bridge event stream** format used by the TUI/SDKs.

The import pipeline is designed to be:

- **Deterministic**: repeated conversions produce identical event envelopes.
- **Contract-gated**: inputs/outputs are defined by JSON schemas under `docs/contracts/`.
- **Safe by default**: the engine-side converter does not need to persist any secrets.

## Contracts

- Import IR schema:
  - `docs/contracts/import/import_ir_v1.schema.json`
- Import run manifest schema:
  - `docs/contracts/import/import_run_manifest_v1.schema.json`
- Output event envelope schema:
  - `docs/contracts/cli_bridge/schemas/session_event_envelope.schema.json`

## Import IR v1 (Input)

`import_ir_v1` is a provider-agnostic JSON representation of a conversation:

- A `conversation.messages[]` array with `role` and `parts`.
- Optional tool call/result structures attached to messages.
- Optional `finish` metadata.

This format is intentionally permissive (additional metadata is allowed) so that different harnesses can carry their own extra fields while still conforming to a shared baseline contract.

## Canonical Events JSONL (Output)

The canonical output is a JSONL file where each line is a **CLI bridge event envelope**.

Fields like `id` and `seq` are produced deterministically by the converter so that downstream tooling can do stable diffs and reproducible imports.

## Converter Tool

Engine-owned conversion script:

```bash
python scripts/import_ir_to_events_jsonl.py \
  --input path/to/import_ir.json \
  --output path/to/events.jsonl \
  --session-id imported-session
```

By default, the script validates the input JSON against `import_ir_v1.schema.json`. Use `--no-validate` to skip validation (not recommended).

## Notes / Scope

- This is an **engine-assist** surface. The actual filesystem importer UX typically belongs in the TUI.
- The initial converter focuses on mapping IR messages into `user_message` / `assistant_message` / `tool_result` events. It can be extended to emit richer event types as needed.

