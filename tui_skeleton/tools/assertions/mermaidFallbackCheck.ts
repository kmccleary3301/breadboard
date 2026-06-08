import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

export const evaluateMermaidFallback = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshot = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const haystacks: string[] = [snapshot]
  for (const name of ["pty_plain.txt", "pty_raw.ansi", "ttydoc.txt"]) {
    try {
      haystacks.push(await fs.readFile(path.join(caseDir, name), "utf8"))
    } catch {
      // ignore
    }
  }
  const combined = haystacks.join("\n\n")
  if (!combined.includes("graph TD") || !combined.includes("A-->B")) {
    anomalies.push({ id: "mermaid-body-missing", message: "Expected mermaid body lines to remain visible in fallback rendering." })
  }
  for (const needle of [
    "Language `mermaid` not found",
    "The above error occurred in the <ReplViewInner> component",
    "ShikiError",
  ]) {
    if (combined.includes(needle)) {
      anomalies.push({ id: "mermaid-crash-signature", message: `Detected mermaid crash signature: ${needle}` })
      break
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMermaidFallback(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
