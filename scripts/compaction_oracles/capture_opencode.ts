#!/usr/bin/env bun
/** Capture pinned OpenCode and plugin decisions without handwritten executed expectations.
 * Usage: OPENCODE_AI_SDK_ROOT=<ai@5.0.124 installation> bun scripts/compaction_oracles/capture_opencode.ts <opencode source> <plugin source>
 * Writes only under the current worktree. Run twice and compare case bytes.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { sourceRunner } from "./opencode_source_runner";
import type { RecaptureCase } from "./opencode_source_runner";

type ChatPart = { type: string; text?: string; image_url?: { url: string } };
type ChatMessage = { role: string; content: string | ChatPart[]; summary?: boolean; tool_call_id?: string;
  name?: string; compacted?: boolean; tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }> };
type NativeTool = { type: string; tool: string; callID: string; state: { status: string; output: string; time: { compacted?: number } } };
type NativeMessage = { info: { role: string; summary?: boolean }; parts: NativeTool[] };
type Config = { compaction?: { auto?: boolean; prune?: boolean; reserved?: number } };
type Case = RecaptureCase & { schema: string; case: string; source: { repo: string; commit: string; evidence: string[] };
  capture: { kind: "executed" | "source_derived"; script: string; notes: string };
  input: { messages: ChatMessage[]; usage: { input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number; total_tokens: number };
    context_window: number; max_input_tokens: number | null; max_output_tokens: number; reason: string; native_settings: Config; summary_responses: string[]; stage_ids?: string[];
    overflow_error?: string; overflow_tokens?: number; overflow_limit?: number } };
const [opencodeArg, pluginArg] = process.argv.slice(2);
if (!opencodeArg || !pluginArg) throw new Error("Expected pinned OpenCode and plugin source directories");
const opencode = resolve(opencodeArg);
const plugin = resolve(pluginArg);
const sourceFile = join(opencode, "packages/opencode/src/session/compaction.ts");
if (!existsSync(sourceFile)) throw new Error("Pinned source directory is missing compaction.ts");
const transpiler = new Bun.Transpiler({ loader: "ts" });
const slice = (path: string, first: number, last: number) => readFileSync(path, "utf8").split("\n").slice(first - 1, last).join("\n");
function compile<T>(source: string, names: string[], values: unknown[], returned: string): T {
  // Trusted pinned code is evaluated at its explicit collaborator boundary.
  return new Function(...names, transpiler.transformSync(source.replaceAll("export ", "")) + `\nreturn ${returned};`)(...values) as T;
}
const maxOutput = compile<(model: { limit: { output: number } }) => number>(slice(join(opencode, "packages/opencode/src/provider/transform.ts"), 875, 877), ["OUTPUT_TOKEN_MAX"], [32000], "maxOutputTokens");
const estimate = compile<(text: string) => number>(slice(join(opencode, "packages/opencode/src/util/token.ts"), 4, 6), ["CHARS_PER_TOKEN"], [4], "estimate");
let config: Config = {};
const isOverflow = compile<(input: { tokens: { input: number; output: number; total: number; cache: { read: number; write: number } }; model: { limit: { context: number; input: number | null; output: number } } }) => Promise<boolean>>(
  slice(sourceFile, 30, 48), ["Config", "ProviderTransform"], [{ get: async () => config }, { maxOutputTokens: maxOutput }], "isOverflow");
let nativeMessages: NativeMessage[] = [];
const updates: NativeTool[] = [];
const prune = compile<(input: { sessionID: string }) => Promise<void>>(slice(sourceFile, 50, 99), ["Config", "Session", "Token", "log"],
  [{ get: async () => config }, { messages: async () => nativeMessages, updatePart: async (part: NativeTool) => { updates.push(part); } }, { estimate }, { info: () => {} }], "prune");
const cases: Case[] = [];
function fixture(name: string, preset = "opencode@1.2.17"): Case {
  const base = preset.startsWith("opencode");
  return { schema: "bb.compaction_oracle_case.v1", preset, case: name,
    source: { repo: base ? "https://github.com/anomalyco/opencode" : "https://github.com/code-yeongyu/oh-my-openagent", commit: base ? "715b844c2a88810b6178d7a2467c7d36ea8fb764" : "e4e13cdebf2c57f3eecb2ab94950f7f6f681169a", evidence: [] },
    capture: { kind: "executed", script: "scripts/compaction_oracles/capture_opencode.ts", notes: "" },
    input: { messages: [{ role: "user", content: "Hello" }, { role: "assistant", content: "Hi" }], usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 0 }, context_window: 200000, max_input_tokens: null, max_output_tokens: 32000, reason: "threshold", native_settings: {}, summary_responses: [] }, expect: {} };
}
for (const branch of ["input_limit", "context_only"]) {
  for (const [boundary, delta] of [["below", -1], ["equal", 0], ["above", 1]] as const) {
    const c = fixture(`trigger_${branch}_${boundary}_usable`);
    c.input.max_input_tokens = branch === "input_limit" ? 150000 : null;
    c.input.usage.total_tokens = (branch === "input_limit" ? 130000 : 168000) + delta;
    cases.push(c);
  }
}
const precedence = fixture("trigger_provider_total_precedence");
precedence.input.max_input_tokens = 150000;
precedence.input.usage = { input_tokens: 10000, output_tokens: 10000, cache_read_tokens: 5000, cache_write_tokens: 5000, total_tokens: 140000 };
cases.push(precedence);
const components = fixture("trigger_component_sum_fallback");
components.input.max_input_tokens = 150000;
components.input.usage = { input_tokens: 70000, output_tokens: 20000, cache_read_tokens: 30000, cache_write_tokens: 15000, total_tokens: 0 };
cases.push(components);
const disabled = fixture("trigger_auto_disabled_blocks_threshold");
disabled.input.max_input_tokens = 150000;
disabled.input.usage.total_tokens = 190000;
disabled.input.native_settings = { compaction: { auto: false } };
cases.push(disabled);
for (const c of cases) {
  config = c.input.native_settings;
  const u = c.input.usage;
  const model = { limit: { context: c.input.context_window, input: c.input.max_input_tokens, output: c.input.max_output_tokens } };
  const fires = await isOverflow({ tokens: { input: u.input_tokens, output: u.output_tokens, total: u.total_tokens, cache: { read: u.cache_read_tokens, write: u.cache_write_tokens } }, model });
  const count = u.total_tokens || u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens;
  // Limit is the observable decision boundary; the native return is boolean.
  const output = maxOutput(model);
  const limit = model.limit.input ? model.limit.input - (config.compaction?.reserved ?? Math.min(20000, output)) : model.limit.context - output;
  c.expect = { trigger: { fires, tokens: count, limit, severity: "soft" } };
  c.source.evidence = ["packages/opencode/src/session/compaction.ts:30-48"];
  c.capture.notes = "Executed pinned isOverflow with provider totals preserved verbatim in input; limit is the formula at compaction.ts:42-46.";
}
const overflow = fixture("trigger_auto_disabled_with_overflow");
overflow.input.reason = "overflow";
overflow.input.usage.total_tokens = 195000;
overflow.input.native_settings = { compaction: { auto: false } };
overflow.capture.kind = "source_derived";
overflow.source.evidence = ["packages/opencode/src/session/processor.ts:355-367", "packages/opencode/src/session/prompt.ts:536-540"];
overflow.capture.notes = "Provider overflow is an explicit ingress, independent of the threshold switch. processor.ts:359-363 sets needsCompaction on ContextOverflowError; prompt.ts:539 passes overflow: !lastAssistant.finish.";
overflow.expect = { trigger: { fires: true, tokens: 195000, limit: 168000, severity: "hard" } };
cases.push(overflow);

const user = (text: string): ChatMessage => ({ role: "user", content: text });
const assistant = (text: string): ChatMessage => ({ role: "assistant", content: text });
const tool = (id: string, chars: number, name = "bash"): ChatMessage => ({ role: "tool", tool_call_id: id, name, content: "a".repeat(chars) });
const pruneFixtures: Array<[string, ChatMessage[]]> = [
  ["prune_two_user_gate", [user("Run tool"), assistant("Running..."), tool("c1", 400000)]],
  ["prune_protect_40000_boundary", [user("Turn 1"), assistant("Running"), tool("c1", 160000), user("Middle turn"), user("Turn 2"), assistant("Done")]],
  ["prune_min_savings_20000_boundary_strict", [user("Turn 1"), assistant("Running"), tool("c_old", 80000), tool("c_recent", 160000), user("Middle turn"), user("Turn 2"), assistant("Done")]],
  ["prune_min_savings_above_boundary", [user("Turn 1"), assistant("Running"), tool("c_old", 80004), tool("c_recent", 160000), user("Middle turn"), user("Turn 2"), assistant("Done")]],
  ["prune_skill_exemption", [user("Turn 1"), assistant("Running"), tool("c_skill", 200000, "skill"), tool("c_bash", 160000), user("Middle turn"), user("Turn 2"), assistant("Done")]],
  ["prune_stop_at_previous_summary", [{ ...assistant("Prior summary"), summary: true }, tool("c_old", 200000), user("Turn 1"), assistant("Answer 1"), user("Turn 2"), assistant("Answer 2")]],
  ["prune_stop_at_already_compacted", [user("Turn 1"), assistant("Running"), { ...tool("c1", 0), compacted: true, content: "[Old tool result content cleared]" }, user("Middle turn"), user("Turn 2"), assistant("Done")]],
];
for (const [name, messages] of pruneFixtures) {
  const c = fixture(name);
  // Request-stage coverage uses the queued user-turn ingress, not a stale assistant tail.
  if (messages.at(-1)?.role === "assistant") messages.pop();
  c.input.messages = messages;
  c.input.usage.total_tokens = 100000;
  c.input.stage_ids = ["prune"];
  config = c.input.native_settings;
  nativeMessages = [];
  for (const m of messages) {
    if (m.role !== "tool") nativeMessages.push({ info: { role: m.role, summary: m.summary }, parts: [] });
    else {
      const owner = nativeMessages.at(-1)!;
      if (owner.info.role !== "assistant") throw new Error("Tool result has no native assistant owner");
      owner.parts.push({ type: "tool", tool: m.name!, callID: m.tool_call_id!, state: { status: "completed", output: String(m.content), time: m.compacted ? { compacted: 1 } : {} } });
      const previous = messages.slice(0, messages.indexOf(m)).findLast(p => p.role === "assistant")!;
      (previous.tool_calls ??= []).push({ id: m.tool_call_id!, type: "function", function: { name: m.name!, arguments: "{}" } });
    }
  }
  updates.length = 0;
  await prune({ sessionID: "oracle" });
  const edits = updates.map(part => {
    const index = messages.findIndex(m => m.tool_call_id === part.callID);
    if (index < 0) throw new Error("Executed tool update absent from input");
    return { index, message: { ...messages[index], content: "[Old tool result content cleared]" } };
  });
  c.expect = { selection: { targets: edits.map(e => e.index) }, edits };
  c.source.evidence = ["packages/opencode/src/session/compaction.ts:50-99", "packages/opencode/src/session/message-v2.ts:636-638"];
  c.capture.notes = "Executed pinned prune with native messages reconstructed from the recorded chat input. Tool names, call IDs, completed state and compaction markers are preserved; expected targets come only from updatePart calls. The budget cases contain two user turns after tool results, so the scan reaches the boundary.";
  cases.push(c);
}
const summaryFixtures: Array<[string, ChatMessage[], string, string]> = [
  ["summary_request_media_stripped_placeholders", [{ role: "user", content: [{ type: "text", text: "Look at this architecture diagram" }, { type: "image_url", image_url: { url: "data:image/png;base64,AA==" } }] }, assistant("I see components A and B.")], "threshold", "Summary of conversation so far"],
  ["placement_auto_continuation", [user("Refactor engine"), { ...assistant("Started refactoring."), tool_calls: [{ id: "c_newly_eligible", type: "function", function: { name: "bash", arguments: "{}" } }] }, tool("c_newly_eligible", 240004), user("Second turn"), assistant("Continued refactoring."), user("Third turn")], "threshold", "Refactoring engine in progress."],
  ["placement_manual_no_continuation", [user("Optimize database queries"), assistant("Indexing complete.")], "manual", "Database indexing complete."],
  ["placement_overflow_exclude_and_replay", [user("First task: inspect code"), assistant("Inspected files."), user("Second task: fix bug in auth.ts")], "overflow", "Summary of first task: code inspected."],
  ["placement_overflow_single_user_media_explanation", [user("Here is massive data dump...")], "overflow", "Summary of initial massive data attempt."],
  ["previous_summary_included_in_next_compaction", [{ ...assistant("Prior compaction summary: phase 1 complete."), summary: true }, user("Proceed with phase 2"), assistant("Phase 2 in progress.")], "threshold", "Summary of phase 1 and phase 2."],
];
const runner = await sourceRunner(opencode, plugin);
for (const [name, messages, reason, response] of summaryFixtures) {
  const c = fixture(name);
  c.input.messages = messages;
  c.input.reason = reason;
  c.input.usage.total_tokens = 150000;
  c.input.summary_responses = [response];
  await runner.summarize(c);
  cases.push(c);
}
const recovery = fixture("recovery_largest_first_masking", "oh-my-opencode@3.10.0");
recovery.input.messages = [user("Run tasks"), assistant("Running tools"), tool("call_a", 300000), assistant("More tools"), tool("call_b", 200000), assistant("Final tool"), tool("call_c", 50000)];
for (const [index, message] of recovery.input.messages.entries()) {
  if (message.role !== "tool") continue;
  const owner = recovery.input.messages[index - 1];
  if (owner.role !== "assistant") throw new Error("Missing recorded recovery tool owner");
  owner.tool_calls = [{ id: message.tool_call_id!, type: "function", function: { name: message.name!, arguments: "{}" } }];
}
recovery.input.usage.total_tokens = 1;
recovery.input.overflow_error = "prompt is too long: 210000 tokens > 160000 maximum";
recovery.input.reason = "overflow";
recovery.source.evidence = ["src/hooks/anthropic-context-window-limit-recovery/parser.ts:12-18,48-58,76-209", "src/hooks/anthropic-context-window-limit-recovery/executor.ts:46-63", "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts:26-196", "src/hooks/anthropic-context-window-limit-recovery/tool-result-storage-sdk.ts:61-93", "packages/opencode/src/session/message-v2.ts:636-638"];
await runner.recover(recovery);
cases.push(recovery);
const contributor = fixture("todo_preservation_summary_contributor", "oh-my-opencode@3.10.0");
contributor.input.messages = [user("Build features and track todos"), assistant("Working on tasks.")];
contributor.input.usage.total_tokens = 150000;
contributor.input.summary_responses = ["Summary including todo state and active files."];
await runner.summarize(contributor);
contributor.capture.notes += " This proves the continuity summary contributor, not the separate Todo.update persistence hook.";
cases.push(contributor);
const preemptive = fixture("preemptive_trigger_not_wired_in_3_10_0", "oh-my-opencode@3.10.0");
preemptive.input.messages = [user("Perform extensive operations"), assistant("Working through operations...")];
preemptive.input.usage.total_tokens = 160000;
preemptive.capture.kind = "source_derived";
preemptive.source.evidence = ["src/plugin/event.ts:137-159", "src/hooks/preemptive-compaction.ts:94-103,148-165", "packages/opencode/src/session/compaction.ts:30-48"];
preemptive.capture.notes = "The complete shipped dispatcher at plugin/event.ts:137-159 never calls preemptiveCompaction.event, which is the only population of its token cache. Therefore only the native threshold applies; 160000 is below 168000.";
preemptive.expect = { trigger: { fires: false, tokens: 160000, limit: 168000 } };
cases.push(preemptive);
if (cases.length !== 26 || new Set(cases.map(c => `${c.preset}/${c.case}`)).size !== 26) throw new Error("Case count or identity changed");
for (const c of cases) {
  if (!["threshold", "overflow", "manual"].includes(c.input.reason)) throw new Error("Invalid compaction reason");
  if (!c.source.evidence.length) throw new Error("Missing source evidence");
  const dir = join(process.cwd(), "tests/compaction/oracles", c.preset);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${c.case}.json`), JSON.stringify(c, null, 2) + "\n");
}
console.log(`Captured ${cases.length} cases: ${cases.filter(c => c.capture.kind === "executed").length} executed, ${cases.filter(c => c.capture.kind === "source_derived").length} source-derived.`);
