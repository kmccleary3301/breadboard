#!/usr/bin/env bun
/** Hermetic, one-time exact OMP oracle capture/check runner for F6. */
import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const SCHEMA_VERSION = "bb.f6.omp_oracle.v1";
const OMP_HEAD = "6c1209842323bb4713f127ac303c97fd043d585c";
const OMP_TREE = "67a19f0d45a71af8c3d9ae83562ffb1fcb579d61";
const CANARY = "F6_DUMMY_CANARY_CREDENTIAL_7c2f8e1a";
const MAX_RAW = 32768;
const PROVIDERS = ["codex", "openai", "anthropic", "openrouter"] as const;
const PROVIDER_IDS = {
  codex: "openai-codex",
  openai: "openai",
  anthropic: "anthropic",
  openrouter: "openrouter",
} as const;
const PROVIDER_FAMILIES = [
  "catalog_model_route",
  "request_ir",
  "text_stream",
  "tool_stream",
  "usage_finish",
  "error_terminal",
  "cancel_terminal",
] as const;
const AUTH_FAMILIES = [
  "api_key_precedence",
  "codex_oauth_precedence",
  "explicit_account_binding",
  "automatic_affinity_restart_rotation",
  "classified_429_rotation",
  "refresh_single_flight",
  "refresh_transient_deferral",
  "refresh_definitive_tombstone",
  "revoke_during_refresh",
] as const;
const ROLE_FAMILIES = [
  "public_alias_selection",
  "unknown_unavailable",
  "lock_secret_rotation_restart",
  "auth_policy_no_fallback",
  "cross_provider_default_forbidden",
] as const;
const EXPECTED_IDS = [
  ...PROVIDERS.flatMap((p) => PROVIDER_FAMILIES.map((f) => `${p}.${f}`)),
  ...AUTH_FAMILIES.map((f) => `auth.${f}`),
  ...ROLE_FAMILIES.map((f) => `role.${f}`),
].sort();
const SOURCE_ANCHORS = [
  "packages/ai/src/registry/registry.ts",
  "packages/ai/src/registry/types.ts",
  "packages/ai/src/registry/openai.ts",
  "packages/ai/src/registry/openai-codex.ts",
  "packages/ai/src/registry/anthropic.ts",
  "packages/ai/src/registry/openrouter.ts",
  "packages/ai/src/providers/anthropic.ts",
  "packages/ai/src/providers/anthropic-client.ts",
  "packages/ai/src/providers/openai-responses.ts",
  "packages/ai/src/providers/openai-codex-responses.ts",
  "packages/ai/src/providers/openai-shared.ts",
  "packages/ai/src/stream.ts",
  "packages/ai/src/types.ts",
  "packages/catalog/src/build.ts",
  "packages/catalog/src/models.ts",
  "packages/catalog/src/types.ts",
  "packages/ai/src/auth-storage.ts",
  "packages/ai/src/auth/sqlite-credential-store.ts",
  "packages/ai/test/auth-storage-codex-selection.test.ts",
  "packages/ai/test/auth-storage-oauth-account-select.test.ts",
  "packages/coding-agent/src/config/model-resolver.ts",
  "packages/coding-agent/src/config/model-roles.ts",
  "packages/coding-agent/test/model-resolver.test.ts",
  "packages/coding-agent/test/issue-985-subagent-auth-fallback.test.ts",
] as const;

function die(message: string): never {
  throw new Error(message);
}
function args(argv: string[]) {
  let root: string | undefined,
    output: string | undefined,
    fixture: string | undefined,
    mode: "capture" | "check" | undefined,
    force = false;
  for (let i = 0; i < argv.length; i++) {
    switch (argv[i]) {
      case "--oracle-root":
        root = argv[++i];
        break;
      case "--capture":
        if (mode && mode !== "capture") die("choose exactly one mode");
        mode = "capture";
        break;
      case "--check":
        if (mode && mode !== "check") die("choose exactly one mode");
        mode = "check";
        break;
      case "--output":
        output = argv[++i];
        break;
      case "--fixture":
        fixture = argv[++i];
        break;
      case "--force":
        force = true;
        break;
      case "--help":
        console.log(
          "--oracle-root <path> exactly one of --capture --output <path> or --check --fixture <path> [--force]",
        );
        process.exit(0);
      default:
        die(`unknown argument ${argv[i]}`);
    }
  }
  if (!root || !mode) die("--oracle-root and exactly one mode are required");
  if (mode === "capture" && (!output || fixture))
    die("--capture requires --output and forbids --fixture");
  if (mode === "check" && (!fixture || output || force))
    die("--check requires --fixture and is read-only");
  return {
    root: resolve(root),
    output: output && resolve(output),
    fixture: fixture && resolve(fixture),
    mode,
    force,
  };
}
function git(root: string, argv: string[]): string {
  const p = Bun.spawnSync(["git", "-C", root, ...argv], {
    stdout: "pipe",
    stderr: "pipe",
  });
  if (p.exitCode !== 0)
    die(`git ${argv.join(" ")} failed: ${Buffer.from(p.stderr).toString()}`);
  return Buffer.from(p.stdout).toString().trim();
}
function verify(root: string) {
  if (
    git(root, ["rev-parse", "HEAD"]) !== OMP_HEAD ||
    git(root, ["rev-parse", "HEAD^{tree}"]) !== OMP_TREE
  )
    die("oracle identity mismatch");
  if (git(root, ["status", "--porcelain"]) !== "")
    die("oracle checkout must be clean");
  const blobs: Record<string, string> = {};
  for (const path of SOURCE_ANCHORS) {
    if (git(root, ["ls-files", "--error-unmatch", "--", path]) !== path)
      die(`untracked source anchor: ${path}`);
    const blob = git(root, ["rev-parse", `HEAD:${path}`]);
    if (!/^[0-9a-f]{40}$/.test(blob)) die(`invalid source blob: ${path}`);
    blobs[path] = blob;
  }
  return blobs;
}
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value as Record<string, unknown>)
        .sort()
        .map((k) => [k, sortKeys((value as Record<string, unknown>)[k])]),
    );
  return value;
}
function json(value: unknown): string {
  return `${JSON.stringify(sortKeys(value))}\n`;
}
function hash(value: string | Uint8Array): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
async function runnerHash() {
  return hash(new Uint8Array(await Bun.file(import.meta.path).arrayBuffer()));
}
function bounded(value: unknown): string {
  const text = typeof value === "string" ? value : json(value);
  return text.length <= MAX_RAW
    ? text
    : `${text.slice(0, MAX_RAW)}…[truncated]`;
}
function rejectCanary(value: unknown) {
  if (json(value).includes(CANARY)) die("canary credential leaked");
}
function modelLocalId(provider: (typeof PROVIDERS)[number]): string {
  const productId = SCENARIO.models[provider].id as string;
  return productId.slice(productId.indexOf("/") + 1);
}
function zeroUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}
function context(provider: (typeof PROVIDERS)[number]) {
  const spec = modelSpec(provider);
  const request = SCENARIO.request;
  const prior = request.messages[2];
  const result = request.messages[3];
  return {
    systemPrompt: [request.messages[0].content],
    messages: [
      {
        role: "user",
        content: request.messages[1].content,
        timestamp: 1700000000000,
      },
      {
        role: "assistant",
        content: prior.tool_calls.map((call: any) => ({
          type: "toolCall",
          id: call.id,
          name: call.name,
          arguments: structuredClone(call.arguments),
        })),
        api: spec.api,
        provider: spec.provider,
        model: spec.id,
        usage: zeroUsage(),
        stopReason: "toolUse",
        timestamp: 1700000000001,
      },
      {
        role: "toolResult",
        toolCallId: result.tool_call_id,
        toolName: prior.tool_calls[0].name,
        content: [{ type: "text", text: result.content }],
        isError: false,
        timestamp: 1700000000002,
      },
    ],
    tools: structuredClone(request.tools),
  };
}
function modelSpec(provider: (typeof PROVIDERS)[number]) {
  const scenarioModel = SCENARIO.models[provider];
  return {
    id: modelLocalId(provider),
    name: "F6 shared scenario model",
    provider: PROVIDER_IDS[provider],
    api: scenarioModel.api,
    baseUrl:
      provider === "anthropic"
        ? "https://example.invalid/anthropic"
        : "https://example.invalid/v1",
    reasoning: true,
    input: ["text"],
    cost: { input: 1, output: 1, cacheRead: 1, cacheWrite: 1 },
    contextWindow: 32000,
    maxTokens: SCENARIO.request.max_output_tokens,
    supportsTools: true,
    preferWebsockets: false,
  };
}
function response(
  status: string,
  usage: unknown = undefined,
  error: unknown = undefined,
) {
  return {
    id: "resp-fixed-1",
    object: "response",
    status,
    model: "f6-fixed",
    output: [],
    usage,
    error,
    incomplete_details: null,
  };
}
function responseEvents(family: string): unknown[] {
  const textValue = SCENARIO.fake.text.delta;
  const tool = SCENARIO.fake.tool;
  const msg = {
    id: "msg-item-1",
    type: "message",
    role: "assistant",
    status: "completed",
    phase: "final_answer",
    content: [{ type: "output_text", text: textValue, annotations: [] }],
  };
  const reasoning = {
    id: "reasoning-item-1",
    type: "reasoning",
    status: "completed",
    summary: [],
  };
  const call = {
    id: "fc-item-1",
    type: "function_call",
    status: "completed",
    call_id: tool.call_id,
    name: tool.name,
    arguments: tool.arguments_json,
  };
  if (family === "cancel_terminal")
    return [
      { type: "response.created", response: response("in_progress") },
      {
        type: "response.output_item.added",
        output_index: 0,
        item: { ...msg, status: "in_progress", content: [] },
      },
      {
        type: "response.output_text.delta",
        output_index: 0,
        item_id: msg.id,
        content_index: 0,
        delta: SCENARIO.fake.cancel.after_partial.delta,
      },
    ];
  const out: unknown[] = [
    { type: "response.created", response: response("in_progress") },
  ];
  if (family === "tool_stream" || family === "usage_finish")
    out.push(
      {
        type: "response.output_item.added",
        output_index: 0,
        item: { ...reasoning, status: "in_progress" },
      },
      {
        type: "response.reasoning_text.delta",
        output_index: 0,
        item_id: reasoning.id,
        content_index: 0,
        delta: tool.reasoning_delta,
      },
      {
        type: "response.output_item.done",
        output_index: 0,
        item: reasoning,
      },
      {
        type: "response.output_item.added",
        output_index: 1,
        item: { ...call, status: "in_progress", arguments: "" },
      },
      {
        type: "response.function_call_arguments.delta",
        output_index: 1,
        item_id: call.id,
        delta: call.arguments,
      },
      {
        type: "response.function_call_arguments.done",
        output_index: 1,
        item_id: call.id,
        arguments: call.arguments,
      },
      { type: "response.output_item.done", output_index: 1, item: call },
    );
  else
    out.push(
      {
        type: "response.output_item.added",
        output_index: 0,
        item: { ...msg, status: "in_progress", content: [] },
      },
      {
        type: "response.output_text.delta",
        output_index: 0,
        item_id: msg.id,
        content_index: 0,
        delta: textValue,
      },
      { type: "response.output_item.done", output_index: 0, item: msg },
    );
  const usage =
    family === "usage_finish"
      ? {
          input_tokens: SCENARIO.fake.usage.input_tokens,
          output_tokens: SCENARIO.fake.usage.output_tokens,
          total_tokens: SCENARIO.fake.usage.total_tokens,
          input_tokens_details: {
            cached_tokens: SCENARIO.fake.usage.cached_input_tokens,
          },
          output_tokens_details: {
            reasoning_tokens: SCENARIO.fake.usage.reasoning_tokens,
          },
        }
      : undefined;
  out.push({
    type: "response.completed",
    response: response("completed", usage),
  });
  return out;
}
function anthropicEvents(family: string): unknown[] {
  const start = {
    type: "message_start",
    message: {
      id: "msg-fixed-1",
      type: "message",
      role: "assistant",
      model: modelLocalId("anthropic"),
      content: [],
      usage: { input_tokens: SCENARIO.fake.usage.input_tokens },
    },
  };
  if (family === "cancel_terminal")
    return [
      start,
      {
        type: "content_block_start",
        index: 0,
        content_block: { type: "text", text: "" },
      },
      {
        type: "content_block_delta",
        index: 0,
        delta: {
          type: "text_delta",
          text: SCENARIO.fake.cancel.after_partial.delta,
        },
      },
    ];
  const out: unknown[] = [start];
  if (family === "tool_stream" || family === "usage_finish")
    out.push(
      {
        type: "content_block_start",
        index: 0,
        content_block: { type: "thinking", thinking: "", signature: "" },
      },
      {
        type: "content_block_delta",
        index: 0,
        delta: {
          type: "thinking_delta",
          thinking: SCENARIO.fake.tool.reasoning_delta,
        },
      },
      {
        type: "content_block_delta",
        index: 0,
        delta: { type: "signature_delta", signature: "fixture-signature" },
      },
      { type: "content_block_stop", index: 0 },
      {
        type: "content_block_start",
        index: 1,
        content_block: {
          type: "tool_use",
          id: SCENARIO.fake.tool.call_id,
          name: SCENARIO.fake.tool.name,
          input: {},
        },
      },
      {
        type: "content_block_delta",
        index: 1,
        delta: {
          type: "input_json_delta",
          partial_json: SCENARIO.fake.tool.arguments_json,
        },
      },
      { type: "content_block_stop", index: 1 },
    );
  else
    out.push(
      {
        type: "content_block_start",
        index: 0,
        content_block: { type: "text", text: "" },
      },
      {
        type: "content_block_delta",
        index: 0,
        delta: { type: "text_delta", text: SCENARIO.fake.text.delta },
      },
      { type: "content_block_stop", index: 0 },
    );
  out.push(
    {
      type: "message_delta",
      delta: {
        stop_reason:
          family === "tool_stream" || family === "usage_finish"
            ? "tool_use"
            : "end_turn",
      },
      usage: {
        input_tokens: SCENARIO.fake.usage.input_tokens,
        output_tokens: SCENARIO.fake.usage.output_tokens,
        cache_read_input_tokens: SCENARIO.fake.usage.cached_input_tokens,
      },
    },
    { type: "message_stop" },
  );
  return out;
}
function decodeRequestBody(body: BodyInit | null | undefined): unknown {
  if (typeof body === "string") return JSON.parse(body);
  if (body instanceof Uint8Array) {
    try {
      return JSON.parse(new TextDecoder().decode(Bun.zstdDecompressSync(body)));
    } catch {
      return { binary_sha256: hash(body), byte_length: body.byteLength };
    }
  }
  return body ?? null;
}

function canonicalValue(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}
function logicalCallId(value: unknown): string {
  const text = String(value ?? "");
  return text.includes("|") ? text.slice(0, text.indexOf("|")) : text;
}
function semantic(event: any): Record<string, unknown> | null {
  const sourceIndex = event.contentIndex;
  const block = event.partial?.content?.[sourceIndex];
  switch (event.type) {
    case "start":
      return { kind: "response_start" };
    case "text_start":
    case "text_end":
    case "thinking_start":
    case "thinking_end":
      return { kind: event.type, content_index: 0 };
    case "text_delta":
    case "thinking_delta":
      return {
        kind: event.type,
        content_index: 0,
        delta: event.delta,
      };
    case "toolcall_start":
      return {
        kind: "tool_call_start",
        content_index: 0,
        call_id: logicalCallId(block?.id),
        name: block?.name ?? null,
      };
    case "toolcall_delta":
      return {
        kind: "tool_call_delta",
        content_index: 0,
        call_id: logicalCallId(block?.id),
        delta: event.delta,
      };
    case "toolcall_end": {
      const call = event.toolCall;
      const argumentsValue = structuredClone(call?.arguments ?? {});
      return {
        kind: "tool_call_end",
        content_index: 0,
        call_id: logicalCallId(call?.id),
        name: call?.name ?? null,
        arguments_json: canonicalValue(argumentsValue),
        arguments: argumentsValue,
      };
    }
    default:
      return null;
  }
}
const SCENARIO = JSON.parse(
  await Bun.file(
    resolve(
      import.meta.dir,
      "../../conformance/provider_differential/scenario.v1.json",
    ),
  ).text(),
) as any;
function scenarioInput(provider: string, family: string): any {
  const fakeRoot = SCENARIO.fake;
  const fake =
    family === "catalog_model_route"
      ? { kind: "catalog_identity_only" }
      : family === "request_ir" || family === "text_stream"
        ? { kind: "text", ...fakeRoot.text }
        : family === "tool_stream" || family === "usage_finish"
          ? { kind: "tool", ...fakeRoot.tool, usage: fakeRoot.usage }
          : family === "error_terminal"
            ? { kind: "errors", cases: fakeRoot.errors }
            : { kind: "cancellation", ...fakeRoot.cancel };
  return {
    provider,
    family,
    model: SCENARIO.models[provider],
    request: SCENARIO.request,
    fake,
  };
}
function resultFromEvents(events: Record<string, unknown>[]) {
  const text = events
    .filter((event) => event.kind === "text_delta")
    .map((event) => String(event.delta ?? ""))
    .join("");
  const reasoning = events
    .filter((event) => event.kind === "thinking_delta")
    .map((event) => String(event.delta ?? ""))
    .join("");
  const calls = events
    .filter((event) => event.kind === "tool_call_end")
    .map((event) => ({
      call_id: event.call_id,
      name: event.name,
      arguments_json: event.arguments_json,
      arguments: event.arguments,
    }));
  return {
    assembled_text: text,
    assembled_reasoning: reasoning,
    tool_calls: calls,
  };
}
function usageFromRaw(
  provider: (typeof PROVIDERS)[number],
  family: string,
  rawOutput: any[],
) {
  if (family !== "usage_finish") return null;
  if (provider === "anthropic") {
    const start = rawOutput.find((event) => event.type === "message_start");
    const delta = rawOutput.find((event) => event.type === "message_delta");
    const input = start?.message?.usage?.input_tokens ?? null;
    const output = delta?.usage?.output_tokens ?? null;
    const cached = delta?.usage?.cache_read_input_tokens ?? null;
    if (input == null || output == null)
      die("Anthropic usage was not observed");
    return {
      input_tokens: input,
      output_tokens: output,
      total_tokens: input + output,
      reasoning_tokens: null,
      cached_input_tokens: cached,
      cache_read_tokens: cached,
      cache_write_tokens: delta?.usage?.cache_creation_input_tokens ?? null,
    };
  }
  const terminal = rawOutput.find(
    (event) => event.type === "response.completed",
  );
  const usage = terminal?.response?.usage;
  if (!usage) die(`${provider} usage was not observed`);
  const cached = usage.input_tokens_details?.cached_tokens ?? null;
  return {
    input_tokens: usage.input_tokens ?? null,
    output_tokens: usage.output_tokens ?? null,
    total_tokens: usage.total_tokens ?? null,
    reasoning_tokens: usage.output_tokens_details?.reasoning_tokens ?? null,
    cached_input_tokens: cached,
    cache_read_tokens: cached,
    cache_write_tokens: null,
  };
}
function assertCompletedProjection(
  family: string,
  result: ReturnType<typeof resultFromEvents>,
) {
  if (family === "request_ir" || family === "text_stream") {
    if (
      result.assembled_text !== SCENARIO.fake.text.delta ||
      result.assembled_reasoning !== "" ||
      result.tool_calls.length !== 0
    )
      die(`${family} actual output disagrees with shared text scenario`);
    return;
  }
  if (family === "tool_stream" || family === "usage_finish") {
    const call = result.tool_calls[0];
    if (
      result.assembled_text !== "" ||
      result.assembled_reasoning !== SCENARIO.fake.tool.reasoning_delta ||
      result.tool_calls.length !== 1 ||
      call?.call_id !== SCENARIO.fake.tool.call_id ||
      call?.name !== SCENARIO.fake.tool.name ||
      canonicalValue(call?.arguments) !==
        canonicalValue(SCENARIO.fake.tool.arguments)
    )
      die(`${family} actual output disagrees with shared tool scenario`);
  }
}
function traceFromActual(args: {
  provider: (typeof PROVIDERS)[number];
  family: string;
  events: Record<string, unknown>[];
  rawOutput: any[];
  rawTerminal: Record<string, unknown>;
  cancelMode?: "before_output" | "after_partial";
  errorCase?: any;
}) {
  const { provider, family, rawOutput, rawTerminal, cancelMode, errorCase } =
    args;
  const events = args.events.map((event, sequence) => ({
    sequence,
    ...event,
  }));
  if (events[0]?.kind !== "response_start")
    die(`${provider}.${family} did not emit response_start`);
  const result = resultFromEvents(events);
  const outputEmitted = events.some((event) =>
    [
      "text_delta",
      "thinking_delta",
      "tool_call_start",
      "tool_call_delta",
      "tool_call_end",
    ].includes(String(event.kind)),
  );
  let terminal: Record<string, unknown>;
  if (family === "error_terminal") {
    if (!errorCase || rawTerminal.state !== "error")
      die(`${provider} error case did not fail`);
    terminal = {
      state: "error",
      error_code: errorCase.error_code,
      error_message: errorCase.error_message,
      retryable: errorCase.retryable,
      http_status: errorCase.http_status,
      finish_reason: "error",
      output_emitted: outputEmitted,
    };
  } else if (family === "cancel_terminal") {
    if (rawTerminal.state !== "cancelled")
      die(`${provider} cancellation did not abort`);
    const expectedText =
      cancelMode === "after_partial"
        ? SCENARIO.fake.cancel.after_partial.delta
        : "";
    if (result.assembled_text !== expectedText)
      die(`${provider} ${cancelMode} cancellation output mismatch`);
    terminal = {
      state: "cancelled",
      reason: "caller_cancelled",
      finish_reason: "aborted",
      output_emitted: outputEmitted,
    };
  } else {
    if (rawTerminal.state !== "done")
      die(`${provider}.${family} did not complete`);
    assertCompletedProjection(family, result);
    terminal = {
      state: "done",
      finish_reason: rawTerminal.finish_reason,
      output_emitted: outputEmitted,
    };
  }
  return {
    schema_version: "bb.provider_semantic_trace.v1",
    provider,
    model: SCENARIO.models[provider].id,
    request: SCENARIO.request,
    events,
    result,
    usage: usageFromRaw(provider, family, rawOutput),
    terminal,
  };
}

async function streamCapture(
  provider: (typeof PROVIDERS)[number],
  family: string,
  ai: any,
  buildModel: any,
  cancelMode?: "before_output" | "after_partial",
  errorCase?: any,
) {
  const spec = modelSpec(provider);
  const model = buildModel(spec);
  const ctx = context(provider);
  let rawRequest: unknown;
  const rawOutput: any[] = [];
  const controller = new AbortController();
  if (family === "cancel_terminal" && cancelMode === "before_output") {
    controller.abort(new DOMException("synthetic cancellation", "AbortError"));
  }
  const fakeFetch = async (_input: RequestInfo | URL, init?: RequestInit) => {
    rawRequest = decodeRequestBody(init?.body);
    if (errorCase?.http_status != null) {
      const payload = {
        error: {
          message: `synthetic ${errorCase.label} rejection`,
          type: "synthetic_error",
          code: errorCase.error_code,
        },
      };
      rawOutput.push({
        kind: "http_error",
        status: errorCase.http_status,
        body: payload,
      });
      return new Response(JSON.stringify(payload), {
        status: errorCase.http_status,
        headers: {
          "content-type": "application/json",
          "x-request-id": "request-fixed-1",
        },
      });
    }
    if (errorCase?.label === "malformed") {
      const created = {
        type: "response.created",
        response: response("in_progress"),
      };
      const malformed = 'event: response.output_item.added\ndata: {"type":\n\n';
      rawOutput.push(created, { kind: "malformed_sse", bytes: malformed });
      const frames =
        `event: response.created\ndata: ${JSON.stringify(created)}\n\n` +
        malformed;
      return new Response(frames, {
        status: 200,
        headers: {
          "content-type": "text/event-stream",
          "x-request-id": "request-fixed-1",
        },
      });
    }
    const providerEvents = responseEvents(family);
    rawOutput.push(...providerEvents);
    const frames =
      providerEvents
        .map(
          (event) =>
            `event: ${(event as any).type}\ndata: ${JSON.stringify(event)}\n\n`,
        )
        .join("") + "data: [DONE]\n\n";
    return new Response(frames, {
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "x-request-id": "request-fixed-1",
      },
    });
  };
  const fakeClient = {
    messages: {
      create(params: unknown) {
        rawRequest = params;
        if (errorCase?.http_status != null) {
          const error = Object.assign(
            new Error(`synthetic ${errorCase.label} rejection`),
            { status: errorCase.http_status },
          );
          rawOutput.push({
            kind: "client_error",
            status: errorCase.http_status,
          });
          return { withResponse: async () => Promise.reject(error) };
        }
        const data =
          errorCase?.label === "malformed"
            ? [anthropicEvents("text_stream")[0]]
            : anthropicEvents(family);
        return {
          withResponse: async () => ({
            data: (async function* () {
              for (const event of data) {
                rawOutput.push(event);
                yield event;
              }
              if (errorCase?.label === "malformed") {
                rawOutput.push({ kind: "malformed_stream_termination" });
                throw new Error("synthetic malformed Anthropic stream");
              }
            })(),
            response: new Response(null, { status: 200 }),
            request_id: "request-fixed-1",
          }),
        };
      },
    },
  };
  const rawEvents: unknown[] = [];
  const options: any = {
    apiKey: CANARY,
    fetch: fakeFetch,
    signal: controller.signal,
    streamFirstEventTimeoutMs: 0,
    streamIdleTimeoutMs: 0,
    statefulResponses: false,
    providerRetryWait: async () => {},
    onPayload: (payload: unknown) => {
      rawRequest = payload;
    },
    onSseEvent: (event: any) => {
      rawEvents.push({ event: event.event, data: event.data });
    },
  };
  if (provider === "anthropic") options.client = fakeClient;
  let stream: any;
  let caught: unknown;
  try {
    stream =
      provider === "anthropic"
        ? ai.streamAnthropic(model, ctx, options)
        : ai.streamSimple(model, ctx, options);
  } catch (error) {
    caught = error;
  }
  const events: Record<string, unknown>[] = [];
  let message: any;
  let terminal: Record<string, unknown> = {
    state: family === "cancel_terminal" ? "cancelled" : "error",
    finish_reason: family === "cancel_terminal" ? "aborted" : "error",
  };
  try {
    if (stream) {
      for await (const event of stream) {
        const projected = semantic(event);
        if (projected) {
          events.push(projected);
          if (
            family === "cancel_terminal" &&
            cancelMode === "after_partial" &&
            projected.kind === "text_delta"
          ) {
            controller.abort(
              new DOMException("synthetic cancellation", "AbortError"),
            );
            throw controller.signal.reason;
          }
        }
        if (event.type === "done") {
          message = event.message;
          terminal = {
            state: "done",
            finish_reason: event.reason,
          };
        }
        if (event.type === "error") {
          message = event.error;
          terminal = {
            state: event.reason === "aborted" ? "cancelled" : "error",
            finish_reason: event.reason,
          };
        }
      }
    }
  } catch (error) {
    caught = caught ?? error;
    terminal = {
      state: controller.signal.aborted ? "cancelled" : "error",
      finish_reason: controller.signal.aborted ? "aborted" : "error",
    };
  }
  const errorText = message?.errorMessage
    ? String(message.errorMessage)
    : caught instanceof Error
      ? caught.message
      : caught == null
        ? null
        : String(caught);
  const actualStatus =
    typeof message?.errorStatus === "number"
      ? message.errorStatus
      : typeof (caught as any)?.status === "number"
        ? (caught as any).status
        : null;
  if (errorCase) {
    if (terminal.state !== "error" || !errorText)
      die(`${provider}.${errorCase.label} lacked an actual provider failure`);
    if (errorCase.http_status != null && actualStatus !== errorCase.http_status)
      die(
        `${provider}.${errorCase.label} status ${String(actualStatus)} != ${errorCase.http_status}`,
      );
  }
  if (
    family === "cancel_terminal" &&
    (!controller.signal.aborted || terminal.state !== "cancelled")
  )
    die(`${provider}.${cancelMode} cancellation was not observed`);
  const logicalInput = scenarioInput(provider, family);
  const semanticTrace = traceFromActual({
    provider,
    family,
    events,
    rawOutput,
    rawTerminal: terminal,
    cancelMode,
    errorCase,
  });
  return {
    raw_input: bounded(logicalInput),
    raw_request: bounded(rawRequest ?? null),
    raw_output: bounded(rawOutput.length > 0 ? rawOutput : rawEvents),
    observations: {
      semantic_trace: semanticTrace,
      raw_terminal: terminal,
      raw_message: bounded(message ?? null),
      raw_error:
        errorText?.replaceAll(CANARY, "[redacted]").slice(0, 1024) ?? null,
      raw_error_status: actualStatus,
    },
  };
}

const AUTH_ROLE_DIVERGENCES = new Set([
  "role.lock_secret_rotation_restart",
  "role.auth_policy_no_fallback",
  "role.cross_provider_default_forbidden",
]);

function oauthCredential(suffix: string, expires = Date.now() + 60 * 60_000) {
  return {
    type: "oauth" as const,
    access: `${CANARY}-access-${suffix}`,
    refresh: `${CANARY}-refresh-${suffix}`,
    expires,
    accountId: `account-${suffix}`,
    email: `${suffix}@example.test`,
  };
}

function apiKeyCredential(suffix: string, source?: "login") {
  return {
    type: "api_key" as const,
    key: `${CANARY}-api-${suffix}`,
    ...(source ? { source } : {}),
  };
}

async function withAuthStorage<T>(
  authModule: any,
  run: (storage: any, store: any) => Promise<T>,
): Promise<T> {
  const directory = await mkdtemp(join(tmpdir(), "bb-f6-omp-auth-"));
  const store = await authModule.SqliteAuthCredentialStore.open(
    join(directory, "agent.db"),
  );
  try {
    return await run(new authModule.AuthStorage(store), store);
  } finally {
    store.close();
    await rm(directory, { recursive: true, force: true });
  }
}

async function authSemanticProjection(
  family: string,
  authModule: any,
): Promise<Record<string, unknown>> {
  if (family === "api_key_precedence") {
    return withAuthStorage(authModule, async (storage) => {
      const provider = "openai";
      const values = {
        runtime: `${CANARY}-runtime`,
        config: `${CANARY}-config`,
        oauth: `${CANARY}-oauth`,
        login_api_key: `${CANARY}-login`,
        env: `${CANARY}-env`,
        stored_api_key: `${CANARY}-stored`,
        fallback: `${CANARY}-fallback`,
      };
      const sourceByValue = new Map(
        Object.entries(values).map(([source, value]) => [value, source]),
      );
      const previousEnv = process.env.OPENAI_API_KEY;
      process.env.OPENAI_API_KEY = values.env;
      try {
        storage.setFallbackResolver((selected: string) =>
          selected === provider ? values.fallback : undefined,
        );
        await storage.set(provider, [
          {
            type: "oauth",
            access: values.oauth,
            refresh: `${CANARY}-precedence-refresh`,
            expires: Date.now() + 60 * 60_000,
            accountId: "precedence-oauth",
          },
          { type: "api_key", key: values.login_api_key, source: "login" },
          { type: "api_key", key: values.stored_api_key },
        ]);
        storage.setRuntimeApiKey(provider, values.runtime);
        storage.setConfigApiKey(provider, values.config);
        const selected: string[] = [];
        const resolveSource = async () => {
          const value = await storage.getApiKey(provider);
          const source = value && sourceByValue.get(value);
          if (!source)
            die("OMP auth precedence returned an unknown credential");
          selected.push(source);
        };
        await resolveSource();
        storage.removeRuntimeApiKey(provider);
        await resolveSource();
        storage.removeConfigApiKey(provider);
        await resolveSource();
        await storage.set(provider, [
          { type: "api_key", key: values.login_api_key, source: "login" },
          { type: "api_key", key: values.stored_api_key },
        ]);
        await resolveSource();
        await storage.set(provider, [
          { type: "api_key", key: values.stored_api_key },
        ]);
        await resolveSource();
        delete process.env.OPENAI_API_KEY;
        await resolveSource();
        await storage.set(provider, []);
        await resolveSource();
        return { selected_sources: selected };
      } finally {
        if (previousEnv === undefined) delete process.env.OPENAI_API_KEY;
        else process.env.OPENAI_API_KEY = previousEnv;
      }
    });
  }
  if (family === "codex_oauth_precedence") {
    let fallbackCalls = 0;
    return withAuthStorage(authModule, async (storage) => {
      const provider = "openai-codex";
      storage.setFallbackResolver((selected: string) => {
        fallbackCalls += selected === provider ? 1 : 0;
        return selected === provider ? `${CANARY}-codex-fallback` : undefined;
      });
      const credential = oauthCredential("codex");
      await storage.set(provider, credential);
      const selected = await storage.getApiKey(provider);
      return {
        selected_source:
          storage.getCredentialOrigin(provider)?.kind ?? "unavailable",
        fallback_ignored: fallbackCalls === 0,
        oauth_selected: selected === credential.access,
      };
    });
  }
  if (family === "explicit_account_binding") {
    return withAuthStorage(authModule, async (storage, store) => {
      const provider = "unit-f6-explicit";
      await storage.set(provider, [
        oauthCredential("first"),
        oauthCredential("second"),
      ]);
      const target = storage.listOAuthAccounts(provider)[1];
      if (!target) die("OMP explicit account fixture lacks a second account");
      const pinned = storage.pinSessionOAuthAccount(
        provider,
        "session-explicit",
        target.credentialId,
      );
      const selected = storage.getOAuthAccountIdentity(
        provider,
        "session-explicit",
      )?.email;
      const restarted = new authModule.AuthStorage(store);
      await restarted.reload();
      const restored = restarted.getOAuthAccountIdentity(
        provider,
        "session-explicit",
      )?.email;
      return {
        explicit_selection_persisted:
          pinned && selected === "second@example.test" && restored === selected,
        selected_account: selected?.split("@", 1)[0] ?? null,
        silently_rotated: restored !== selected,
      };
    });
  }
  if (family === "automatic_affinity_restart_rotation") {
    return withAuthStorage(authModule, async (storage, store) => {
      const provider = "unit-f6-affinity";
      const session = "session-affinity";
      const first = apiKeyCredential("affinity-first");
      const second = apiKeyCredential("affinity-second");
      await storage.set(provider, [first, second]);
      const initial = await storage.getApiKey(provider, session);
      const selectedRow = storage
        .listStoredCredentials(provider)
        .find((row: any) => row.credential.key === initial);
      if (!selectedRow) die("OMP automatic affinity did not select a row");
      const restarted = new authModule.AuthStorage(store);
      await restarted.reload();
      const afterRestart = await restarted.getApiKey(provider, session);
      const rotatedSecret = `${CANARY}-api-affinity-rotated`;
      store.updateAuthCredential(selectedRow.id, {
        type: "api_key",
        key: rotatedSecret,
      });
      await restarted.reload();
      const afterSecretRotation = await restarted.getApiKey(provider, session);
      await restarted.removeCredential(provider, selectedRow.id);
      const afterUnavailable = await restarted.getApiKey(provider, session);
      return {
        restart_preserved: afterRestart === initial,
        secret_rotation_preserved: afterSecretRotation === rotatedSecret,
        rotated_when_unavailable:
          afterUnavailable !== undefined &&
          afterUnavailable !== afterSecretRotation,
      };
    });
  }
  if (family === "classified_429_rotation") {
    return withAuthStorage(authModule, async (storage) => {
      const provider = "unit-f6-rate";
      const session = "session-rate";
      await storage.set(provider, [
        apiKeyCredential("rate-first"),
        apiKeyCredential("rate-second"),
      ]);
      const initial = await storage.getApiKey(provider, session);
      const error: any = new Error("429 rate limit exceeded");
      error.status = 429;
      const switched = await storage.rotateSessionCredential(
        provider,
        session,
        {
          error,
          apiKey: initial,
        },
      );
      const replacement = await storage.getApiKey(provider, session);
      return {
        automatic_rotated_on_classified_429:
          switched && replacement !== undefined && replacement !== initial,
      };
    });
  }
  if (family === "refresh_single_flight") {
    return withAuthStorage(authModule, async (first, store) => {
      const provider = "unit-f6-refresh-flight";
      await first.set(provider, oauthCredential("flight", Date.now() - 1));
      const row = first.listStoredCredentials(provider)[0];
      if (!row) die("OMP single-flight fixture lacks a credential");
      const second = new authModule.AuthStorage(store);
      await second.reload();
      let providerCalls = 0;
      let releaseRefresh: (() => void) | undefined;
      let markEntered: (() => void) | undefined;
      const entered = new Promise<void>((resolveEntered) => {
        markEntered = resolveEntered;
      });
      const released = new Promise<void>((resolveReleased) => {
        releaseRefresh = resolveReleased;
      });
      const refresh = async () => {
        providerCalls += 1;
        markEntered?.();
        await released;
        return oauthCredential("flight-new");
      };
      const options = {
        credentialId: row.id,
        observedCredential: row.credential,
        credentialFromRow: (value: any) =>
          value?.type === "oauth" ? value : undefined,
        forceRefresh: true,
        refreshSkewMs: 0,
        refreshTimeoutMs: 2_000,
        refresh,
      };
      const firstResult = first.refreshStoredOAuthCredential(provider, options);
      await entered;
      const secondResult = second.refreshStoredOAuthCredential(
        provider,
        options,
      );
      await Promise.resolve();
      releaseRefresh?.();
      const results = await Promise.all([firstResult, secondResult]);
      return {
        provider_calls: providerCalls,
        workers_converged:
          results.every(
            (result: any) =>
              result.credential?.access === `${CANARY}-access-flight-new`,
          ) && results.length === 2,
      };
    });
  }
  if (family === "refresh_transient_deferral") {
    return withAuthStorage(authModule, async (storage) => {
      const provider = "unit-f6-refresh-transient";
      await storage.set(provider, oauthCredential("transient", Date.now() - 1));
      const row = storage.listStoredCredentials(provider)[0];
      if (!row) die("OMP transient refresh fixture lacks a credential");
      let providerCalls = 0;
      const result = await storage.refreshStoredOAuthCredential(provider, {
        credentialId: row.id,
        observedCredential: row.credential,
        credentialFromRow: (value: any) =>
          value?.type === "oauth" ? value : undefined,
        forceRefresh: true,
        refresh: async () => {
          providerCalls += 1;
          throw new Error("temporarily unavailable");
        },
        keepCredentialOnRefreshFailure: true,
      });
      return {
        credential_retained:
          result.credential?.access === row.credential.access &&
          storage.listStoredCredentials(provider).length === 1,
        revoked: result.removed,
        provider_calls: providerCalls,
      };
    });
  }
  if (family === "refresh_definitive_tombstone") {
    return withAuthStorage(authModule, async (storage) => {
      const provider = "unit-f6-refresh-definitive";
      await storage.set(
        provider,
        oauthCredential("definitive", Date.now() - 1),
      );
      const row = storage.listStoredCredentials(provider)[0];
      if (!row) die("OMP definitive refresh fixture lacks a credential");
      let providerCalls = 0;
      const result = await storage.refreshStoredOAuthCredential(provider, {
        credentialId: row.id,
        observedCredential: row.credential,
        credentialFromRow: (value: any) =>
          value?.type === "oauth" ? value : undefined,
        forceRefresh: true,
        refresh: async () => {
          providerCalls += 1;
          throw new Error("invalid_grant");
        },
        isDefinitiveFailure: () => true,
        disabledCause: () => "oauth refresh failed: invalid_grant",
      });
      const tombstones = await storage.listDisabledCredentials(provider);
      return {
        credential_tombstoned:
          result.removed &&
          tombstones.length === 1 &&
          storage.listStoredCredentials(provider).length === 0,
        active_credentials: storage.listStoredCredentials(provider).length,
        provider_calls: providerCalls,
      };
    });
  }
  if (family === "revoke_during_refresh") {
    return withAuthStorage(authModule, async (refresher, store) => {
      const provider = "unit-f6-refresh-race";
      await refresher.set(provider, oauthCredential("race", Date.now() - 1));
      const row = refresher.listStoredCredentials(provider)[0];
      if (!row) die("OMP revoke race fixture lacks a credential");
      const revoker = new authModule.AuthStorage(store);
      await revoker.reload();
      let markEntered: (() => void) | undefined;
      let releaseRefresh: (() => void) | undefined;
      const entered = new Promise<void>((resolveEntered) => {
        markEntered = resolveEntered;
      });
      const released = new Promise<void>((resolveReleased) => {
        releaseRefresh = resolveReleased;
      });
      const pending = refresher.refreshStoredOAuthCredential(provider, {
        credentialId: row.id,
        observedCredential: row.credential,
        credentialFromRow: (value: any) =>
          value?.type === "oauth" ? value : undefined,
        forceRefresh: true,
        refresh: async () => {
          markEntered?.();
          await released;
          return oauthCredential("race-new");
        },
      });
      await entered;
      const revoked = await revoker.removeCredential(provider, row.id);
      releaseRefresh?.();
      const result = await pending;
      await revoker.reload();
      return {
        refresh_result:
          result.credential === undefined && result.refreshed === false
            ? "discarded"
            : "committed",
        resurrected:
          !revoked || revoker.listStoredCredentials(provider).length !== 0,
      };
    });
  }
  die(`unknown OMP auth scenario: ${family}`);
}

async function roleSemanticProjection(
  family: string,
  resolverModule: any,
  catalogModule: any,
): Promise<Record<string, unknown>> {
  const openai = catalogModule.buildModel(modelSpec("openai"));
  const anthropic = catalogModule.buildModel(modelSpec("anthropic"));
  if (family === "public_alias_selection") {
    const settings = {
      getModelRole: (role: string) =>
        role === "default" ? "openai/gpt-5-mini" : undefined,
    };
    const result = resolverModule.resolveModelRoleValue("@default", [openai], {
      settings,
    });
    return {
      alias_resolved: result.model !== undefined,
      provider_id: result.model?.provider ?? null,
      model_id: result.model?.id ?? null,
    };
  }
  if (family === "unknown_unavailable") {
    const settings = { getModelRole: () => undefined };
    const known = resolverModule.resolveModelRoleValue("@smol", [], {
      settings,
    });
    const unknown = resolverModule.resolveModelRoleValue(
      "@not-configured",
      [],
      { settings },
    );
    return {
      known_unavailable_resolved: known.model !== undefined,
      unknown_resolved: unknown.model !== undefined,
    };
  }
  if (family === "lock_secret_rotation_restart") {
    let configured = "openai/gpt-5-mini";
    const settings = { getModelRole: () => configured };
    const before = resolverModule.resolveModelRoleValue(
      "@default",
      [openai, anthropic],
      { settings },
    );
    configured = "anthropic/claude-3-opus";
    const after = resolverModule.resolveModelRoleValue(
      "@default",
      [openai, anthropic],
      { settings },
    );
    return {
      target_stable_after_configuration_change:
        before.model?.provider === after.model?.provider &&
        before.model?.id === after.model?.id,
      lock_restored: false,
    };
  }
  if (family === "auth_policy_no_fallback") {
    const registry = {
      getAvailable: () => [anthropic, openai],
      getApiKey: async (model: any) =>
        model.provider === "openai" ? `${CANARY}-role-auth` : undefined,
    };
    const result = await resolverModule.resolveModelOverrideWithAuthFallback(
      ["anthropic/claude-3-opus"],
      "openai/gpt-5-mini",
      registry,
    );
    return {
      auth_failure_fallback_used: result.authFallbackUsed,
      selected_provider: result.model?.provider ?? null,
    };
  }
  if (family === "cross_provider_default_forbidden") {
    const result = resolverModule.resolveModelRoleValue(
      "anthropic/missing,openai/gpt-5-mini",
      [openai],
    );
    return {
      cross_provider_fallback_used: result.model?.provider === "openai",
      selected_provider: result.model?.provider ?? null,
    };
  }
  die(`unknown OMP role scenario: ${family}`);
}

async function sourceObservation(
  kind: "auth" | "role",
  family: string,
  dependencies: {
    authModule: any;
    resolverModule: any;
    catalogModule: any;
  },
) {
  const sourceAnchors =
    kind === "auth"
      ? [
          "packages/ai/src/auth-storage.ts",
          "packages/ai/src/auth/sqlite-credential-store.ts",
        ]
      : [
          "packages/coding-agent/src/config/model-resolver.ts",
          "packages/coding-agent/src/config/model-roles.ts",
        ];
  const referenceTests =
    kind === "auth"
      ? [
          "packages/ai/test/auth-storage-codex-selection.test.ts",
          "packages/ai/test/auth-storage-oauth-account-select.test.ts",
        ]
      : [
          "packages/coding-agent/test/model-resolver.test.ts",
          "packages/coding-agent/test/issue-985-subagent-auth-fallback.test.ts",
        ];
  const rowId = `${kind}.${family}`;
  const absentFromOmp = AUTH_ROLE_DIVERGENCES.has(rowId);
  const semanticProjection =
    kind === "auth"
      ? await authSemanticProjection(family, dependencies.authModule)
      : await roleSemanticProjection(
          family,
          dependencies.resolverModule,
          dependencies.catalogModule,
        );
  const input = {
    kind,
    family,
    source_anchors: sourceAnchors,
    reference_tests: referenceTests,
  };
  const observations = {
    source_derived: true,
    reference_test_execution_required: true,
    portable_equivalent: !absentFromOmp,
    divergence_expected: absentFromOmp,
    semantic_projection: semanticProjection,
    execution: {
      actual_api_calls: true,
      network_denied: true,
      secrets_redacted: true,
    },
  };
  return {
    raw_input: bounded(input),
    raw_output: bounded({
      semantic_projection: semanticProjection,
      execution: observations.execution,
    }),
    observations,
  };
}

async function capture(a: ReturnType<typeof args>) {
  const blobs = verify(a.root);
  const oldFetch = globalThis.fetch;
  const saved: Record<string, string | undefined> = {};
  for (const key of Object.keys(process.env)) {
    if (/API_KEY|TOKEN|SECRET|PASSWORD|PROXY|PI_/i.test(key)) {
      saved[key] = process.env[key];
      delete process.env[key];
    }
  }
  process.env.PI_CODEX_WEBSOCKET = "0";
  process.env.PI_CODEX_ZSTD = "0";
  process.env.PI_OPENROUTER_RESPONSES = "1";
  globalThis.fetch = (() => {
    throw new Error("ambient network denied");
  }) as typeof fetch;
  try {
    const ai: any = await import(
      pathToFileURL(resolve(a.root, "packages/ai/src/index.ts")).href
    );
    const catalog: any = await import(
      pathToFileURL(resolve(a.root, "packages/catalog/src/build.ts")).href
    );
    const registry: any = await import(
      pathToFileURL(resolve(a.root, "packages/ai/src/registry/registry.ts"))
        .href
    );
    const authModule: any = await import(
      pathToFileURL(resolve(a.root, "packages/ai/src/auth-storage.ts")).href
    );
    const resolverModule: any = await import(
      pathToFileURL(
        resolve(a.root, "packages/coding-agent/src/config/model-resolver.ts"),
      ).href
    );
    const rows: any[] = [];
    for (const provider of PROVIDERS) {
      const spec = modelSpec(provider);
      const model = catalog.buildModel(spec);
      const definition = registry.getProviderDefinition(PROVIDER_IDS[provider]);
      const input = scenarioInput(provider, "catalog_model_route");
      const output = {
        canonical_projection: {
          provider_id: provider,
          model_id: model.id,
        },
        provider_definition: {
          id: definition?.id ?? null,
          name: definition?.name ?? null,
          keys: definition ? Object.keys(definition).sort() : [],
        },
        model: {
          id: model.id,
          api: model.api,
          provider: model.provider,
          compat: model.compat,
          thinking: model.thinking ?? null,
        },
      };
      rows.push({
        id: `${provider}.catalog_model_route`,
        provider,
        family: "catalog_model_route",
        input,
        input_sha256: hash(json(input)),
        output,
        output_sha256: hash(json(output)),
      });
      for (const family of PROVIDER_FAMILIES.slice(1)) {
        const inRow = scenarioInput(provider, family);
        let outRow;
        if (family === "cancel_terminal") {
          const before = await streamCapture(
            provider,
            family,
            ai,
            catalog.buildModel,
            "before_output",
          );
          const after = await streamCapture(
            provider,
            family,
            ai,
            catalog.buildModel,
            "after_partial",
          );
          outRow = {
            raw_input: bounded(inRow),
            raw_request: bounded({
              before: before.raw_request,
              after: after.raw_request,
            }),
            raw_output: bounded({
              before: before.raw_output,
              after: after.raw_output,
            }),
            observations: {
              before_output: before.observations,
              after_partial: after.observations,
            },
          };
        } else if (family === "error_terminal") {
          const captures = [];
          for (const errorCase of SCENARIO.fake.errors) {
            captures.push({
              label: errorCase.label,
              capture: await streamCapture(
                provider,
                family,
                ai,
                catalog.buildModel,
                undefined,
                errorCase,
              ),
            });
          }
          outRow = {
            raw_input: bounded(inRow),
            raw_request: bounded(
              captures.map(({ label, capture }) => ({
                label,
                raw_request: capture.raw_request,
              })),
            ),
            raw_output: bounded(
              captures.map(({ label, capture }) => ({
                label,
                raw_output: capture.raw_output,
              })),
            ),
            observations: {
              semantic_traces: captures.map(({ label, capture }) => ({
                label,
                trace: capture.observations.semantic_trace,
              })),
              raw_cases: captures.map(({ label, capture }) => ({
                label,
                raw_terminal: capture.observations.raw_terminal,
                raw_error: capture.observations.raw_error,
                raw_error_status: capture.observations.raw_error_status,
              })),
            },
          };
        } else {
          outRow = await streamCapture(
            provider,
            family,
            ai,
            catalog.buildModel,
          );
        }
        rows.push({
          id: `${provider}.${family}`,
          provider,
          family,
          input: inRow,
          input_sha256: hash(json(inRow)),
          output: outRow,
          output_sha256: hash(json(outRow)),
        });
      }
    }
    for (const [kind, families] of [
      ["auth", AUTH_FAMILIES],
      ["role", ROLE_FAMILIES],
    ] as const) {
      for (const family of families) {
        const input = { kind, family };
        const output = await sourceObservation(kind, family, {
          authModule,
          resolverModule,
          catalogModule: catalog,
        });
        rows.push({
          id: `${kind}.${family}`,
          provider: "omp",
          family,
          input,
          input_sha256: hash(json(input)),
          output,
          output_sha256: hash(json(output)),
        });
      }
    }
    rows.sort((left, right) => left.id.localeCompare(right.id));
    if (
      rows.length !== 42 ||
      rows.map((row) => row.id).join("\n") !== EXPECTED_IDS.join("\n")
    ) {
      die("capture did not produce exact 42 rows");
    }
    const fixture = {
      schema_version: SCHEMA_VERSION,
      oracle: { head: OMP_HEAD, tree: OMP_TREE, source_blobs: blobs },
      runner_sha256: await runnerHash(),
      rows,
    };
    rejectCanary(fixture);
    if (!a.force && (await Bun.file(a.output!).exists())) {
      die("refusing overwrite without --force");
    }
    await Bun.write(a.output!, json(fixture));
  } finally {
    globalThis.fetch = oldFetch;
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}
async function check(a: ReturnType<typeof args>) {
  const blobs = verify(a.root);
  const raw = await Bun.file(a.fixture!).text();
  const fixture: any = JSON.parse(raw);
  if (json(fixture) !== raw) die("fixture is not canonical JSON with newline");
  if (
    fixture.schema_version !== SCHEMA_VERSION ||
    fixture.oracle?.head !== OMP_HEAD ||
    fixture.oracle?.tree !== OMP_TREE ||
    json(fixture.oracle.source_blobs) !== json(blobs)
  ) {
    die("schema/identity/source blob mismatch");
  }
  if (fixture.runner_sha256 !== (await runnerHash()))
    die("runner hash mismatch");
  if (!Array.isArray(fixture.rows) || fixture.rows.length !== 42) {
    die("fixture must contain exactly 42 rows");
  }
  const ids = fixture.rows.map((row: any) => row.id).sort();
  if (ids.join("\n") !== EXPECTED_IDS.join("\n")) die("row IDs mismatch");
  for (const row of fixture.rows) {
    if (
      hash(json(row.input)) !== row.input_sha256 ||
      hash(json(row.output)) !== row.output_sha256
    ) {
      die(`row hash mismatch: ${row.id}`);
    }
    rejectCanary(row);
    const providerRow = PROVIDERS.includes(row.provider);
    if (providerRow) {
      if (
        canonicalValue(row.input) !==
        canonicalValue(scenarioInput(row.provider, row.family))
      )
        die(`shared scenario mismatch: ${row.id}`);
      if (row.family === "catalog_model_route") {
        if (
          canonicalValue(row.output?.canonical_projection) !==
          canonicalValue({
            provider_id: row.provider,
            model_id: modelLocalId(row.provider),
          })
        )
          die(`catalog projection mismatch: ${row.id}`);
        continue;
      }
    }
    if (
      !row.output ||
      typeof row.output.raw_input !== "string" ||
      typeof row.output.raw_output !== "string" ||
      !row.output.observations
    ) {
      die(`canonical output spelling mismatch: ${row.id}`);
    }
    if (!providerRow) {
      const observations = row.output.observations;
      const expectedDivergence = AUTH_ROLE_DIVERGENCES.has(row.id);
      if (
        observations.source_derived !== true ||
        observations.reference_test_execution_required !== true ||
        observations.portable_equivalent !== !expectedDivergence ||
        observations.divergence_expected !== expectedDivergence ||
        !observations.semantic_projection ||
        typeof observations.semantic_projection !== "object" ||
        Array.isArray(observations.semantic_projection) ||
        observations.execution?.actual_api_calls !== true ||
        observations.execution?.network_denied !== true ||
        observations.execution?.secrets_redacted !== true
      ) {
        die(`actual auth/role projection is incomplete: ${row.id}`);
      }
      continue;
    }
    if (typeof row.output.raw_request !== "string")
      die(`model-visible request is missing: ${row.id}`);
    if (row.family === "error_terminal") {
      const traces = row.output.observations.semantic_traces;
      const rawCases = row.output.observations.raw_cases;
      if (
        !Array.isArray(traces) ||
        traces.length !== 3 ||
        !Array.isArray(rawCases) ||
        rawCases.length !== 3
      )
        die(`error taxonomy is incomplete: ${row.id}`);
      for (const [index, errorCase] of SCENARIO.fake.errors.entries()) {
        const traced = traces[index];
        const actual = rawCases[index];
        if (
          traced?.label !== errorCase.label ||
          traced?.trace?.terminal?.state !== "error" ||
          traced?.trace?.terminal?.output_emitted !== false ||
          traced?.trace?.terminal?.http_status !== errorCase.http_status ||
          actual?.label !== errorCase.label ||
          actual?.raw_terminal?.state !== "error" ||
          typeof actual?.raw_error !== "string" ||
          !actual.raw_error
        )
          die(`error evidence mismatch: ${row.id}/${errorCase.label}`);
      }
      continue;
    }
    if (row.family === "cancel_terminal") {
      for (const phase of ["before_output", "after_partial"]) {
        const observed = row.output.observations[phase];
        const traceValue = observed?.semantic_trace;
        if (
          !traceValue ||
          traceValue.terminal?.state !== "cancelled" ||
          traceValue.usage !== null
        )
          die(`invalid cancellation observation: ${row.id}/${phase}`);
        const kinds =
          traceValue.events?.map((event: { kind: string }) => event.kind) ?? [];
        const after = phase === "after_partial";
        if (
          traceValue.terminal.output_emitted !== after ||
          kinds.includes("text_delta") !== after
        )
          die(`cancellation boundary mismatch: ${row.id}/${phase}`);
      }
      continue;
    }
    const traceValue = row.output.observations.semantic_trace;
    if (
      canonicalValue(traceValue?.request) !==
        canonicalValue(SCENARIO.request) ||
      traceValue?.provider !== row.provider ||
      traceValue?.model !== SCENARIO.models[row.provider].id ||
      traceValue?.terminal?.state !== "done" ||
      traceValue?.terminal?.output_emitted !== true ||
      typeof row.output.observations.raw_message !== "string"
    )
      die(`actual semantic projection is incomplete: ${row.id}`);
    assertCompletedProjection(row.family, traceValue.result);
    if (row.family === "usage_finish" && traceValue.usage == null)
      die(`usage observation is missing: ${row.id}`);
    if (row.family !== "usage_finish" && traceValue.usage !== null)
      die(`usage unexpectedly materialized: ${row.id}`);
  }
  console.log("ok: exact 42 F6 oracle rows");
}
const parsed = args(process.argv.slice(2));
if (parsed.mode === "capture") await capture(parsed);
else await check(parsed);
