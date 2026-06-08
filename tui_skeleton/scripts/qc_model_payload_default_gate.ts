import { readFileSync } from "node:fs"

const [recordPath, snapshotPath] = process.argv.slice(2)
if (!recordPath || !snapshotPath) {
  console.error("Usage: qc_model_payload_default_gate.ts <record-jsonl> <snapshot.txt>")
  process.exit(2)
}

const failures: string[] = []
const records = readFileSync(recordPath, "utf8")
  .trim()
  .split(/\r?\n/)
  .filter(Boolean)
  .map((line) => JSON.parse(line))
const create = records.find((record: any) => record?.kind === "create_session")
if (!create) {
  failures.push("missing create_session record")
} else {
  const body = create.body ?? {}
  const overrides = body.overrides ?? {}
  const metadata = body.metadata ?? {}
  const overrideModel = overrides["providers.default_model"]
  const metadataModel = metadata.model
  if (overrideModel !== "openai/gpt-5.4-mini") {
    failures.push(`providers.default_model override was ${String(overrideModel)}; expected openai/gpt-5.4-mini`)
  }
  if (metadataModel !== "openai/gpt-5.4-mini") {
    failures.push(`metadata.model was ${String(metadataModel)}; expected openai/gpt-5.4-mini`)
  }
  const serialized = JSON.stringify(body)
  if (serialized.includes("gpt-5.1-codex-mini")) {
    failures.push("create_session payload contains stale gpt-5.1-codex-mini")
  }
}

const snapshot = readFileSync(snapshotPath, "utf8")
if (!snapshot.includes("gpt-5.4-mini") && !snapshot.includes("openai/gpt-5.4-mini")) {
  failures.push("startup snapshot does not show gpt-5.4-mini")
}
if (snapshot.includes("gpt-5.1-codex-mini")) {
  failures.push("startup snapshot contains stale gpt-5.1-codex-mini")
}
if (!snapshot.includes("enter send")) {
  failures.push("startup snapshot did not reach ready composer/footer")
}

if (failures.length > 0) {
  console.error(`[qc] model payload default gate failed (${failures.length})`)
  for (const failure of failures) console.error(`- ${failure}`)
  process.exit(1)
}

console.log("[qc] model payload default gate passed")
