import crypto from "node:crypto";
import { syncBuiltinESMExports } from "node:module";
import { fail, openaiCompletions, sessionModule } from "./pi_0_57_1_runtime.mjs";









const replayState = { observedMessages: [], replayClock: 1000, overflowRecoveryAttempted: false };
// Episode-local stock SessionManager, retained by the serial framed worker.
// Id seam: session-manager.js 0.73.1:13-20; 0.57.1:9-16.
// Entry clock seam: 0.73.1:580-589,617-630; 0.57.1:574-583,611-624.
const sessionManager = sessionModule.SessionManager.inMemory();
const sessionEntries = [];

const NativeDate = Date;
function nextTimestamp() { return ++replayState.replayClock; }
async function withReplayClock(timestamp, action) {
  globalThis.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [timestamp])); }
    static now() { return timestamp; }
  };
  try { return await action(); }
  finally { globalThis.Date = NativeDate; }
}
function appendSessionEntry(fields, timestamp) {
  const originalUuid = crypto.randomUUID;
  const originalDate = globalThis.Date;
  crypto.randomUUID = () => `${sessionEntries.length.toString(16).padStart(8, "0")}-0000-0000-0000-000000000000`;
  syncBuiltinESMExports();
  globalThis.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [timestamp])); }
    static now() { return timestamp; }
  };
  try {
    const id = fields.type === "message"
      ? sessionManager.appendMessage(fields.message)
      : sessionManager.appendCompaction(fields.summary, fields.firstKeptEntryId,
          fields.tokensBefore, fields.details, fields.fromHook);
    const entry = sessionManager.getEntry(id);
    sessionEntries.push(entry);
    return entry;
  } finally {
    crypto.randomUUID = originalUuid;
    syncBuiltinESMExports();
    globalThis.Date = originalDate;
  }
}
async function syncSessionMessages(messages, model) {
  if (messages.length < replayState.observedMessages.length
      || replayState.observedMessages.some((m, i) => JSON.stringify(m) !== JSON.stringify(messages[i]))) {
    fail("conductor history differs from retained Pi session context");
  }
  for (const original of messages.slice(replayState.observedMessages.length)) {
    if (!["user", "assistant", "toolResult"].includes(original.role)) {
      fail("new session messages must be stock AgentMessages");
    }
    // Clock seams: agent-session.js:656 (user), pi-ai openai-completions.js:46
    // (assistant), pi-agent-core agent-loop.js:257 (toolResult).
    // Replay event order, not BB's query-start timestamp across retries.
    const timestamp = nextTimestamp();
    const message = { ...original, timestamp };
    // Stock lifecycle reset: agent-session.js:186,223-227.
    if (message.role === "user" || (message.role === "assistant" && message.stopReason !== "error")) {
      replayState.overflowRecoveryAttempted = false;
    }
    // Stock initializes usage even when the provider emits no usage chunk
    // (0.57.1 openai-completions.js:31-47; 0.73.1:54-69).
    if (message.role === "assistant" && message.usage === undefined) {
      message.usage = await parseProviderUsage(undefined, timestamp, model);
    }
    appendSessionEntry({ type: "message", message }, timestamp);
  }
  replayState.observedMessages = structuredClone(messages);
}
// Replay only the provider transport, running the stock OpenAI stream parser.
// Usage parsing: 0.73.1 openai-completions.js:199-200; 0.57.1:92-115.
// Assistant clock: 0.73.1:69; 0.57.1:46.
async function parseProviderUsage(usage, timestamp, model) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    `data: ${JSON.stringify({ choices: [], usage })}\n\ndata: [DONE]\n\n`,
    { headers: { "content-type": "text/event-stream" } },
  );
  try {
    const message = await withReplayClock(timestamp, async () => {
      const stream = openaiCompletions.streamSimpleOpenAICompletions(model,
        { messages: [] }, { apiKey: "EMPTY" });
      for await (const event of stream) { /* stock parser owns usage */ }
      return stream.result();
    });
    if (message.stopReason === "error") fail(message.errorMessage);
    return message.usage;
  } finally { globalThis.fetch = originalFetch; }
}
async function prepareSession(payload, model) {
  await syncSessionMessages(payload.messages, model);
  const latest = sessionEntries.findLast((e) => e.type === "message"
    && e.message.role === "assistant" && e.message.stopReason !== "error");
  // The phase protocol's usage is the provider's raw OpenAI usage, not a Pi
  // alias. Parsing it also restores the last successful response before overflow.
  if (latest && ((payload.usage !== undefined && payload.usage !== null)
      || (latest.message.usage === undefined
        && (payload.reason === "threshold" || payload.checkpoint === "agent_end")))) {
    latest.message.usage = await parseProviderUsage(payload.usage, latest.message.timestamp, model);
  }
  return sessionManager.getBranch();
}





export { NativeDate, appendSessionEntry, nextTimestamp, parseProviderUsage, prepareSession, replayState, sessionEntries, sessionManager, syncSessionMessages, withReplayClock };
