#!/usr/bin/env node
/**
 * OpenClaw 2026.9.4 tool boundary. The model loop stays in BreadBoard;
 * this process owns only the pinned source tool factories and their scope.
 */
import readline from "node:readline";
import { pathToFileURL } from "node:url";
import { join } from "node:path";
import crypto from "node:crypto";

const dist = process.env.OPENCLAW_DIST || "/opt/openclaw/dist";
const core = await import(pathToFileURL(join(dist, "core-coding-tools-DoP9tAh3.mjs")));
const bootstrap = await import(pathToFileURL(join(dist, "bootstrap-DYYMCrXY.mjs")));
const workspaceModule = await import(pathToFileURL(join(dist, "workspace-YW5Pl2cf.mjs")));
const { t: createCoreCodingTools } = core;
const { n: buildBootstrapContextFiles } = bootstrap;
const { _: loadWorkspaceBootstrapFiles } = workspaceModule;

let workspace = null;
let scopeKey = "openclaw:e4";
let tools = new Map();
const pending = new Map();

function reply(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function makeTools() {
  const built = createCoreCodingTools({
    codingRoot: workspace,
    containmentRoot: workspace,
    includeBaseCodingTools: true,
    includeShellTools: true,
    readOnly: false,
    workspaceOnly: true,
    execDefaults: {
      host: "gateway",
      security: "full",
      ask: "off",
      allowBackground: true,
      scopeKey,
      cwd: workspace,
    },
    processDefaults: { scopeKey },
  });
  tools = new Map(built.map((tool) => [tool.name, tool]));
}

async function bootstrapContext() {
  const files = await loadWorkspaceBootstrapFiles(workspace);
  return buildBootstrapContextFiles(files, { maxChars: 20000, totalMaxChars: 60000 });
}

async function handle(message) {
  const op = message?.op;
  if (op === "init") {
    if (typeof message.workspace !== "string" || !message.workspace) throw new Error("workspace is required");
    workspace = message.workspace;
    scopeKey = typeof message.scopeKey === "string" && message.scopeKey ? message.scopeKey : scopeKey;
    makeTools();
    return { ok: true, protocol: "bb.openclaw.tool-worker.jsonl.v1", tools: [...tools.keys()] };
  }
  if (!workspace) throw new Error("worker is not initialized");
  if (op === "bootstrap") return { ok: true, context: await bootstrapContext() };
  if (op === "ack") {
    const token = String(message.token || "");
    if (!pending.delete(token)) throw new Error(`unknown acknowledgement ${token}`);
    return { ok: true, acknowledged: token };
  }
  if (op !== "tool") throw new Error(`unknown worker operation ${op}`);
  const name = String(message.name || "");
  const tool = tools.get(name);
  if (!tool) throw new Error(`undeclared tool ${name}`);
  const result = await tool.execute(String(message.tool_call_id || crypto.randomUUID()), message.arguments || {});
  const token = crypto.randomUUID();
  pending.set(token, { name, createdAt: Date.now() });
  return { ok: true, tool_call_id: message.tool_call_id, result, acknowledgement: { token, required: true } };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  try {
    reply(await handle(JSON.parse(line)));
  } catch (error) {
    reply({ ok: false, error: error instanceof Error ? error.message : String(error) });
  }
}
