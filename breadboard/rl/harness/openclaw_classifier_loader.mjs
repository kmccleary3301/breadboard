import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { join } from "node:path";
import { readFile } from "node:fs/promises";
import { isMainThread } from "node:worker_threads";
import { createHash } from "node:crypto";

// Source: OpenClaw 2026.9.4 dist/agent-exec-BAuhpelg.mjs. The loader
// exposes two private pinned declarations without copying their implementation.
const filename = "agent-exec-BAuhpelg.mjs";
const expected = "2e39dbc961337936860849aaaed6a26b734d0c20648093f2bc51a46ebfe9526d";
const appended = "\nexport { classifyAgentExecResult, exitCodeForEnvelope };\n";
const dist = process.env.OPENCLAW_DIST || "/opt/openclaw/dist";
const sourceUrl = pathToFileURL(join(dist, filename)).href;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "openclaw:pinned-agent-exec") {
    return { url: sourceUrl, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url !== sourceUrl) return nextLoad(url, context);
  const bytes = await readFile(new URL(url));
  if (createHash("sha256").update(bytes).digest("hex") !== expected) {
    throw new Error(`pinned OpenClaw dist digest mismatch for ${filename}`);
  }
  return { format: "module", shortCircuit: true, source: Buffer.concat([bytes, Buffer.from(appended)]) };
}

// Node's --import registers the hooks before loading the worker's static import.
// Node's --import runs on the main thread; the registered hook module runs in
// the loader thread and does not register itself recursively.
if (isMainThread) register(new URL(import.meta.url), import.meta.url);
