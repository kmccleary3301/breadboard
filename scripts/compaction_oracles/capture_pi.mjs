#!/usr/bin/env node
/**
 * Capture pi@0.73.1 compaction oracle cases by executing the installed
 * @mariozechner/pi-coding-agent package.
 *
 *   node scripts/compaction_oracles/capture_pi.mjs <pi-coding-agent package dir> [--write-prompts]
 *
 * Every case calls the package's own exports (shouldCompact,
 * calculateContextTokens, prepareCompaction, compact, buildSessionContext,
 * convertToLlm). Summary model calls go through pi-ai's completeSimple to a
 * scripted provider registered with pi-ai's registerApiProvider, which records
 * each request and returns the case's scripted text. Cases are written as
 * bb.compaction_oracle_case.v1 with BreadBoard chat-format messages; the
 * script converts them to Pi messages and converts Pi's projected context back.
 *
 * Prompt files under breadboard_engine/compaction/presets/prompts/pi@0.73.1/
 * are checked byte for byte against the package (written with --write-prompts).
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const PRESET = "pi@0.73.1";
const SCRIPT = "scripts/compaction_oracles/capture_pi.mjs";
const REPO_ROOT = resolve(dirname(new URL(import.meta.url).pathname), "../..");
const OUT_DIR = join(REPO_ROOT, "tests/compaction/oracles", PRESET);
const PROMPT_DIR = join(REPO_ROOT, "breadboard_engine/compaction/presets/prompts", PRESET);

const args = process.argv.slice(2);
const pkgDir = args.find((a) => !a.startsWith("--"));
const writePrompts = args.includes("--write-prompts");
if (!pkgDir) {
  console.error("usage: capture_pi.mjs <pi-coding-agent package dir> [--write-prompts]");
  process.exit(2);
}
const pkgRoot = resolve(pkgDir);
const pkgJson = JSON.parse(readFileSync(join(pkgRoot, "package.json"), "utf8"));
if (pkgJson.name !== "@mariozechner/pi-coding-agent" || pkgJson.version !== "0.73.1") {
  console.error(`expected @mariozechner/pi-coding-agent@0.73.1 at ${pkgRoot}, found ${pkgJson.name}@${pkgJson.version}`);
  process.exit(2);
}

/** Node resolution of @mariozechner/pi-ai from the package, so we load the same module instance it does. */
function resolvePiAi(from) {
  let dir = from;
  for (;;) {
    const candidate = join(dir, "node_modules/@mariozechner/pi-ai/dist/index.js");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`cannot resolve @mariozechner/pi-ai from ${from}`);
    dir = parent;
  }
}

const file = (rel) => join(pkgRoot, "dist", rel);
const imp = (path) => import(pathToFileURL(path).href);
const compaction = await imp(file("core/compaction/index.js"));
const messagesMod = await imp(file("core/messages.js"));
const sessionMod = await imp(file("core/session-manager.js"));
const piAi = await imp(resolvePiAi(join(pkgRoot, "dist/core/compaction")));

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
const SOURCES = ["dist/core/compaction/compaction.js", "dist/core/compaction/utils.js", "dist/core/messages.js", "dist/core/session-manager.js"];
const sourceDigests = Object.fromEntries(SOURCES.map((rel) => [rel, sha256(readFileSync(join(pkgRoot, rel)))]));

// ---------------------------------------------------------------------------
// Prompts: non-exported template literals are sliced from compaction.js and
// evaluated; the rest are package exports.
// ---------------------------------------------------------------------------

function literalConst(source, name) {
  const start = source.indexOf(`const ${name} = \``);
  if (start < 0) throw new Error(`${name} not found`);
  const open = source.indexOf("`", start);
  const close = source.indexOf("`;", open + 1);
  const literal = source.slice(open, close + 1);
  if (literal.includes("${")) throw new Error(`${name} is not a plain template literal`);
  return new Function(`return ${literal};`)();
}

const compactionSrc = readFileSync(file("core/compaction/compaction.js"), "utf8");
const lineOf = (needle) => compactionSrc.slice(0, compactionSrc.indexOf(needle)).split("\n").length;
const PROMPTS = {
  "summarization-system.txt": [compaction.SUMMARIZATION_SYSTEM_PROMPT, "dist/core/compaction/utils.js SUMMARIZATION_SYSTEM_PROMPT"],
  "summary.txt": [literalConst(compactionSrc, "SUMMARIZATION_PROMPT"), `dist/core/compaction/compaction.js:${lineOf("const SUMMARIZATION_PROMPT")} SUMMARIZATION_PROMPT`],
  "update-summary.txt": [literalConst(compactionSrc, "UPDATE_SUMMARIZATION_PROMPT"), `dist/core/compaction/compaction.js:${lineOf("const UPDATE_SUMMARIZATION_PROMPT")} UPDATE_SUMMARIZATION_PROMPT`],
  "turn-prefix.txt": [literalConst(compactionSrc, "TURN_PREFIX_SUMMARIZATION_PROMPT"), `dist/core/compaction/compaction.js:${lineOf("const TURN_PREFIX_SUMMARIZATION_PROMPT")} TURN_PREFIX_SUMMARIZATION_PROMPT`],
  "summary-context.txt": [
    `${messagesMod.COMPACTION_SUMMARY_PREFIX}{{summary}}${messagesMod.COMPACTION_SUMMARY_SUFFIX}`,
    "dist/core/messages.js COMPACTION_SUMMARY_PREFIX + {{summary}} + COMPACTION_SUMMARY_SUFFIX",
  ],
};

mkdirSync(PROMPT_DIR, { recursive: true });
let promptMismatch = false;
for (const [name, [text]] of Object.entries(PROMPTS)) {
  const path = join(PROMPT_DIR, name);
  if (writePrompts) writeFileSync(path, text);
  else if (!existsSync(path) || readFileSync(path, "utf8") !== text) {
    console.error(`prompt ${name} differs from the package; rerun with --write-prompts`);
    promptMismatch = true;
  }
}
const sourceJson = {
  repo: "https://github.com/badlogic/pi-mono",
  package: "@mariozechner/pi-coding-agent@0.73.1",
  commit: "781152fc24841dc54b22284514604048ebe5e2c9",
  published_package_sha256: "7bf5d492670c04fd7c599dee7e6eaabff964084affd216766107e6741df7a2e1",
  license: "MIT",
  extracted_by: SCRIPT,
  source_sha256: sourceDigests,
  files: Object.fromEntries(Object.entries(PROMPTS).map(([name, [, origin]]) => [name, origin])),
};
if (writePrompts) writeFileSync(join(PROMPT_DIR, "SOURCE.json"), JSON.stringify(sourceJson, null, 2) + "\n");
if (promptMismatch) process.exit(1);

// ---------------------------------------------------------------------------
// Scripted summary provider
// ---------------------------------------------------------------------------

const API = "bb-oracle-scripted";
let scripted = [];
let requests = [];
function respond(_model, context, options) {
  requests.push({
    system: context.systemPrompt,
    messages: context.messages.map((m) => ({ role: m.role, content: m.content.map((c) => c.text).join("") })),
    max_tokens: options?.maxTokens ?? null,
    tools: [],
  });
  const text = scripted.shift();
  if (text === undefined) throw new Error("scripted provider ran out of responses");
  const stream = piAi.createAssistantMessageEventStream();
  const message = {
    role: "assistant",
    content: [{ type: "text", text }],
    api: API,
    provider: "bb-oracle",
    model: "scripted",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop",
    timestamp: 0,
  };
  queueMicrotask(() => stream.push({ type: "done", reason: "stop", message }));
  return stream;
}
piAi.registerApiProvider({ api: API, stream: respond, streamSimple: respond }, "bb-oracle");
const MODEL = { id: "scripted", name: "scripted", api: API, provider: "bb-oracle", reasoning: false, input: ["text"], contextWindow: 200000, maxTokens: 32000, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, baseUrl: "" };

// ---------------------------------------------------------------------------
// BreadBoard chat format <-> Pi messages
// ---------------------------------------------------------------------------

const ZERO_USAGE = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

function toPi(message, index) {
  const ts = 1000 + index;
  if (message.role === "user") return { role: "user", content: typeof message.content === "string" ? message.content : message.content, timestamp: ts };
  if (message.role === "assistant") {
    const content = [];
    if (message.reasoning_content) content.push({ type: "thinking", thinking: message.reasoning_content });
    if (message.content) content.push({ type: "text", text: message.content });
    for (const call of message.tool_calls ?? []) {
      content.push({ type: "toolCall", id: call.id, name: call.function.name, arguments: JSON.parse(call.function.arguments) });
    }
    return { role: "assistant", content, api: "x", provider: "x", model: "x", usage: ZERO_USAGE, stopReason: message.tool_calls ? "toolUse" : "stop", timestamp: ts };
  }
  if (message.role === "tool") {
    const content = typeof message.content === "string" ? [{ type: "text", text: message.content }] : message.content;
    return { role: "toolResult", toolCallId: message.tool_call_id, toolName: message.name ?? "tool", content, isError: false, timestamp: ts };
  }
  throw new Error(`unsupported role ${message.role}`);
}

function fromPiLlm(message, byPiMessage) {
  if (byPiMessage.has(message)) return byPiMessage.get(message);
  if (message.role === "user") {
    const text = typeof message.content === "string" ? message.content : message.content.map((c) => c.text).join("");
    return { role: "user", content: text };
  }
  throw new Error(`unexpected generated ${message.role} message`);
}

function entriesFor(history, ledger) {
  // Session entries in append order: messages, with each prior compaction
  // entry inserted where the history stood when it was made.
  const entries = [];
  const piMessages = [];
  let parent = null;
  const push = (entry) => {
    entry.parentId = parent;
    parent = entry.id;
    entries.push(entry);
  };
  const ids = history.map((_, i) => `m${i}`);
  let next = 0;
  for (const [n, record] of (ledger ?? []).entries()) {
    for (; next < record.history_length; next++) {
      if (history[next].role === "system") continue;
      const pi = toPi(history[next], next);
      piMessages[next] = pi;
      push({ type: "message", id: ids[next], timestamp: new Date(1000 + next).toISOString(), message: pi });
    }
    push({
      type: "compaction",
      id: `c${n}`,
      timestamp: new Date(5000 + n).toISOString(),
      summary: record.summary,
      firstKeptEntryId: ids[record.first_kept_index],
      tokensBefore: 0,
      details: { readFiles: record.details?.read_files ?? [], modifiedFiles: record.details?.modified_files ?? [] },
    });
  }
  for (; next < history.length; next++) {
    if (history[next].role === "system") continue;
    const pi = toPi(history[next], next);
    piMessages[next] = pi;
    push({ type: "message", id: ids[next], timestamp: new Date(1000 + next).toISOString(), message: pi });
  }
  return { entries, piMessages };
}

// ---------------------------------------------------------------------------
// Case helpers
// ---------------------------------------------------------------------------

const SRC = { repo: "https://github.com/badlogic/pi-mono", commit: "781152fc24841dc54b22284514604048ebe5e2c9", package: "@mariozechner/pi-coding-agent@0.73.1", package_sha256: "7bf5d492670c04fd7c599dee7e6eaabff964084affd216766107e6741df7a2e1" };
const cases = [];

function baseInput(messages, extra = {}) {
  return { messages, usage: null, context_window: 200000, max_input_tokens: null, max_output_tokens: null, reason: "threshold", native_settings: {}, ...extra };
}

function captureThreshold(name, usage, window, settings, evidence) {
  const piUsage = { input: usage.input_tokens, output: usage.output_tokens, cacheRead: usage.cache_read_tokens, cacheWrite: usage.cache_write_tokens, totalTokens: usage.total_tokens };
  const tokens = compaction.calculateContextTokens(piUsage);
  const merged = { ...compaction.DEFAULT_COMPACTION_SETTINGS, ...settings };
  const fires = compaction.shouldCompact(tokens, window, merged);
  const native = {};
  if (settings.reserveTokens !== undefined) native.reserveTokens = settings.reserveTokens;
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET,
    case: name,
    source: { ...SRC, evidence },
    capture: { kind: "executed", script: SCRIPT, notes: "calculateContextTokens(usage) then shouldCompact(tokens, contextWindow, settings), called from the installed package (dist/core/compaction/compaction.js:76-78,149-153). The pre-prompt check reads the last assistant usage (dist/core/agent-session.js:1441)." },
    input: baseInput(
      [
        { role: "user", content: "earlier task" },
        { role: "assistant", content: "done" },
        { role: "user", content: "next task" },
      ],
      { usage, context_window: window, native_settings: native },
    ),
    expect: { trigger: { fires, tokens, limit: window - merged.reserveTokens, severity: "soft" } },
  });
}

async function captureCompaction(name, { history, settings = {}, responses, ledger, notes, reason = "threshold" }) {
  const merged = { ...compaction.DEFAULT_COMPACTION_SETTINGS, ...settings };
  const { entries, piMessages } = entriesFor(history, ledger);
  const indexOf = new Map(piMessages.map((m, i) => [m, i]).filter(([m]) => m));
  const preparation = compaction.prepareCompaction(entries, merged);
  if (!preparation) throw new Error(`${name}: prepareCompaction returned undefined`);
  scripted = [...responses];
  requests = [];
  const result = await compaction.compact(preparation, MODEL, "key", undefined, undefined, undefined, undefined);
  if (scripted.length) throw new Error(`${name}: ${scripted.length} scripted responses unused`);
  const firstKept = Number(result.firstKeptEntryId.slice(1));
  const sessionEntries = [...entries, { type: "compaction", id: "cnew", parentId: entries.at(-1).id, timestamp: new Date(9000).toISOString(), summary: result.summary, firstKeptEntryId: result.firstKeptEntryId, tokensBefore: result.tokensBefore, details: result.details }];
  const context = sessionMod.buildSessionContext(sessionEntries);
  const llm = messagesMod.convertToLlm(context.messages);
  const byPi = new Map();
  for (const m of llm) {
    const i = indexOf.get(m);
    if (i !== undefined) byPi.set(m, history[i]);
  }
  const head = history.filter((m) => m.role === "system");
  const projected = [...head, ...llm.map((m) => fromPiLlm(m, byPi))];
  const selection = {
    first_kept_index: firstKept,
    summarize: preparation.messagesToSummarize.map((m) => indexOf.get(m)),
  };
  if (preparation.isSplitTurn) selection.turn_prefix = preparation.turnPrefixMessages.map((m) => indexOf.get(m));
  const native = {};
  if (settings.reserveTokens !== undefined) native.reserveTokens = settings.reserveTokens;
  if (settings.keepRecentTokens !== undefined) native.keepRecentTokens = settings.keepRecentTokens;
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET,
    case: name,
    source: { ...SRC, evidence: ["dist/core/compaction/compaction.js:161-354,437-614", "dist/core/compaction/utils.js:14-150", "dist/core/messages.js:7-12,75-110", "dist/core/session-manager.js:112-200"] },
    capture: {
      kind: "executed",
      script: SCRIPT,
      notes: `prepareCompaction(entries, settings) and compact(preparation, model) from the installed package; summary calls reach a scripted pi-ai provider through completeSimple. Projected view is buildSessionContext + convertToLlm of the session with the new compaction entry, converted back to chat format. ${notes}`,
    },
    input: { ...baseInput(history, { native_settings: native, reason }), summary_responses: responses, ...(ledger ? { ledger } : {}) },
    expect: { selection, summary_requests: requests, projected_view: projected, summary: result.summary, details: { read_files: result.details.readFiles, modified_files: result.details.modifiedFiles } },
  });
}

// ---------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------

const TRIGGER_EVIDENCE = ["dist/core/compaction/compaction.js:76-78,149-153", "dist/core/agent-session.js:1376-1446"];
const usage = (input, output, total) => ({ input_tokens: input, output_tokens: output, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: total });
captureThreshold("threshold_total_below_limit", usage(180000, 3615, 183615), 200000, {}, TRIGGER_EVIDENCE);
captureThreshold("threshold_total_equal_limit_does_not_fire", usage(180000, 3616, 183616), 200000, {}, TRIGGER_EVIDENCE);
captureThreshold("threshold_total_above_limit_fires", usage(180000, 3617, 183617), 200000, {}, TRIGGER_EVIDENCE);
captureThreshold(
  "threshold_components_when_total_zero",
  { input_tokens: 100000, output_tokens: 2000, cache_read_tokens: 60000, cache_write_tokens: 30000, total_tokens: 0 },
  200000,
  { reserveTokens: 8000 },
  TRIGGER_EVIDENCE,
);

const sys = { role: "system", content: "You are pi." };
const tool = (id, name, args) => ({ id, type: "function", function: { name, arguments: JSON.stringify(args) } });
const pad = (label, n) => `${label} ${"x".repeat(n)}`;

await captureCompaction("summary_basic_cut_at_user", {
  history: [
    sys,
    { role: "user", content: pad("first task", 400) },
    { role: "assistant", content: pad("first answer", 400) },
    { role: "user", content: pad("second task", 400) },
    { role: "assistant", content: pad("second answer", 400) },
    { role: "user", content: pad("third task", 400) },
    { role: "assistant", content: pad("third answer", 300) },
  ],
  settings: { keepRecentTokens: 150 },
  responses: ["## Goal\nSummary of the first two tasks."],
  notes: "The walk crosses keepRecentTokens at a user message, so the cut is not a split turn.",
});

await captureCompaction("summary_split_turn_with_tool_results", {
  history: [
    sys,
    { role: "user", content: pad("old request", 300) },
    { role: "assistant", content: "ok, older work done" },
    { role: "user", content: "Fix the parser in src/parse.ts" },
    { role: "assistant", content: "Reading the file.", tool_calls: [tool("c1", "read", { path: "src/parse.ts" })] },
    { role: "tool", tool_call_id: "c1", name: "read", content: pad("file body", 3000) },
    { role: "assistant", content: "Editing.", reasoning_content: "The bug is the off-by-one.", tool_calls: [tool("c2", "edit", { path: "src/parse.ts", oldText: "i <= n", newText: "i < n" })] },
    { role: "tool", tool_call_id: "c2", name: "edit", content: "Edited src/parse.ts" },
    { role: "assistant", content: pad("Done. The loop bound was wrong.", 200) },
  ],
  settings: { keepRecentTokens: 200 },
  responses: ["## Goal\nOlder history summary.", "## Original Request\nFix the parser."],
  notes: "The cut lands on an assistant message inside the last turn: history and turn-prefix summaries run in parallel and are joined; the tool result over 2000 chars is truncated in the transcript; read/edit paths produce <read-files>/<modified-files> tags.",
});

await captureCompaction("summary_update_with_previous_summary", {
  history: [
    sys,
    { role: "user", content: pad("task one", 300) },
    { role: "assistant", content: pad("answer one", 300) },
    { role: "user", content: pad("task two", 300) },
    { role: "assistant", content: pad("answer two", 300) },
    { role: "user", content: pad("task three", 300) },
    { role: "assistant", content: pad("answer three", 300) },
    { role: "user", content: pad("task four", 60) },
    { role: "assistant", content: pad("answer four", 300) },
  ],
  ledger: [
    {
      first_kept_index: 3,
      history_length: 5,
      summary: "## Goal\nPrior summary of task one.\n\n<read-files>\nREADME.md\n</read-files>",
      details: { read_files: ["README.md"], modified_files: [] },
    },
  ],
  settings: { keepRecentTokens: 80 },
  responses: ["## Goal\nUpdated summary through task three."],
  notes: "A prior compaction entry exists: the update prompt carries <previous-summary> unescaped, the walk starts at its firstKeptEntry, and its readFiles carry over into the new tags.",
});

await captureCompaction("summary_budget_never_reached_keeps_everything", {
  history: [
    sys,
    { role: "user", content: "short task" },
    { role: "assistant", content: "short answer" },
    { role: "user", content: "another short task" },
  ],
  settings: {},
  responses: ["## Goal\n(nothing to summarize)"],
  reason: "manual",
  notes: "keepRecentTokens is never reached: the cut stays at the first cut point, nothing is summarized, and Pi still sends one summary request over an empty conversation.",
});

// ---------------------------------------------------------------------------
// Write
// ---------------------------------------------------------------------------

rmSync(OUT_DIR, { recursive: true, force: true });
mkdirSync(OUT_DIR, { recursive: true });
for (const c of cases) writeFileSync(join(OUT_DIR, `${c.case}.json`), JSON.stringify(c, null, 2) + "\n");
console.log(`wrote ${cases.length} cases to ${OUT_DIR}`);
console.log(readdirSync(OUT_DIR).join("\n"));
