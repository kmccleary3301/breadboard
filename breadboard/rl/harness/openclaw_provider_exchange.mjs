import { pathToFileURL } from "node:url";
import { join } from "node:path";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import crypto from "node:crypto";

const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

export function createProviderExchange({ context, dist, protocol, toSourceHistory, getCompactionSnapshot, createReplaySessionManager }) {
let sourceProviderExchanges = new Set();
let sourceProviderHeaders = null;
let sourceIndexing = null;
let sourceMemoryRuntime = null;
let sourceTranscriptAccessor = null;
let sourceEnsureSessionEntry = null;
let sourceTranscriptUpdate = null;
let sourceSessionFileMarker = null;
let sourceMemoryScope = null;
let sourceMemoryManager = null;
const sourceMemoryTasks = new Set();
const sourceProviderQueue = [];
let sourceProviderWake = Promise.withResolvers();
let sourceProviderActive = null;
let sourceMemoryRows = [];
let sourceFetch = null;
let sourceUndici = null;
let sourceUndiciFetch = null;
function suspendProviderFetch(fetchImpl) {
  return (url, options) => {
    if (sourceProviderExchanges.has("embeddings")
      && String(url) === context.modelConfig.baseUrl.replace(/\/$/, "") + "/embeddings") {
      const response = Promise.withResolvers();
      sourceProviderQueue.push({
        request: {
          provider_exchange: "embeddings", method: options.method, url: String(url),
          header_names: [...new Headers(options.headers).keys()].sort(),
          body_b64: Buffer.from(options.body).toString("base64"),
        },
        response,
      });
      sourceProviderWake.resolve();
      return response.promise;
    }
    return fetchImpl(url, options);
  };
}

async function loadSourceIndexing() {
  if (sourceIndexing) return;
  const pinned = {
    "session-accessor-BxcCxteu.mjs": "f59cc36b974aa4ada62a700be36499c5e33e16676d8ba0b1a6bec3518e8ebbe5",
    "session-accessor.sqlite-entry-Ifvoy33r.mjs": "d9843739c1870bd770d1103bea38966f2248d899e7e5fbd444fa8d24e4b54e8f",
    "transcript-events-DKy5FIiI.mjs": "2848f8475d3bab64d570c60e076d4e4d583e4cb076eba2ebcacef6b9b298cf00",
    "legacy-sqlite-marker-COPKCuIN.mjs": "058e98b9e4549906a194bde53d262ad3a1504cbe1a9d0e57c68d4a5e999118e3",
    "memory-runtime-Divzyf_1.mjs": "10940eb790c878025d2817539df5b26b94d40be2e1805b59c965f07ee5cb3e44",
    "extensions/memory-core/manager-runtime.js": "a3d7355f2615621421f078b8870923ae89af6f2bf2d18a4aab648c1953261fa9",
    "undici-runtime-n7jdZvcB.mjs": "2fbd40d3f3c70da09b0aa2ade1247f0d24e5632ba323328dbbad009b898bace8",
  };
  for (const [name, digest] of Object.entries(pinned)) {
    if (sha256(await readFile(join(dist, name))) !== digest) {
      throw new Error(`pinned OpenClaw indexing digest mismatch for ${name}`);
    }
  }
  // Only the HTTP await is replaced. Stock owns provider selection, extraction,
  // chunking, caching, retries, vector validation and skip-on-failure.
  sourceFetch = globalThis.fetch;
  globalThis.fetch = suspendProviderFetch(sourceFetch);
  // Dispatcher-aware source calls bypass ambient global fetch
  // (runtime-fetch-CEXoSWzR.mjs:45-52). Suspend their HTTP await too.
  const { a: loadUndiciRuntimeDeps } = await import(pathToFileURL(join(dist, "undici-runtime-n7jdZvcB.mjs")));
  sourceUndici = loadUndiciRuntimeDeps();
  sourceUndiciFetch = sourceUndici.fetch;
  sourceUndici.fetch = suspendProviderFetch(sourceUndiciFetch);
  sourceTranscriptAccessor = await import(pathToFileURL(join(dist, "session-accessor-BxcCxteu.mjs")));
  sourceEnsureSessionEntry = (await import(pathToFileURL(join(dist, "session-accessor.sqlite-entry-Ifvoy33r.mjs")))).D;
  sourceTranscriptUpdate = (await import(pathToFileURL(join(dist, "transcript-events-DKy5FIiI.mjs")))).n;
  sourceSessionFileMarker = (await import(pathToFileURL(join(dist, "legacy-sqlite-marker-COPKCuIN.mjs")))).t;
  sourceMemoryRuntime = await import(pathToFileURL(join(dist, "memory-runtime-Divzyf_1.mjs")));
  const { MemoryIndexManager } = await import(pathToFileURL(join(dist, "extensions/memory-core/manager-runtime.js")));
  const stockSync = MemoryIndexManager.prototype.sync;
  MemoryIndexManager.prototype.sync = function(params) {
    const work = stockSync.call(this, params);
    observeSourceMemoryWork(work);
    return work;
  };
  sourceIndexing = await import("openclaw:pinned-compaction-indexing");
}

function observeSourceMemoryWork(work) {
  sourceMemoryTasks.add(work);
  // Observers do not change the promise returned to stock's own error handler.
  work.then(
    () => sourceMemoryTasks.delete(work),
    () => sourceMemoryTasks.delete(work),
  );
}

async function mirrorSourceTranscript(manager) {
  sourceMemoryManager = manager;
  sourceMemoryScope ??= await sourceTranscriptAccessor.N({
    agentId: "main", sessionId: context.sourceSessionIdentity, sessionKey: context.sourceSessionKey,
  }, context.sourceConfig);
  if (!sourceEnsureSessionEntry(sourceMemoryScope, {
    sessionId: context.sourceSessionIdentity, updatedAt: context.sourceInitialTimestamp,
  })) throw new Error("source indexing session identity could not be persisted");
  if (!sourceTranscriptAccessor.tt(sourceMemoryScope, manager.getPersistedFileEntries())) {
    throw new Error("source indexing transcript mirror was refused");
  }
  const db = new DatabaseSync(sourceTranscriptAccessor.j(sourceMemoryScope), { readOnly: true });
  try {
    sourceMemoryRows = db.prepare(
      "SELECT event_json FROM transcript_events WHERE session_id = ? ORDER BY seq",
    ).all(context.sourceSessionIdentity).map((row) => JSON.parse(row.event_json));
  } finally { db.close(); }
}

async function startSourceIndexing(manager) {
  await loadSourceIndexing();
  await mirrorSourceTranscript(manager);
  const params = {
    config: context.sourceConfig, agentId: "main", sessionKey: context.sourceSessionKey,
    sessionId: context.sourceSessionIdentity, sessionFile: sourceSessionFileMarker(sourceMemoryScope),
  };
  sourceTranscriptUpdate(params);
  // This is the stock async hook's work, awaited only by the replay checkpoint.
  // compact-BvznzvKp.mjs:235-267 retains stock's catch/log/skip semantics.
  observeSourceMemoryWork(sourceIndexing.runPostCompactionSessionMemorySync(params));
}

async function mirrorSourceHistory(messages) {
  const admitted = messages[0]?.role === "system" ? messages.slice(1) : messages;
  const raw = toSourceHistory(admitted);
  const timestamp = new Date(raw.reduce((last, msg) => Math.max(last, msg.timestamp ?? 0), context.sourceInitialTimestamp) + 1).toISOString();
  const manager = createReplaySessionManager(getCompactionSnapshot().entries, timestamp);
  for (const msg of raw.slice(getCompactionSnapshot().messages.length)) manager.appendMessage(msg);
  await mirrorSourceTranscript(manager);
  sourceTranscriptUpdate({
    agentId: "main", sessionId: context.sourceSessionIdentity, sessionKey: context.sourceSessionKey,
    sessionFile: sourceSessionFileMarker(sourceMemoryScope),
  });
}

async function drainSourceProviderExchanges(response) {
  if (response !== undefined) {
    if (!sourceProviderActive) throw new Error("no source provider exchange is suspended");
    if (response.error) {
      const error = new Error(response.error.message);
      error.name = response.error.type;
      sourceProviderActive.response.reject(error);
    } else {
      const body = Buffer.from(response.body_b64, "base64");
      sourceProviderActive.response.resolve(new Response(body, {
        status: response.status_code, headers: response.headers,
      }));
    }
    sourceProviderActive = null;
  }
  for (;;) {
    if (sourceProviderQueue.length) {
      if (sourceProviderActive) throw new Error("source provider response was not consumed");
      sourceProviderActive = sourceProviderQueue.shift();
      return { schema_version: protocol, kind: "provider_exchange", request: sourceProviderActive.request };
    }
    if (!sourceMemoryTasks.size) {
      return { schema_version: protocol, kind: "provider_exchanges_drained", transcript_rows: sourceMemoryRows };
    }
    sourceProviderWake = Promise.withResolvers();
    await Promise.race([
      sourceProviderWake.promise, Promise.allSettled([...sourceMemoryTasks]),
    ]);
  }
}
function configure(message) {
    sourceProviderExchanges = new Set(message.provider_exchanges ?? []);
    sourceProviderHeaders = message.provider_configuration?.headers ?? null;
    if (sourceProviderExchanges.size
      && (!sourceProviderHeaders || typeof sourceProviderHeaders !== "object")) {
      throw new Error("private source provider configuration is required");
    }

}
async function close() {
    if (sourceMemoryRuntime) await sourceMemoryRuntime.i(context.sourceConfig);
    if (sourceFetch) globalThis.fetch = sourceFetch;
    if (sourceUndiciFetch) sourceUndici.fetch = sourceUndiciFetch;

}
return { configure, loadSourceIndexing, mirrorSourceTranscript, startSourceIndexing, mirrorSourceHistory, drainSourceProviderExchanges, close,
    get exchanges() { return sourceProviderExchanges; },
    get headers() { return sourceProviderHeaders; },
    get hasMirroredTranscript() { return Boolean(sourceMemoryManager); },
  };
}
