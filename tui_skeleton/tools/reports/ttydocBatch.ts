import { promises as fs } from "node:fs"
import path from "node:path"

export const buildBatchTtyDoc = async (batchDir: string) => {
  const manifestPath = path.join(batchDir, "manifest.json")
  let manifest: any
  try {
    manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"))
  } catch {
    return null
  }
  const cases: Array<any> = Array.isArray(manifest.cases) ? manifest.cases : []
  const reportsDir = path.join(batchDir, "reports")
  await fs.mkdir(reportsDir, { recursive: true })
  const reportPath = path.join(reportsDir, "ttydoc_batch.txt")
  const lines: string[] = []
  lines.push("# BreadBoard Batch TTY Report")
  lines.push("")
  lines.push(`Batch dir: ${path.basename(batchDir)}`)
  if (manifest.guardrailMetrics) {
    lines.push("")
    lines.push("## Guardrail Metrics Summary")
    lines.push(JSON.stringify(manifest.guardrailMetrics, null, 2))
  }
  if (manifest.keyFuzz) {
    lines.push("")
    lines.push("## Key-Fuzz Summary")
    lines.push(JSON.stringify(manifest.keyFuzz.summary ?? {}, null, 2))
  }
  lines.push("")
  lines.push("## Cases")
  for (const entry of cases) {
    const id = entry.id
    const chaos = entry.chaos ?? null
    const ttyDocRel = entry.ttyDocPath ?? "ttydoc.txt"
    lines.push("")
    lines.push(`### ${id}`)
    lines.push(`- ttydoc: \`${ttyDocRel}\``)
    if (chaos) {
      lines.push(`- chaos: ${JSON.stringify(chaos)}`)
    } else {
      lines.push("- chaos: none")
    }
  }
  lines.push("")
  await fs.writeFile(reportPath, lines.join("\n"), "utf8")
  return reportPath
}

