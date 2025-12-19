import { promises as fs } from "node:fs"

export interface LayoutAnomaly {
  readonly id: string
  readonly message: string
  readonly line?: number
}

const findFirstNonEmptyRow = (lines: string[]): number => {
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].trim().length > 0) {
      return i
    }
  }
  return -1
}

const containsCaseInsensitive = (value: string, needle: string): boolean =>
  value.toLowerCase().includes(needle.toLowerCase())

export const runLayoutAssertionsOnLines = (lines: string[]): LayoutAnomaly[] => {
  const anomalies: LayoutAnomaly[] = []
  if (lines.length === 0) {
    anomalies.push({ id: "grid-empty", message: "Grid snapshot is empty" })
    return anomalies
  }

  const firstRowIndex = findFirstNonEmptyRow(lines)
  if (firstRowIndex === -1) {
    anomalies.push({ id: "header-missing", message: "No non-empty rows found in grid" })
  }

  const headerMatches = lines
    .map((line, index) => ({ line, index }))
    .filter((entry) => containsCaseInsensitive(entry.line, "kyle code"))

  if (headerMatches.length > 1) {
    anomalies.push({ id: "header-duplicate", message: "KyleCode banner appears multiple times" })
  }

  const composerCandidate = lines.findIndex((line) => {
    const trimmed = line.trim()
    return trimmed.startsWith("›") || containsCaseInsensitive(trimmed, "type your request")
  })
  if (composerCandidate === -1) {
    anomalies.push({ id: "composer-missing", message: "Could not find composer prompt" })
  }

  const shortcutsMatch = lines.filter((line) => containsCaseInsensitive(line, "Slash commands"))
  if (shortcutsMatch.length > 1) {
    anomalies.push({ id: "shortcuts-duplicate", message: "Slash commands helper repeats multiple times" })
  }

  const guardrailMatches = lines
    .map((line, index) => ({ line, index }))
    .filter((entry) => containsCaseInsensitive(entry.line, "Guardrail"))
  if (guardrailMatches.length > 1) {
    anomalies.push({ id: "guardrail-duplicate", message: "Guardrail banner appears multiple times" })
  }
  if (guardrailMatches.length > 0 && headerMatches.length > 0) {
    const first = guardrailMatches[0]
    if (first.index - headerMatches[0].index > 20) {
      anomalies.push({ id: "guardrail-offset", message: "Guardrail banner too far from header", line: first.index })
    }
  }

  const toolSections = lines.filter((line) => line.trimStart().startsWith("[tool]"))
  if (toolSections.length > 0) {
    let lastKind: string | null = null
    for (const line of toolSections) {
      const match = line.match(/\[tool]\s*\[([A-Z]+)\]/i)
      const kind = match ? match[1] : null
      if (lastKind && kind && lastKind === kind && !line.includes("…")) {
        anomalies.push({ id: "tool-duplicate", message: `Consecutive tool rows share the same kind (${kind}).` })
        break
      }
      lastKind = kind
    }
  }

  const diffSummaries = lines.filter((line) => line.includes("Δ +") && line.includes("lines hidden"))
  for (const summary of diffSummaries) {
    if (!summary.includes("press e")) {
      anomalies.push({ id: "diff-summary-missing", message: "Collapsed diff summary missing instructions" })
    }
  }

  const modelHeaderMatches = lines
    .map((line, index) => ({ line, index }))
    .filter((entry) => containsCaseInsensitive(entry.line, "Provider · Model"))
  if (modelHeaderMatches.length > 1) {
    anomalies.push({ id: "model-header-duplicate", message: "Model picker header repeats", line: modelHeaderMatches[1].index })
  }
  for (const match of modelHeaderMatches) {
    if (containsCaseInsensitive(match.line, "Context") || match.line.includes("Conte")) {
      const providerIndex = match.line.toLowerCase().indexOf("provider")
      const contextIndex = match.line.toLowerCase().indexOf("context".toLowerCase())
      if (contextIndex !== -1 && contextIndex < providerIndex) {
        anomalies.push({ id: "model-header-order", message: "Context column appears before Provider column", line: match.index })
      }
    }
  }

  return anomalies
}

export const runLayoutAssertions = async (gridPath: string): Promise<LayoutAnomaly[]> => {
  const contents = await fs.readFile(gridPath, "utf8")
  const lines = contents.split(/\r?\n/)
  return runLayoutAssertionsOnLines(lines)
}
