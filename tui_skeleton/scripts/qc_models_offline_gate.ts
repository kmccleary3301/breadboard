import { promises as fs } from "node:fs"

const target = process.argv[2] ?? "scripts/_tmp_qc_models_offline.txt"

const fail = (message: string): never => {
  throw new Error(`[qc:models-offline] ${message}`)
}

const run = async () => {
  const raw = await fs.readFile(target, "utf8")
  const hasSelectModel = raw.includes("Select model")
  const stillLoading = raw.includes("Loading model catalog")
  const hasCatalogFailure =
    raw.includes("Failed to load model catalog") || raw.includes("Model catalog unavailable")
  const hasFatalError = /\b(TypeError|Unhandled|Exception|Traceback)\b/i.test(raw)

  if (!hasSelectModel && !hasCatalogFailure) {
    fail('missing resolved model-picker state (expected "Select model" or explicit catalog failure)')
  }
  if (stillLoading && !hasSelectModel && !hasCatalogFailure) fail("menu remained stuck in loading state")
  if (hasFatalError) fail("unexpected fatal error marker in snapshot")

  console.log("[qc:models-offline] passed")
}

run().catch((error) => {
  console.error((error as Error).message)
  process.exitCode = 1
})

