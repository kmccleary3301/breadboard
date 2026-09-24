import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { join } from "node:path";
import { readFile } from "node:fs/promises";
import { isMainThread } from "node:worker_threads";
import { createHash } from "node:crypto";

// Source: OpenClaw 2026.9.4 dist/agent-exec-BAuhpelg.mjs. The loader
// exposes pinned private declarations without copying their implementation.
const modules = new Map([
  ["openclaw:pinned-agent-exec", {
    filename: "agent-exec-BAuhpelg.mjs",
    expected: "2e39dbc961337936860849aaaed6a26b734d0c20648093f2bc51a46ebfe9526d",
    appended: "\nexport { classifyAgentExecResult, errorEnvelope, exitCodeForEnvelope, formatErrorMessage };\n",
  }],
  ["openclaw:pinned-attempt-prompt", {
    filename: "builtin-openclaw-B-H-7lKk.mjs",
    expected: "0a8c813e535c92d03f69bc58381518ba0e6ac6e46f3adda54138c5f668340ea8",
    appended: "\nexport { buildAttemptSystemPrompt };\n",
  }],
]);
const dist = process.env.OPENCLAW_DIST || join(process.cwd(), "dist");
const byUrl = new Map([...modules].map(([specifier, module]) => [
  pathToFileURL(join(dist, module.filename)).href, module,
]));

export async function resolve(specifier, context, nextResolve) {
  const module = modules.get(specifier);
  if (module) return { url: pathToFileURL(join(dist, module.filename)).href, shortCircuit: true };
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  const module = byUrl.get(url);
  if (!module) return nextLoad(url, context);
  const bytes = await readFile(new URL(url));
  if (createHash("sha256").update(bytes).digest("hex") !== module.expected) {
    throw new Error(`pinned OpenClaw dist digest mismatch for ${module.filename}`);
  }
  return { format: "module", shortCircuit: true, source: Buffer.concat([bytes, Buffer.from(module.appended)]) };
}

// Node's --import registers the hooks before loading the worker's static import.
// Node's --import runs on the main thread; the registered hook module runs in
// the loader thread and does not register itself recursively.
if (isMainThread) register(new URL(import.meta.url), import.meta.url);
