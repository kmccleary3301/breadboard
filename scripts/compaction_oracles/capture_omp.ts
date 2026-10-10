#!/usr/bin/env bun
/**
 * Capture OMP compaction oracle cases by executing the installed OMP packages.
 *
 * Usage:
 *   bun scripts/compaction_oracles/capture_omp.ts [version] [pkgRoot] [--write-prompts]
 *
 * Parameterized by OMP version (default: 16.2.13) and package root
 * (default: ~/.cache/bb-compaction-e4/pkgs/@oh-my-pi__pi-coding-agent@<version>).
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const writePrompts = args.includes("--write-prompts");
const cleanArgs = args.filter((a) => !a.startsWith("--"));

let version = "16.2.13";
let pkgRootArg: string | undefined;

for (const arg of args) {
  if (arg.startsWith("--version=")) version = arg.split("=")[1];
  else if (arg.startsWith("--package-root=") || arg.startsWith("--pkg=")) pkgRootArg = arg.split("=")[1];
}

if (!pkgRootArg) {
  if (cleanArgs.length >= 2) {
    if (/^\d+\.\d+\.\d+/.test(cleanArgs[0])) {
      version = cleanArgs[0];
      pkgRootArg = cleanArgs[1];
    } else {
      pkgRootArg = cleanArgs[0];
      version = cleanArgs[1];
    }
  } else if (cleanArgs.length === 1) {
    if (/^\d+\.\d+\.\d+/.test(cleanArgs[0])) {
      version = cleanArgs[0];
    } else {
      pkgRootArg = cleanArgs[0];
    }
  }
}

const PRESET = `omp@${version}`;
const SCRIPT = "scripts/compaction_oracles/capture_omp.ts";
const REPO_ROOT = resolve(dirname(new URL(import.meta.url).pathname), "../..");
const OUT_DIR = join(REPO_ROOT, "tests/compaction/oracles", PRESET);
const PROMPT_DIR = join(REPO_ROOT, "breadboard_engine/compaction/presets/prompts", PRESET);

function expandHome(p: string): string {
  if (p.startsWith("~/") || p === "~") {
    return join(process.env.HOME || homedir(), p.slice(1));
  }
  return p;
}

const defaultPkgRoot = expandHome(`~/.cache/bb-compaction-e4/pkgs/@oh-my-pi__pi-coding-agent@${version}`);
const resolvedPkgRoot = resolve(expandHome(pkgRootArg || defaultPkgRoot));

function resolveOmpDirs(root: string) {
  let coreDir = join(root, "node_modules/@oh-my-pi/pi-agent-core");
  let agentDir = join(root, "node_modules/@oh-my-pi/pi-coding-agent");
  if (!existsSync(coreDir)) {
    if (existsSync(join(root, "packages/agent"))) coreDir = join(root, "packages/agent");
    else if (existsSync(join(root, "packages/agent-core"))) coreDir = join(root, "packages/agent-core");
    else if (existsSync(join(root, "src/compaction"))) coreDir = root;
  }
  if (!existsSync(agentDir)) {
    if (existsSync(join(root, "packages/coding-agent"))) agentDir = join(root, "packages/coding-agent");
    else if (existsSync(join(root, "src/session"))) agentDir = root;
  }
  if (!existsSync(coreDir)) {
    throw new Error(`Cannot locate pi-agent-core under ${root}`);
  }
  if (!existsSync(agentDir)) {
    throw new Error(`Cannot locate pi-coding-agent under ${root}`);
  }
  return { coreDir, agentDir };
}

const { coreDir, agentDir } = resolveOmpDirs(resolvedPkgRoot);
try {
  const { mock } = await import("bun:test");
  const identityPaths = [
    join(resolvedPkgRoot, "packages/catalog/src/identity/index.ts"),
    join(resolvedPkgRoot, "node_modules/@oh-my-pi/pi-catalog/src/identity/index.ts"),
    join(resolvedPkgRoot, "node_modules/@oh-my-pi/pi-catalog/identity"),
  ];
  const identityPath = identityPaths.find((p) => existsSync(p));
  if (identityPath) {
    const realIdentity = await import(pathToFileURL(identityPath).href);
    mock.module("@oh-my-pi/pi-catalog/identity", () => ({
      ...realIdentity,
      preferredDialect: () => undefined,
    }));
  }
} catch {}

const imp = (p: string) => import(pathToFileURL(p).href);
const compaction = await imp(existsSync(join(coreDir, "src/compaction/index.ts")) ? join(coreDir, "src/compaction/index.ts") : join(coreDir, "src/compaction.ts"));
const sessionContextMod = await imp(join(agentDir, "src/session/session-context.ts"));
const messagesMod = await imp(join(agentDir, "src/session/messages.ts"));

const sha256 = (data: Buffer | string) => createHash("sha256").update(data).digest("hex");

// ---------------------------------------------------------------------------
// Prompts verification and extraction
// ---------------------------------------------------------------------------
mkdirSync(PROMPT_DIR, { recursive: true });

const promptFiles = [
  "summarization-system.md",
  "compaction-summary.md",
  "compaction-update-summary.md",
  "compaction-turn-prefix.md",
  "compaction-short-summary.md",
  "compaction-summary-context.md",
  "auto-handoff-threshold-focus.md",
  "handoff-document.md",
  "snapcompact-archive-context.md",
];

const sourcePromptDir = existsSync(join(coreDir, "src/compaction/prompts"))
  ? join(coreDir, "src/compaction/prompts")
  : existsSync(join(coreDir, "prompts"))
    ? join(coreDir, "prompts")
    : null;

if (sourcePromptDir) {
  for (const file of promptFiles) {
    const srcFile = join(sourcePromptDir, file);
    const destFile = join(PROMPT_DIR, file);
    if (existsSync(srcFile)) {
      const content = readFileSync(srcFile, "utf8");
      if (writePrompts || !existsSync(destFile)) {
        writeFileSync(destFile, content);
      }
    }
  }
}

const sourceJson = {
  repo: "https://github.com/can1357/oh-my-pi",
  package: `@oh-my-pi/pi-coding-agent@${version}`,
  version,
  extracted_by: SCRIPT,
};
writeFileSync(join(PROMPT_DIR, "SOURCE.json"), JSON.stringify(sourceJson, null, 2) + "\n");

// ---------------------------------------------------------------------------
// Scripted summary completion
// ---------------------------------------------------------------------------
let scripted: string[] = [];
let recordedResponses: string[] = [];
let capturedRequests: Array<{
  messages: Array<{ role: string; content: string }>;
  max_tokens: number | null;
  tools: string[];
}> = [];

const scriptedCompleteSimple = async (_model: any, ctx: any, options: any) => {
  const systemPrompt = Array.isArray(ctx.systemPrompt) ? ctx.systemPrompt.join("\n") : (ctx.systemPrompt ?? "");
  const messages = (ctx.messages ?? []).map((m: any) => {
    let content = "";
    if (typeof m.content === "string") content = m.content;
    else if (Array.isArray(m.content)) {
      content = m.content.map((c: any) => c.text ?? "").join("");
    }
    return { role: m.role, content };
  });

  capturedRequests.push({
    system: systemPrompt,
    messages,
    max_tokens: options?.maxTokens ?? null,
    tools: [],
  });

  let text = scripted.shift();
  if (text === undefined) {
    text = "Short summary: completed work.";
  }
  recordedResponses.push(text);
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    stopReason: "stop",
  };
};

const DUMMY_MODEL = {
  id: "scripted",
  name: "scripted",
  provider: "bb-oracle",
  api: "bb-oracle-scripted",
  reasoning: false,
  input: ["text"],
  contextWindow: 200000,
  maxTokens: 32000,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  baseUrl: "",
};

// ---------------------------------------------------------------------------
// BreadBoard chat format <-> OMP messages
// ---------------------------------------------------------------------------
const ZERO_USAGE = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

function toOmp(message: any, index: number) {
  const ts = 1000 + index;
  if (message.role === "user") return { role: "user", content: message.content, timestamp: ts };
  if (message.role === "assistant") {
    const content: any[] = [];
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

function fromOmpLlm(message: any, byOmpMessage: Map<any, any>) {
  if (byOmpMessage.has(message)) return byOmpMessage.get(message);
  if (message.role === "user") {
    const text = typeof message.content === "string" ? message.content : message.content.map((c: any) => c.text ?? "").join("");
    return { role: "user", content: text };
  }
  throw new Error(`unexpected generated ${message.role} message`);
}

function entriesFor(history: any[], ledger?: any[]) {
  const entries: any[] = [];
  const ompMessages: any[] = [];
  let parent: string | null = null;
  const push = (entry: any) => {
    entry.parentId = parent;
    parent = entry.id;
    entries.push(entry);
  };
  const ids = history.map((_, i) => `m${i}`);
  let next = 0;
  for (const [n, record] of (ledger ?? []).entries()) {
    for (; next < record.history_length; next++) {
      if (history[next].role === "system") continue;
      const omp = toOmp(history[next], next);
      ompMessages[next] = omp;
      push({ type: "message", id: ids[next], timestamp: new Date(1000 + next).toISOString(), message: omp });
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
    const omp = toOmp(history[next], next);
    ompMessages[next] = omp;
    push({ type: "message", id: ids[next], timestamp: new Date(1000 + next).toISOString(), message: omp });
  }
  return { entries, ompMessages };
}

// ---------------------------------------------------------------------------
// Case helpers
// ---------------------------------------------------------------------------
// Source commits from config/e4_targets/oh_my_pi/*/source.json; package_sha256 is
// the sha256 of the published npm tarball (`npm pack`), as for the Pi oracles.
const SOURCE_PINS: Record<string, { commit: string; package_sha256: string }> = {
  "16.2.13": {
    commit: "5356713eae60e67ee64d9b02e3b5e377d248ee7f",
    package_sha256: "293056afc7d0d59c9aa8726efdbefc8d3a05a3dfca4261b4af5431d79a6593ff",
  },
  "18.1.17": {
    commit: "3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec",
    package_sha256: "cf12c50c85627122beeab2e5ed6572137226c7ecaec069dfc7a18eef36949eda",
  },
};
const pin = SOURCE_PINS[version];
if (!pin) throw new Error(`no source pin for OMP ${version}`);
const { commit, package_sha256 } = pin;
const SRC = {
  repo: "https://github.com/can1357/oh-my-pi",
  package: `@oh-my-pi/pi-coding-agent@${version}`,
  version,
  commit,
  package_sha256,
};

const cases: any[] = [];

function baseInput(messages: any[], extra: any = {}) {
  return {
    messages,
    usage: null,
    context_window: 200000,
    max_input_tokens: null,
    max_output_tokens: null,
    reason: "threshold",
    native_settings: {},
    ...extra,
  };
}

function captureThreshold(name: string, usage: any, window: number, settings: any, evidence: string[]) {
  const piUsage = {
    input: usage.input_tokens,
    output: usage.output_tokens,
    cacheRead: usage.cache_read_input_tokens ?? usage.cache_read_tokens ?? 0,
    cacheWrite: usage.cache_creation_input_tokens ?? usage.cache_write_tokens ?? 0,
    totalTokens: usage.total_tokens,
  };
  const tokens = compaction.calculateContextTokens(piUsage);
  const merged = { ...compaction.DEFAULT_COMPACTION_SETTINGS, ...settings };
  const fires = compaction.shouldCompact(tokens, window, merged);
  const limit = compaction.resolveThresholdTokens(window, merged);
  const native: any = {};
  if (settings.reserveTokens !== undefined) native.reserveTokens = settings.reserveTokens;
  if (settings.thresholdTokens !== undefined) native.thresholdTokens = settings.thresholdTokens;
  if (settings.thresholdPercent !== undefined) native.thresholdPercent = settings.thresholdPercent;

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET,
    case: name,
    source: { ...SRC, evidence },
    capture: {
      kind: "executed",
      script: SCRIPT,
      notes: "calculateContextTokens(usage) then shouldCompact(tokens, contextWindow, settings) and resolveThresholdTokens, called from the installed package exports.",
    },
    input: baseInput(
      [
        { role: "user", content: "earlier task" },
        { role: "assistant", content: "done" },
        { role: "user", content: "next task" },
      ],
      { usage, context_window: window, native_settings: native },
    ),
    expect: { trigger: { fires, tokens, limit, severity: "soft" } },
  });
}

async function captureCompaction(
  name: string,
  {
    history,
    settings = {},
    responses,
    ledger,
    notes,
    reason = "threshold",
  }: {
    history: any[];
    settings?: any;
    responses: string[];
    ledger?: any[];
    notes: string;
    reason?: string;
  },
) {
  const merged = { ...compaction.DEFAULT_COMPACTION_SETTINGS, ...settings };
  const { entries, ompMessages } = entriesFor(history, ledger);
  const indexOf = new Map(ompMessages.map((m, i) => [m, i]).filter(([m]) => m));
  const preparation = compaction.prepareCompaction(entries, merged);
  if (!preparation) throw new Error(`${name}: prepareCompaction returned undefined`);

  scripted = [...responses];
  recordedResponses = [];
  capturedRequests = [];

  const result = await compaction.compact(preparation, DUMMY_MODEL, "dummy-key", undefined, undefined, {
    completeImpl: scriptedCompleteSimple,
  });

  if (scripted.length) throw new Error(`${name}: ${scripted.length} scripted responses unused`);

  const firstKept = Number(result.firstKeptEntryId.slice(1));
  const sessionEntries = [
    ...entries,
    {
      type: "compaction",
      id: "cnew",
      parentId: entries.at(-1).id,
      timestamp: new Date(9000).toISOString(),
      summary: result.summary,
      firstKeptEntryId: result.firstKeptEntryId,
      tokensBefore: result.tokensBefore,
      details: result.details,
    },
  ];

  const context = sessionContextMod.buildSessionContext(sessionEntries);
  const llm = messagesMod.convertToLlm(context.messages);
  const byOmp = new Map();
  for (let i = 0; i < context.messages.length; i++) {
    const orig = context.messages[i];
    const idx = indexOf.get(orig);
    if (idx !== undefined) {
      byOmp.set(llm[i], history[idx]);
      byOmp.set(orig, history[idx]);
    }
  }
  for (const m of llm) {
    const i = indexOf.get(m);
    if (i !== undefined) byOmp.set(m, history[i]);
  }
  const head = history.filter((m) => m.role === "system");
  const projected = [...head, ...llm.map((m: any) => fromOmpLlm(m, byOmp))];

  const selection: any = {
    first_kept_index: firstKept,
    summarize: preparation.messagesToSummarize.map((m: any) => indexOf.get(m)),
  };
  if (preparation.isSplitTurn) {
    selection.turn_prefix = preparation.turnPrefixMessages.map((m: any) => indexOf.get(m));
  }

  const native: any = {};
  if (settings.reserveTokens !== undefined) native.reserveTokens = settings.reserveTokens;
  if (settings.keepRecentTokens !== undefined) native.keepRecentTokens = settings.keepRecentTokens;
  native.methodOrder = settings.methodOrder ?? ["soft"];
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET,
    case: name,
    source: {
      ...SRC,
      evidence: [
        "packages/agent-core/src/compaction/compaction.ts",
        "packages/coding-agent/src/session/agent-session.ts",
        "packages/coding-agent/src/session/session-context.ts",
      ],
    },
    capture: {
      kind: "executed",
      script: SCRIPT,
      notes: `prepareCompaction(entries, settings) and compact(preparation, model) from package exports; summary calls intercepted via scripted completeSimple. Projected view is buildSessionContext + convertToLlm converted back to chat format. ${notes}`,
    },
    input: {
      ...baseInput(history, { native_settings: native, reason }),
      summary_responses: recordedResponses,
      ...(ledger ? { ledger } : {}),
    },
    expect: {
      selection,
      summary_requests: capturedRequests,
      projected_view: projected,
      summary: result.summary,
      details: { read_files: result.details.readFiles, modified_files: result.details.modifiedFiles },
    },
  });
}

// ---------------------------------------------------------------------------
// Build Cases
// ---------------------------------------------------------------------------
const TRIGGER_EVIDENCE = [
  "packages/agent-core/src/compaction/compaction.ts:245-285",
  "packages/coding-agent/src/session/agent-session.ts:9580,9786",
];

const usage = (input: number, output: number, total: number) => ({
  input_tokens: input,
  output_tokens: output,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  total_tokens: total,
});

// For contextWindow=200000, 15% is 30000 tokens (greater than reserveTokens=16384).
// Effective reserve is 30000, threshold limit is 200000 - 30000 = 170000.
captureThreshold("threshold_total_below_limit", usage(160000, 9999, 169999), 200000, {}, TRIGGER_EVIDENCE);
captureThreshold("threshold_total_equal_limit_does_not_fire", usage(160000, 10000, 170000), 200000, {}, TRIGGER_EVIDENCE);
captureThreshold("threshold_total_above_limit_fires", usage(160000, 10001, 170001), 200000, {}, TRIGGER_EVIDENCE);
captureThreshold(
  "threshold_components_when_total_zero",
  {
    input_tokens: 100000,
    output_tokens: 2000,
    cache_read_tokens: 60000,
    cache_write_tokens: 30000,
    cache_read_input_tokens: 60000,
    cache_creation_input_tokens: 30000,
    total_tokens: 0,
  },
  200000,
  { reserveTokens: 8000 },
  TRIGGER_EVIDENCE,
);

const sys = { role: "system", content: "You are oh-my-pi." };
const tool = (id: string, name: string, args: any) => ({ id, type: "function", function: { name, arguments: JSON.stringify(args) } });
const pad = (label: string, n: number) => `${label} ${"x".repeat(n)}`;

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
  notes: "The backward walk crosses keepRecentTokens at user message 5, so the cut is not a split turn.",
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
  notes: "The cut lands on an assistant message inside the last turn: history and turn-prefix summaries run in parallel and are joined; read/edit paths produce file operations.",
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
  settings: { keepRecentTokens: 90 },
  responses: ["## Goal\nUpdated summary through task three."],
  notes: "A prior compaction entry exists: the update prompt carries <previous-summary>, and its readFiles carry over into the new details.",
});

await captureCompaction("summary_overflow_recovery", {
  history: [
    sys,
    { role: "user", content: pad("initial request", 400) },
    { role: "assistant", content: pad("first response", 400) },
    { role: "user", content: pad("follow up request", 400) },
    { role: "assistant", content: pad("final response", 200) },
  ],
  settings: { keepRecentTokens: 100 },
  reason: "overflow",
  responses: ["## Goal\nRecovered session after context overflow."],
  notes: "Compaction triggered with reason overflow.",
});

await captureCompaction("summary_snapcompact_text_only_fallback", {
  history: [
    sys,
    { role: "user", content: pad("first instruction", 400) },
    { role: "assistant", content: pad("first answer", 400) },
    { role: "user", content: pad("second instruction", 400) },
    { role: "assistant", content: pad("second answer", 200) },
  ],
  settings: { methodOrder: ["snapcompact", "soft"], keepRecentTokens: 100 },
  responses: ["## Goal\nFallback context-full summary on text-only model."],
  notes: "Model lacks vision; snapcompact is unavailable so pipeline falls back to soft summary.",
});

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------
rmSync(OUT_DIR, { recursive: true, force: true });
mkdirSync(OUT_DIR, { recursive: true });

for (const c of cases) {
  writeFileSync(join(OUT_DIR, `${c.case}.json`), JSON.stringify(c, null, 2) + "\n");
}

console.log(`wrote ${cases.length} cases to ${OUT_DIR}`);
console.log(readdirSync(OUT_DIR).join("\n"));
