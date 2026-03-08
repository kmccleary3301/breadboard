# Tool Lifecycle and Render V1

## Purpose

This dossier defines the semantic split between:

- tool specification
- tool call
- tool execution outcome
- tool model render

This is one of the highest-risk and highest-value contract families in the entire multi-engine effort.

---

## Contract Role

The tool contract family should answer:

- what tools exist and how they are defined
- how tool calls are identified and validated
- what actually happened during tool execution
- what the model and transcript were shown about that execution
- how approval/permission interacts with the tool lifecycle
- how partial progress and terminal outcomes are represented

---

## Current Python Owners

Primary owners and related surfaces:

- `agentic_coder_prototype/tool_call_ir.py`
- `agentic_coder_prototype/compilation/tool_yaml_loader.py`
- `agentic_coder_prototype/compilation/tool_prompt_synth.py`
- `agentic_coder_prototype/tool_prompt_planner.py`
- provider/runtime integration paths
- dialect adapters under `agentic_coder_prototype/dialects/`
- MCP tooling surfaces under `agentic_coder_prototype/mcp/tooling.py`
- executor surfaces implied by tests and runtime modules

---

## Shared Semantics That Must Be Frozen

### 1. Tool spec is distinct from tool call

Tool spec should include:

- canonical name
- aliases
- description
- input schema
- output schema if relevant
- approval policy hints
- visibility rules
- capability tags

Tool call should include:

- stable call id
- canonical name
- normalized args
- lineage fields
- lifecycle state

### 2. Execution outcome is not the same as model render

Execution outcome should represent what actually happened:

- raw structured result
- stdout/stderr if relevant
- error taxonomy
- artifacts
- side-effect summary or digest
- timing/resource usage if needed

Model render should represent what gets fed back into:

- transcript
- provider exchange
- assistant/tool visible content stream

These must remain separate.

### 3. Lifecycle must be explicit

A first-pass lifecycle state machine should cover:

- declared
- args_validated
- approval_requested
- approved
- denied
- scheduled
- executing
- partial_update
- completed
- failed
- cancelled

Not every tool or lane needs every state, but the model should be shared.

### 4. Approval placement matters

Approval/permission is not an incidental extra field.

The contract must define:

- when permission is requested
- whether a denied tool call is terminal or recoverable
- whether the call ID already exists before approval
- how denial is represented back to model-visible state

### 5. Rendering is parity-sensitive

The model-visible render policy is part of the shared semantics.

That includes:

- formatting style
- truncation rules
- summarization rules
- whether partial progress is shown
- error formatting rules

This is one of the easiest places for Python and TypeScript engines to drift while both appearing to "work."

---

## Non-Goals

This dossier does not attempt to freeze:

- every individual tool schema immediately
- every provider-specific tool presentation quirk immediately
- host-specific visual rendering or reducer conventions

Those can be layered later, but the lifecycle and rendering doctrine must come first.

---

## Current Evidence

Current supporting evidence and tests include:

- `tests/test_tool_call_ir.py`
- `tests/test_tool_yaml_loader.py`
- `tests/test_v2_tool_registry_paths.py`
- `tests/test_enhanced_tool_calling.py`
- `tests/test_agent_openai_tools_integration.py`
- `tests/test_agent_tool_executor.py`
- `tests/test_enhanced_executor_validation.py`
- `tests/test_enhanced_executor_routing.py`
- `tests/test_composite_tool_caller_tracking.py`
- `tests/test_tool_prompt_planner.py`
- docs under `docs/conformance/tool_runtime_evidence/`
- `docs/conformance/tool_schema_hashes_baseline_v1.json`

---

## Known Ambiguities

1. lifecycle states are still implicit in current implementation surfaces
2. render policy may still be spread across provider/runtime/dialect layers
3. approval placement and denial semantics may not yet be centrally specified
4. partial progress semantics likely differ across tools and paths

---

## First Schema Implications

The first-pass schema set should likely include at least:

- `bb.tool_spec.v1`
- `bb.tool_call.v1`
- `bb.tool_execution_outcome.v1`
- `bb.tool_model_render.v1`

The first-pass schemas should freeze:

- identifiers
- lineage
- lifecycle fields
- payload boundaries
- visibility-related fields

They should not attempt to encode all tool-specific payload detail on day one.

---

## Immediate Next Steps

1. freeze first-pass lifecycle states
2. identify current tool render owners and truncation policies
3. define tool call lineage fields
4. define denial/error/partial-update semantics
5. create a first fixture set for lifecycle traces and model render outputs
