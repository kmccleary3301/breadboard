import path from "node:path"
import { existsSync } from "node:fs"
import { createRequire } from "node:module"
import { Worker, type WorkerOptions } from "node:worker_threads"
import { pathToFileURL } from "node:url"

const requireFromModule = createRequire(import.meta.url)

const WORKER_ENTRY = requireFromModule.resolve("@stream-mdx/worker")
const WORKER_PACKAGE_ROOT = path.dirname(path.dirname(WORKER_ENTRY))
const DEFAULT_WORKER_BUNDLE = pathToFileURL(path.join(WORKER_PACKAGE_ROOT, "dist", "hosted", "markdown-worker.js")).href

const normalizeWorkerBundleUrl = (value?: string | URL): string => {
  if (value instanceof URL) return value.href
  if (!value) return DEFAULT_WORKER_BUNDLE
  try {
    return new URL(value).href
  } catch {
    return pathToFileURL(path.resolve(value)).href
  }
}

export type MarkdownThreadOptions = WorkerOptions & {
  /** Override the worker bundle URL used by the bootstrap. */
  workerBundle?: string | URL
}

export const createMarkdownWorkerThread = (options: MarkdownThreadOptions = {}): Worker => {
  const { workerBundle, ...workerOptions } = options
  const bundleUrl = normalizeWorkerBundleUrl(workerBundle)
  const jsEntry = new URL("./worker-thread-entry.js", import.meta.url)
  const tsEntry = new URL("./worker-thread-entry.ts", import.meta.url)
  const runnerUrl = existsSync(jsEntry) ? jsEntry : tsEntry
  return new Worker(runnerUrl, {
    ...workerOptions,
    workerData: {
      ...(workerOptions.workerData ?? {}),
      bundleUrl,
    },
  })
}
