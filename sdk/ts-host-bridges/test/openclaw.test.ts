import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

import {
  UnsupportedOpenClawEmbeddedRunError,
  buildOpenClawBreadboardRunRequest,
  buildOpenClawExecutionCapability,
  buildOpenClawExecutionPlacement,
  findUnsupportedOpenClawFields,
  runOpenClawEmbeddedViaBreadboard,
  type OpenClawEmbeddedRunParams,
} from "../src/index.js"

const MODULE_DIR = dirname(fileURLToPath(import.meta.url))

function loadFixture(name: string): string {
  const candidates = [
    join(MODULE_DIR, "fixtures", name),
    join(MODULE_DIR, "../../../test/fixtures", name),
  ]
  for (const candidate of candidates) {
    try {
      return readFileSync(candidate, "utf8")
    } catch {
      continue
    }
  }
  throw new Error(`Unable to load fixture ${name}`)
}

function buildBaseParams(): OpenClawEmbeddedRunParams {
  return {
    sessionId: "session:test",
    sessionKey: "agent:test:embedded:1",
    sessionFile: "/tmp/session-1.jsonl",
    workspaceDir: "/tmp/workspace",
    prompt: "hello",
    provider: "openai",
    model: "mock-1",
    timeoutMs: 5_000,
    runId: "run-embedded-test-1",
  }
}

test("openclaw bridge builds a stable BreadBoard run request", () => {
  const request = buildOpenClawBreadboardRunRequest(buildBaseParams())
  assert.equal(request.schema_version, "bb.run_request.v1")
  assert.equal(request.entry_mode, "openclaw_embedded")
  assert.equal(request.task, "hello")
  assert.equal(request.workspace_root, "/tmp/workspace")
  assert.equal(request.requested_model, "mock-1")
  assert.deepEqual(request.metadata, {
    host: "openclaw",
    session_id: "session:test",
    session_key: "agent:test:embedded:1",
    session_file: "/tmp/session-1.jsonl",
    agent_id: null,
    agent_dir: null,
    auth_profile_id: null,
    auth_profile_source: null,
    timeout_ms: 5_000,
    extra_system_prompt: null,
  })
})

test("openclaw bridge derives execution capability and placement for the supported slice", () => {
  const capability = buildOpenClawExecutionCapability(buildBaseParams())
  const placement = buildOpenClawExecutionPlacement(capability, buildBaseParams())
  assert.equal(capability.schema_version, "bb.execution_capability.v1")
  assert.equal(capability.security_tier, "trusted_dev")
  assert.equal(capability.isolation_class, "none")
  assert.equal(placement.schema_version, "bb.execution_placement.v1")
  assert.equal(placement.placement_class, "inline_ts")
  assert.equal(placement.capability_id, capability.capability_id)
})

test("openclaw bridge detects unsupported slice fields", () => {
  const unsupported = findUnsupportedOpenClawFields({
    ...buildBaseParams(),
    images: [{ type: "image" }],
    clientTools: [{ type: "function", function: { name: "calc" } }],
    onBlockReply: async () => undefined,
  })
  assert.deepEqual(unsupported, ["images", "clientTools", "onBlockReply"])
})

test("openclaw bridge allows a narrow single-function tool slice when explicitly enabled", () => {
  const unsupported = findUnsupportedOpenClawFields(
    {
      ...buildBaseParams(),
      clientTools: [{ type: "function", function: { name: "repo_linter" } }],
    },
    { allowSingleFunctionToolSlice: true },
  )
  assert.deepEqual(unsupported, [])
})

test("openclaw bridge routes supported slice through BreadBoard and projects callbacks", async () => {
  const seen: string[] = []
  const invocation = await runOpenClawEmbeddedViaBreadboard(
    {
      ...buildBaseParams(),
      onAssistantMessageStart: async () => {
        seen.push("assistant_start")
      },
      onPartialReply: async (payload) => {
        seen.push(`assistant_delta:${payload.text}`)
      },
      onReasoningStream: async (payload) => {
        seen.push(`reasoning:${payload.text}`)
      },
      onReasoningEnd: async () => {
        seen.push("reasoning_end")
      },
      onToolResult: async (payload) => {
        seen.push(`tool:${payload.text}`)
      },
      onAgentEvent: (evt) => {
        seen.push(`event:${evt.stream}`)
      },
    },
    {
      executeBreadboard: async () => ({
        assistantText: "hello from BreadBoard",
        reasoningDeltas: ["thinking..."],
        toolResults: [{ text: "tool ok" }],
        agentEvents: [{ stream: "breadboard.synthetic", data: { ok: true } }],
        usage: { total: 10 },
      }),
    },
  )

  assert.equal(invocation.mode, "breadboard")
  assert.equal(invocation.executionCapability.schema_version, "bb.execution_capability.v1")
  assert.equal(invocation.executionPlacement.schema_version, "bb.execution_placement.v1")
  assert.equal(invocation.result.payloads?.[0]?.text, "hello from BreadBoard")
  assert.equal(invocation.result.meta.agentMeta?.provider, "openai")
  assert.deepEqual(seen, [
    "event:provider_response",
    "assistant_start",
    "assistant_delta:hello from BreadBoard",
    "reasoning:thinking...",
    "reasoning_end",
    "tool:tool ok",
    "event:breadboard.synthetic",
  ])
})

test("openclaw bridge honors the frozen supported-slice acceptance fixture", async () => {
  const fixture = JSON.parse(loadFixture("openclaw_embedded_supported_slice.json")) as {
    request: OpenClawEmbeddedRunParams
    breadboardOutput: {
      assistantText: string
      reasoningDeltas: string[]
      toolResults: Array<{ text?: string; mediaUrls?: string[] }>
      agentEvents: Array<{ stream: string; data: Record<string, unknown> }>
      usage: Record<string, unknown>
    }
    expected: {
      mode: "breadboard"
      callbackTrace: string[]
      payloadText: string
      provider: string
      model: string
      stopReason: string
    }
  }

  const seen: string[] = []
  const invocation = await runOpenClawEmbeddedViaBreadboard(
    {
      ...fixture.request,
      onAssistantMessageStart: async () => {
        seen.push("assistant_start")
      },
      onPartialReply: async (payload) => {
        seen.push(`assistant_delta:${payload.text}`)
      },
      onReasoningStream: async (payload) => {
        seen.push(`reasoning:${payload.text}`)
      },
      onReasoningEnd: async () => {
        seen.push("reasoning_end")
      },
      onToolResult: async (payload) => {
        seen.push(`tool:${payload.text}`)
      },
      onAgentEvent: (evt) => {
        seen.push(`event:${evt.stream}`)
      },
    },
    {
      executeBreadboard: async () => fixture.breadboardOutput,
    },
  )

  assert.equal(invocation.mode, fixture.expected.mode)
  assert.deepEqual(seen, fixture.expected.callbackTrace)
  assert.equal(invocation.result.payloads?.[0]?.text, fixture.expected.payloadText)
  assert.equal(invocation.result.meta.agentMeta?.provider, fixture.expected.provider)
  assert.equal(invocation.result.meta.agentMeta?.model, fixture.expected.model)
  assert.equal(invocation.result.meta.stopReason, fixture.expected.stopReason)
})

test("openclaw bridge preserves host-owned transcript pre-state across a continuation turn", async () => {
  const fixture = JSON.parse(loadFixture("openclaw_embedded_transcript_continuation_slice.json")) as {
    request: OpenClawEmbeddedRunParams
    existingTranscript: Array<Record<string, unknown>>
    breadboardOutput: {
      assistantText: string
      usage: Record<string, unknown>
    }
    expected: {
      mode: "breadboard"
      transcriptKinds: string[]
      assistantTailText: string
      preservedPrefixItems: number
    }
  }

  const invocation = await runOpenClawEmbeddedViaBreadboard(fixture.request, {
    existingTranscript: fixture.existingTranscript,
    executeBreadboard: async () => fixture.breadboardOutput,
  })

  assert.equal(invocation.mode, fixture.expected.mode)
  assert.deepEqual(
    invocation.transcriptPostState?.items.map((item) => item.kind),
    fixture.expected.transcriptKinds,
  )
  assert.deepEqual(invocation.transcriptPostState?.items.at(-1)?.content, {
    text: fixture.expected.assistantTailText,
  })
  assert.equal(
    invocation.transcriptPostState?.metadata?.preserved_prefix_items,
    fixture.expected.preservedPrefixItems,
  )
})

test("openclaw bridge preserves richer provider quirks in the embedded slice", async () => {
  const fixture = JSON.parse(loadFixture("openclaw_embedded_provider_quirk_slice.json")) as {
    request: OpenClawEmbeddedRunParams
    breadboardOutput: {
      assistantText: string
      providerExchange: Record<string, unknown>
      usage: Record<string, unknown>
      stopReason: string
    }
    expected: {
      mode: "breadboard"
      routeId: string
      providerFamily: string
      runtimeId: string
      finishReason: string
      assistantText: string
    }
  }

  const invocation = await runOpenClawEmbeddedViaBreadboard(fixture.request, {
    executeBreadboard: async () => fixture.breadboardOutput as never,
  })

  assert.equal(invocation.mode, fixture.expected.mode)
  assert.equal(invocation.providerTurn?.providerExchange.request.route_id, fixture.expected.routeId)
  assert.equal(invocation.providerTurn?.providerExchange.request.provider_family, fixture.expected.providerFamily)
  assert.equal(invocation.providerTurn?.providerExchange.request.runtime_id, fixture.expected.runtimeId)
  assert.equal(
    invocation.providerTurn?.providerExchange.response.finish_reasons?.[0],
    fixture.expected.finishReason,
  )
  assert.deepEqual(invocation.transcriptPostState?.items.at(-1)?.content, {
    text: fixture.expected.assistantText,
  })
})

test("openclaw bridge supports the frozen narrow tool-bearing slice", async () => {
  const fixture = JSON.parse(loadFixture("openclaw_embedded_tool_slice.json")) as {
    request: OpenClawEmbeddedRunParams
    expected: {
      mode: "breadboard"
      placementClass: string
      toolCallbackText: string
      assistantText: string
      provider: string
      model: string
    }
  }

  const seen: string[] = []
  const invocation = await runOpenClawEmbeddedViaBreadboard(
    {
      ...fixture.request,
      onAssistantMessageStart: async () => {
        seen.push("assistant_start")
      },
      onPartialReply: async (payload) => {
        seen.push(`assistant_delta:${payload.text}`)
      },
      onToolResult: async (payload) => {
        seen.push(`tool:${payload.text}`)
      },
    },
    {
      toolSlice: {
        command: ["npm", "run", "lint"],
        executeSandbox: async (request) => ({
          schema_version: "bb.sandbox_result.v1",
          request_id: request.request_id,
          status: "completed",
          placement_id: "place-openclaw-tool-1",
          stdout_ref: "artifact://stdout/openclaw-tool-1",
          stderr_ref: "artifact://stderr/openclaw-tool-1",
          artifact_refs: ["artifact://report/openclaw-tool-1"],
          side_effect_digest: "sha256:openclawtool1",
          usage: { wall_ms: 55 },
          evidence_refs: ["evidence://openclaw/tool/1"],
          error: null,
        }),
      },
    },
  )

  assert.equal(invocation.mode, fixture.expected.mode)
  assert.equal(invocation.executionPlacement.placement_class, fixture.expected.placementClass)
  assert.equal(invocation.driverTurn?.driverId, "local-process")
  assert.equal(invocation.result.payloads?.[0]?.text, fixture.expected.assistantText)
  assert.equal(invocation.result.meta.agentMeta?.provider, fixture.expected.provider)
  assert.equal(invocation.result.meta.agentMeta?.model, fixture.expected.model)
  assert.deepEqual(seen, [
    "tool:repo_linter completed via sandbox (npm run lint)",
    "assistant_start",
    "assistant_delta:repo_linter completed successfully.",
  ])
})

test("openclaw bridge can use the trusted-local driver path without a bespoke sandbox callback", async () => {
  const seen: string[] = []
  const invocation = await runOpenClawEmbeddedViaBreadboard(
    {
      ...buildBaseParams(),
      prompt: "Run the repo_linter tool directly.",
      clientTools: [{ type: "function", function: { name: "repo_linter" } }],
      onAssistantMessageStart: async () => {
        seen.push("assistant_start")
      },
      onPartialReply: async (payload) => {
        seen.push(`assistant_delta:${payload.text}`)
      },
      onToolResult: async (payload) => {
        seen.push(`tool:${payload.text}`)
      },
    },
    {
      toolSlice: {
        command: ["repo_linter", "--quick"],
        allowRunPrograms: ["repo_linter"],
        localCommandExecutor: async () => ({
          exitCode: 0,
          stdout: "openclaw local ok",
          stderr: "",
        }),
      },
    },
  )

  assert.equal(invocation.mode, "breadboard")
  assert.equal(invocation.driverTurn?.driverId, "local-process")
  assert.equal(invocation.driverTurn?.sandboxResult.status, "completed")
  assert.match(invocation.driverTurn?.sandboxResult.stdout_ref ?? "", /^file:\/\//)
  assert.equal(invocation.result.payloads?.[0]?.text, "repo_linter completed successfully.")
  assert.deepEqual(seen, [
    "tool:repo_linter completed via sandbox (repo_linter --quick)",
    "assistant_start",
    "assistant_delta:repo_linter completed successfully.",
  ])
})

test("openclaw bridge can preserve provider quirks on an OCI-backed tool slice", async () => {
  const invocation = await runOpenClawEmbeddedViaBreadboard(
    {
      ...buildBaseParams(),
      provider: "anthropic",
      model: "claude-3.7-sonnet",
      authProfileId: "profile:team:anthropic:max",
      authProfileIdSource: "user",
      prompt: "Run the sandboxed repo audit.",
      clientTools: [{ type: "function", function: { name: "repo_audit" } }],
    },
    {
      toolSlice: {
        command: ["sh", "-lc", "echo oci tool ok"],
        imageRef: "node:20-alpine",
        isolationClass: "oci",
        ociCommandExecutor: async () => ({
          exitCode: 0,
          stdout: "oci tool ok",
          stderr: "",
        }),
      },
    },
  )

  assert.equal(invocation.mode, "breadboard")
  assert.equal(invocation.driverTurn?.driverId, "oci")
  assert.equal(invocation.executionPlacement.placement_class, "local_oci")
  assert.equal(invocation.driverTurn?.sandboxResult.status, "completed")
  assert.match(invocation.driverTurn?.sandboxResult.stdout_ref ?? "", /^file:\/\//)
  assert.equal(invocation.result.meta.agentMeta?.provider, "anthropic")
  assert.equal(invocation.result.meta.agentMeta?.model, "claude-3.7-sonnet")
})

test("openclaw bridge falls back cleanly on unsupported slice fields", async () => {
  const invocation = await runOpenClawEmbeddedViaBreadboard(
    {
      ...buildBaseParams(),
      images: [{ type: "image" }],
    },
    {
      nativeFallback: async () => ({
        payloads: [{ text: "native fallback" }],
        meta: { durationMs: 1, stopReason: "completed" },
      }),
    },
  )

  assert.equal(invocation.mode, "fallback")
  assert.deepEqual(invocation.unsupportedFields, ["images"])
  assert.equal(invocation.unsupportedCase?.reason_code, "unsupported_openclaw_embedded_fields")
  assert.equal(invocation.result.payloads?.[0]?.text, "native fallback")
})

test("openclaw bridge throws when unsupported and no fallback is provided", async () => {
  await assert.rejects(
    () =>
      runOpenClawEmbeddedViaBreadboard({
        ...buildBaseParams(),
        disableTools: true,
      }),
    (error: unknown) => {
      assert.ok(error instanceof UnsupportedOpenClawEmbeddedRunError)
      assert.deepEqual(error.unsupportedFields, ["disableTools"])
      return true
    },
  )
})

test("openclaw bridge throws when no BreadBoard executor is provided for a supported slice", async () => {
  await assert.rejects(
    () => runOpenClawEmbeddedViaBreadboard(buildBaseParams()),
    (error: unknown) => {
      assert.ok(error instanceof UnsupportedOpenClawEmbeddedRunError)
      assert.deepEqual(error.unsupportedFields, ["executeBreadboard"])
      return true
    },
  )
})
