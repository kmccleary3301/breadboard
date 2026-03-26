# Optimization V1 Runtime Context

Phase F of BreadBoard Optimization V1 adds the final environment-aware and tool-pack-aware layer.

This is the layer that makes optimization aware of the runtime assumptions behind a sample, rather than treating tools, sandbox constraints, services, and MCP availability as loose notes.

## Phase F records

The Phase F records live in:

- `agentic_coder_prototype/optimize/context.py`
- `agentic_coder_prototype/optimize/dataset.py`
- `agentic_coder_prototype/optimize/backend.py`
- `agentic_coder_prototype/optimize/examples.py`

The main records are:

- `EnvironmentSelector`
- `ToolRequirement`
- `ToolPackContext`
- `ServiceContextRequirement`
- `SandboxContextRequirement`
- `MCPContextRequirement`
- `OptimizationRuntimeContext`
- `OptimizationExecutionContext`
- `RuntimeCompatibilityIssue`
- `RuntimeCompatibilityResult`

## Why this layer exists

Earlier phases already made support envelopes explicit.

Phase F finishes the job by making the sample-side assumptions explicit too. That matters because a candidate should not look "better" only because it quietly assumes:

- a different tool pack
- a different environment selector
- extra network access
- a different service selector
- a different MCP surface

Optimization V1 needs that context to be typed so compatibility can be checked and preserved in lineage.

## Environment-aware sample model

`OptimizationSample` now supports a typed runtime context surface.

The important pieces are:

- `EnvironmentSelector`: what environment/profile the sample expects
- `ToolPackContext`: what tool-pack profile and required tools the sample expects
- `ServiceContextRequirement`: what service selectors the sample expects
- `SandboxContextRequirement`: what filesystem and network constraints the sample expects
- `MCPContextRequirement`: what MCP servers/resources the sample expects, without implying network access by default

Backward compatibility is still preserved through the older `environment_requirements` and `bound_tool_requirements` fields, but the typed records are now the canonical model.

## Service, sandbox, and MCP assumptions

Phase F keeps these surfaces separate on purpose.

- Service context tells you what logical service selector a sample expects.
- Sandbox context tells you what execution constraints the sample expects.
- MCP context tells you what MCP surface a sample expects.

Those are related, but they are not interchangeable.

This makes it possible to express:

- "requires repo service selector `workspace-write`"
- "requires workspace-write sandbox but no network"
- "requires MCP server `beads`"

without collapsing everything into one catch-all dict.

## Tool-pack-aware optimization inputs

The backend now receives an `OptimizationExecutionContext`.

That record binds together:

- target ID
- sample ID
- typed runtime context
- evaluation input compatibility metadata

The reflective backend evaluates runtime compatibility before it starts proposing follow-on candidates. That compatibility result is typed and preserved in backend output.

## How compatibility is detected

The backend produces a `RuntimeCompatibilityResult` for the baseline candidate and for each proposal it considers.

Compatibility checks currently inspect:

- required tool names against the support envelope tool surface
- environment selector/profile against the support envelope environments
- sandbox filesystem/network assumptions against support envelope environments and assumptions
- service selectors against support envelope assumptions
- MCP server assumptions against support envelope assumptions

If any of those checks fail, the backend records structured issues and treats the candidate path as incompatible.

## How this interacts with support envelopes

Support envelopes and runtime context are complementary.

- The support envelope says what the optimization target is allowed to claim.
- The runtime context says what a specific sample actually requires.

Phase F is where those two surfaces finally meet in code.

That lets BreadBoard say:

- "this sample requires `exec_command`, `apply_patch`, and `spawn_agent` in a workspace-write replay-safe context"
- "this candidate is incompatible because it would require `mcp_manager` and internet-enabled sandbox access"

That is more precise than a generic "environment mismatch" label.

## Canonical examples

The canonical Phase F examples live in `agentic_coder_prototype/optimize/examples.py`.

They include:

- a compatible codex dossier backend path whose runtime assumptions match the current support envelope
- an incompatible codex dossier backend path that requires a remote tool-pack profile, networked sandbox access, and a different MCP surface

Minimal usage:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_runtime_context_examples

example = build_codex_dossier_runtime_context_examples()

assert example["compatible"]["result"].compatibility_results[0].status == "compatible"
assert example["incompatible"]["result"].compatibility_results[0].status == "incompatible"
```

If you want serialized payloads:

```python
from agentic_coder_prototype.optimize import build_codex_dossier_runtime_context_examples_payload

payload = build_codex_dossier_runtime_context_examples_payload()
```

## End-to-end reading order

If you want the smallest complete walkthrough of Optimization V1 in code, read the canonical example builders in this order:

1. `build_codex_dossier_example`
2. `build_codex_dossier_dataset_example`
3. `build_codex_dossier_evaluation_example`
4. `build_codex_dossier_backend_example`
5. `build_codex_dossier_promotion_examples`
6. `build_codex_dossier_runtime_context_examples`

That sequence mirrors the execution plan itself:

- bounded substrate
- truth-backed dataset
- explicit evaluation and wrongness
- reflective backend search
- evidence-gated promotion
- runtime-context compatibility

It is the shortest path through the codebase that exercises the full V1 architecture.

## DARWIN anti-overlap boundary

Phase F still does not introduce DARWIN runtime semantics.

This layer adds typed runtime-context assumptions for samples and backend compatibility reasoning. It does not add:

- islands
- archives
- migration or topology logic
- asynchronous campaign loops

That boundary matters because Optimization V1 must remain coherent and useful without any DARWIN component.
